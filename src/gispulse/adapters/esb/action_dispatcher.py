"""Action dispatcher for ESB trigger events.

Replaces the basic 1-trigger → 1-rule dispatch with a multi-action
system supporting 8 action types (notify, set_field, update_aggregate,
run_job, run_graph, webhook, enqueue, log_event).
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable
from uuid import UUID

from gispulse.core.dispatcher import BaseDispatcher, TriggerContext
from gispulse.core.logging import get_logger
from gispulse.core.models import ActionDef, ActionType
from gispulse.core.sql_safety import validate_identifier as _validate_strict_identifier
from gispulse.core.sql_safety import validate_layer_name as _validate_layer_name

# B-05 (v1.5.3): table / column names that originate from a local GPKG
# (QGIS desktop) flow through :func:`validate_layer_name` to accept
# spaces, accents, dashes. ``_validate_strict_identifier`` is reserved
# for fields that must match a SQL keyword shape (PG NOTIFY channels,
# aggregate function names, ...).
_validate_identifier = _validate_layer_name

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
        # v1.6.0 (#123) — per-(table) set of columns we've already
        # confirmed exist for tag_field auto-create. The lock guards
        # the cache, not the schema migration itself (SQLite handles
        # ALTER TABLE serialisation natively).
        self._tag_field_known_columns: dict[str, set[str]] = {}
        self._tag_field_lock = threading.Lock()

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
        _validate_strict_identifier(channel, "notify_channel")
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
        # B-02 (#103) origin-tagging M1: tag the row with
        # ``trigger:<id>`` so the AFTER UPDATE WHEN clause skips the
        # write-back and we don't loop. A second UPDATE clears the
        # sentinel back to NULL — that one is also suppressed by the
        # WHEN clause (NEW=NULL while OLD LIKE 'trigger:%') so a
        # subsequent QGIS edit fires the trigger normally.
        trigger_marker = (
            f"trigger:{ctx.trigger.id}"
            if getattr(ctx, "trigger", None) is not None
            and getattr(ctx.trigger, "id", None) is not None
            else None
        )
        if trigger_marker is not None:
            self._sql_executor(
                f'UPDATE "{ctx.table}" SET "{target_field}" = %s, '
                f'"_gispulse_origin" = %s WHERE id = %s',
                [value, trigger_marker, ctx.row_id],
            )
            self._sql_executor(
                f'UPDATE "{ctx.table}" SET "_gispulse_origin" = NULL '
                f"WHERE id = %s",
                [ctx.row_id],
            )
        else:
            self._sql_executor(
                f'UPDATE "{ctx.table}" SET "{target_field}" = %s WHERE id = %s',
                [value, ctx.row_id],
            )

    def _tag_field(self, action: ActionDef, ctx: TriggerContext) -> None:
        """Write a validation status onto the row, auto-creating the column.

        v1.6.0 (#123) — wires the ``validate: mode: tag`` rules and
        explicit ``tag_field:`` actions defined in ``triggers.yaml``.
        Differs from :meth:`_set_field` in two ways:

        - The target column (``column``, optional ``message_column``) is
          auto-created with ``ALTER TABLE ADD COLUMN`` on first use. We
          look at SQLite's ``PRAGMA table_info`` to decide; the result
          is cached per ``(table, column)`` to avoid re-checking on
          every event. The cache is process-local — across-process
          contention is fine because ``ALTER TABLE ADD COLUMN`` on a
          non-existing column races safely (SQLite raises ``duplicate
          column name`` which we catch and treat as success).
        - The write-back is multi-column: ``column`` and (optionally)
          ``message_column`` updated in a single statement so the row
          stays consistent for QGIS / portal observers.
        """
        column = action.config.get("column", "")
        value = action.config.get("value")
        message_column = action.config.get("message_column")
        message = action.config.get("message")
        if not column or not self._sql_executor:
            return
        _validate_identifier(ctx.table, "table")
        _validate_identifier(column, "column")
        if message_column:
            _validate_identifier(message_column, "message_column")

        wanted: list[str] = [column]
        if message_column:
            wanted.append(message_column)
        self._ensure_columns(ctx.table, wanted)

        # Origin-tagging M1 (B-02 / v1.5.3) — same guard as _set_field so
        # tag_field writes do not loop through the AFTER UPDATE trigger.
        trigger_marker = (
            f"trigger:{ctx.trigger.id}"
            if getattr(ctx, "trigger", None) is not None
            and getattr(ctx.trigger, "id", None) is not None
            else None
        )
        if message_column:
            set_clause = (
                f'"{column}" = %s, "{message_column}" = %s'
            )
            params: list[Any] = [value, message]
        else:
            set_clause = f'"{column}" = %s'
            params = [value]

        if trigger_marker is not None:
            set_clause += ', "_gispulse_origin" = %s'
            params.append(trigger_marker)
        params.append(ctx.row_id)

        self._sql_executor(
            f'UPDATE "{ctx.table}" SET {set_clause} WHERE id = %s',
            params,
        )
        if trigger_marker is not None:
            self._sql_executor(
                f'UPDATE "{ctx.table}" SET "_gispulse_origin" = NULL WHERE id = %s',
                [ctx.row_id],
            )

    def _ensure_columns(self, table: str, columns: list[str]) -> None:
        """Add ``columns`` to ``table`` if any are missing.

        Each column is created as ``TEXT`` since the v1.6.0 surface only
        writes status strings. Uses the per-instance cache to avoid
        running ``PRAGMA table_info`` on every dispatch — the lock
        protects the cache, not the SQL itself (SQLite serialises ALTER
        TABLE writers on its own).
        """
        if not self._sql_executor:
            return
        cache = self._tag_field_known_columns
        with self._tag_field_lock:
            known = cache.setdefault(table, set())
            missing = [c for c in columns if c not in known]
        if not missing:
            return
        try:
            existing = {
                row[1] if not isinstance(row, dict) else row.get("name")
                for row in self._sql_executor(
                    f'PRAGMA table_info("{table}")', []
                )
                or []
            }
        except Exception as exc:  # noqa: BLE001 — driver-specific
            log.warning("tag_field_pragma_failed", table=table, error=str(exc))
            existing = set()

        for col in missing:
            if col in existing:
                continue
            try:
                self._sql_executor(
                    f'ALTER TABLE "{table}" ADD COLUMN "{col}" TEXT', []
                )
            except Exception as exc:  # noqa: BLE001
                # SQLite raises ``duplicate column name`` when two
                # workers race; treat as success.
                if "duplicate column" in str(exc).lower():
                    pass
                else:
                    log.warning(
                        "tag_field_alter_failed",
                        table=table,
                        column=col,
                        error=str(exc),
                    )
                    continue
        with self._tag_field_lock:
            cache.setdefault(table, set()).update(columns)

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
        if not url or not self._webhook_client:
            return
        custom = self._render_payload(action, ctx)
        transition = (
            ctx.eval_result.transition.value
            if ctx.eval_result and ctx.eval_result.transition
            else None
        )
        trigger_name = getattr(ctx.trigger, "name", None) if ctx.trigger else None
        payload = {
            "event_type": "trigger_fired",
            "trigger_id": str(ctx.trigger.id) if ctx.trigger else None,
            "trigger_name": trigger_name,
            "table": ctx.table,
            "operation": ctx.operation,
            "row_id": ctx.row_id,
            "matched": True,
            "transition": transition,
            "timestamp": ctx.timestamp.isoformat() if ctx.timestamp else None,
            "custom": custom,
        }
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
                from gispulse.rules.operation_executor import OperationExecutor

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
        from gispulse.core.sql_safety import validate_expression as _validate_expression
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
        ActionType.TAG_FIELD:        _tag_field,
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
