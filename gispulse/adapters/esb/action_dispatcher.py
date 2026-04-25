"""Action dispatcher for ESB trigger events.

Replaces the basic 1-trigger → 1-rule dispatch with a multi-action
system supporting 8 action types (notify, set_field, update_aggregate,
run_job, run_graph, webhook, enqueue, log_event).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

from gispulse.core.dispatcher import BaseDispatcher, TriggerContext
from core.logging import get_logger
from core.models import ActionDef, ActionType, EvalResult, Trigger
from core.sql_safety import validate_identifier as _validate_identifier

log = get_logger(__name__)





class ActionDispatcher(BaseDispatcher):
    """Dispatch actions after predicate match.

    Each action type maps to a handler method.  External integrations
    (job runner, graph executor, webhook client) are injected as callables.

    Args:
        job_runner:     ``(rule_id, table, row_id) -> None``
        graph_runner:   ``(graph_id, params) -> None``
        event_hub:      Object with ``broadcast(event_type, data)`` method.
        sql_executor:   ``(sql, params) -> Any`` for set_field/aggregate.
        webhook_client: ``(url, payload) -> None`` for outbound HTTP.
    """

    def __init__(
        self,
        job_runner: Callable | None = None,
        graph_runner: Callable | None = None,
        event_hub: Any | None = None,
        sql_executor: Callable | None = None,
        webhook_client: Callable | None = None,
    ) -> None:
        self._job_runner = job_runner
        self._graph_runner = graph_runner
        self._event_hub = event_hub
        self._sql_executor = sql_executor
        self._webhook_client = webhook_client

    def dispatch(self, action: ActionDef, context: TriggerContext) -> None:
        """Route an action to the appropriate handler."""
        handler = self._handlers.get(action.action_type)
        if handler is None:
            log.warning("action_unknown_type", action_type=action.action_type)
            return
        try:
            handler(self, action, context)
            log.info(
                "action_dispatched",
                action_type=action.action_type.value,
                trigger_id=str(context.trigger.id),
                table=context.table,
            )
        except Exception as exc:
            log.error(
                "action_dispatch_failed",
                action_type=action.action_type.value,
                trigger_id=str(context.trigger.id),
                error=str(exc),
            )

    def dispatch_all(self, actions: list[ActionDef], context: TriggerContext) -> int:
        """Dispatch multiple actions. Returns count of successful dispatches."""
        count = 0
        for action in actions:
            try:
                self.dispatch(action, context)
                count += 1
            except Exception as exc:
                log.error(
                    "action_dispatch_all_failed",
                    action_type=action.action_type.value if hasattr(action.action_type, "value") else str(action.action_type),
                    trigger_id=str(context.trigger.id),
                    error=str(exc),
                )
        return count

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _notify(self, action: ActionDef, ctx: TriggerContext) -> None:
        channel = action.config.get("channel", "gispulse_events")
        _validate_identifier(channel, "notify_channel")
        payload = self._render_payload(action, ctx)
        if self._event_hub:
            self._event_hub.broadcast(f"trigger:{channel}", {
                "trigger_id": str(ctx.trigger.id),
                "transition": ctx.eval_result.transition.value if ctx.eval_result.transition else None,
                "table": ctx.table,
                "row_id": ctx.row_id,
                "payload": payload,
            })
        if self._sql_executor:
            self._sql_executor(
                "SELECT pg_notify(%s, %s)",
                [channel, json.dumps(payload)],
            )

    def _set_field(self, action: ActionDef, ctx: TriggerContext) -> None:
        target_field = action.config.get("field", "")
        value = action.config.get("value")
        if not target_field or not self._sql_executor:
            return
        _validate_identifier(ctx.table, "table")
        _validate_identifier(target_field, "field")
        self._sql_executor(
            f'UPDATE "{ctx.table}" SET "{target_field}" = %s WHERE id = %s',
            [value, ctx.row_id],
        )

    def _update_aggregate(self, action: ActionDef, ctx: TriggerContext) -> None:
        target_table = action.config.get("target_table", "")
        target_field = action.config.get("target_field", "")
        agg_func = action.config.get("aggregate", "COUNT")
        source_table = ctx.table
        if not all([target_table, target_field, self._sql_executor]):
            return
        _validate_identifier(target_table, "target_table")
        _validate_identifier(target_field, "target_field")
        _validate_identifier(source_table, "source_table")
        if agg_func.upper() not in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
            raise ValueError(f"Unsafe aggregate function: {agg_func!r}")
        self._sql_executor(
            f"""
            UPDATE "{target_table}" t SET "{target_field}" = (
                SELECT {agg_func}(*) FROM "{source_table}" s
                WHERE ST_Contains(t.geom, s.geom)
            ) WHERE ST_Intersects(t.geom, (
                SELECT geom FROM "{source_table}" WHERE id = %s
            ))
            """,
            [ctx.row_id],
        )

    def _run_job(self, action: ActionDef, ctx: TriggerContext) -> None:
        rule_id = action.config.get("rule_id")
        if rule_id and self._job_runner:
            self._job_runner(UUID(str(rule_id)), ctx.table, ctx.row_id)

    def _run_graph(self, action: ActionDef, ctx: TriggerContext) -> None:
        graph_id = action.config.get("graph_id", "")
        graph_params = action.config.get("params", {})
        graph_params["_trigger_table"] = ctx.table
        graph_params["_trigger_row_id"] = ctx.row_id
        if graph_id and self._graph_runner:
            self._graph_runner(graph_id, graph_params)

    def _webhook(self, action: ActionDef, ctx: TriggerContext) -> None:
        url = action.config.get("url", "")
        if not url:
            return
        payload = self._render_payload(action, ctx)
        if self._webhook_client:
            self._webhook_client(url, payload)

    def _enqueue(self, action: ActionDef, ctx: TriggerContext) -> None:
        if not self._sql_executor:
            return
        msg = json.dumps({
            "trigger_id": str(ctx.trigger.id),
            "table": ctx.table,
            "row_id": ctx.row_id,
            "action_config": action.config,
            "timestamp": ctx.timestamp.isoformat(),
        })
        self._sql_executor(
            "INSERT INTO bus_messages (payload, status, created_at) VALUES (%s, 'pending', NOW())",
            [msg],
        )

    def _log_event(self, action: ActionDef, ctx: TriggerContext) -> None:
        log.info(
            "trigger_event_logged",
            trigger_id=str(ctx.trigger.id),
            table=ctx.table,
            operation=ctx.operation,
            row_id=ctx.row_id,
            transition=ctx.eval_result.transition.value if ctx.eval_result.transition else None,
        )
        if self._sql_executor:
            self._sql_executor(
                """INSERT INTO db_audit.status_history
                   (schema_name, table_name, row_id, old_status, new_status, changed_at)
                   VALUES (%s, %s, %s::uuid, %s, %s, NOW())""",
                [
                    "esb",
                    ctx.table,
                    ctx.row_id or "00000000-0000-0000-0000-000000000000",
                    ctx.operation,
                    json.dumps({"transition": ctx.eval_result.transition.value if ctx.eval_result.transition else None}),
                ],
            )

    def _run_sql(self, action: ActionDef, ctx: TriggerContext) -> None:
        """Execute a SQL expression or trigger operations via OperationExecutor.

        If ``action.config`` contains ``"operations"`` (a list of Forge-style
        spatial operation dicts), delegates to :class:`OperationExecutor` for
        BEFORE/AFTER execution. Otherwise, runs a raw SQL expression.
        """
        operations = action.config.get("operations")
        if operations and self._sql_executor:
            # Forge-style spatial operations — delegate to OperationExecutor
            try:
                from rules.operation_executor import OperationExecutor

                executor = OperationExecutor(self._sql_executor)

                geom_wkt = ctx.new_attrs.get("geom") or ctx.new_attrs.get("geometry")
                srid = int(action.config.get("srid", 4326))

                # BEFORE: modify the row data inline
                modified = executor.execute_before(
                    operations, dict(ctx.new_attrs), geom_wkt=geom_wkt, srid=srid,
                )
                # Apply modified fields back via SQL
                for key, val in modified.items():
                    if key not in ctx.new_attrs or ctx.new_attrs[key] != val:
                        if ctx.table and ctx.row_id:
                            _validate_identifier(ctx.table, "table")
                            _validate_identifier(key, "field")
                            self._sql_executor(
                                f'UPDATE "{ctx.table}" SET "{key}" = %s WHERE id = %s',
                                [val, ctx.row_id],
                            )

                # AFTER: propagate to distant tables
                executor.execute_after(
                    operations, modified, geom_wkt=geom_wkt, srid=srid,
                )
                log.info(
                    "operations_executed",
                    trigger_id=str(ctx.trigger.id),
                    table=ctx.table,
                    before_ops=len([o for o in operations if o.get("phase") == "before"]),
                    after_ops=len([o for o in operations if o.get("phase") == "after"]),
                )
            except Exception as exc:
                log.error("operations_execution_failed", trigger_id=str(ctx.trigger.id), error=str(exc))
            return

        # Simple SQL expression mode
        expression = action.config.get("expression", "")
        if not expression or not self._sql_executor:
            return
        from core.sql_safety import validate_expression as _validate_expression
        _validate_expression(expression)
        self._sql_executor(expression, [])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------



    # ------------------------------------------------------------------
    # Handler registry
    # ------------------------------------------------------------------

    _handlers: dict[ActionType, Callable] = {
        ActionType.NOTIFY:           _notify,
        ActionType.SET_FIELD:        _set_field,
        ActionType.UPDATE_AGGREGATE: _update_aggregate,
        ActionType.RUN_JOB:          _run_job,
        ActionType.RUN_GRAPH:        _run_graph,
        ActionType.WEBHOOK:          _webhook,
        ActionType.ENQUEUE:          _enqueue,
        ActionType.LOG_EVENT:        _log_event,
        ActionType.RUN_SQL:          _run_sql,
    }

    @classmethod
    def register_action_handler(
        cls,
        action_type: ActionType,
        handler: Callable,
        *,
        override: bool = False,
    ) -> None:
        """Register a custom action handler.

        Args:
            action_type: The :class:`ActionType` to handle.
            handler:     Callable with signature
                         ``(self, action: ActionDef, ctx: TriggerContext) -> None``.
            override:    If *True*, allow replacing an existing handler.

        Raises:
            ValueError: If *action_type* is already registered and *override*
                        is *False*.
        """
        if action_type in cls._handlers and not override:
            raise ValueError(
                f"Action handler for {action_type!r} already registered. "
                f"Pass override=True to replace."
            )
        cls._handlers[action_type] = handler
        log.info("action_handler_registered", action_type=action_type.value)

    @classmethod
    def unregister_action_handler(cls, action_type: ActionType) -> None:
        """Remove a previously registered action handler.

        Raises:
            KeyError: If no handler is registered for *action_type*.
        """
        if action_type not in cls._handlers:
            raise KeyError(f"No handler registered for {action_type!r}")
        del cls._handlers[action_type]
        log.info("action_handler_unregistered", action_type=action_type.value)
