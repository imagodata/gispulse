"""Trigger Manager — install PostgreSQL triggers and dispatch rule execution.

Phase 3 trigger system.  When a row is INSERTed or UPDATEd in a watched
table, a PostgreSQL trigger function fires ``pg_notify`` on the GISPulse
channel.  :class:`TriggerManager` installs those trigger functions and,
on the listener side, dispatches the notification to the matching rule(s).

Architecture::

    PostGIS table  ──INSERT/UPDATE──►  PG trigger function
                                        │
                                        ▼
                                   pg_notify('gispulse_events', payload)
                                        │
                                        ▼
                                   PgNotifyListener  (asyncpg LISTEN)
                                        │
                                        ▼
                                   TriggerManager.dispatch()
                                        │
                                        ▼
                                   RuleEngine.apply(rule, gdf)
"""

from __future__ import annotations

import json
from typing import Any, Callable
from uuid import UUID

from core.logging import get_logger
from core.models import Trigger, TriggerType
from core.sql_safety import validate_identifier as _safe_ident

log = get_logger(__name__)


# SQL template for installing a trigger + notify function on a PostGIS table.
# Placeholders are validated via _safe_ident before interpolation.
_TRIGGER_FUNC_SQL = """
CREATE OR REPLACE FUNCTION gispulse_notify_{suffix}()
RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify(
        'gispulse_events',
        json_build_object(
            'trigger_id', '{trigger_id}',
            'table',      TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
            'operation',  TG_OP,
            'row_id',     COALESCE(NEW.id::text, OLD.id::text, ''),
            'timestamp',  now()::text
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS gispulse_trg_{suffix} ON "{schema}"."{table}";
CREATE TRIGGER gispulse_trg_{suffix}
    AFTER INSERT OR UPDATE ON "{schema}"."{table}"
    FOR EACH ROW EXECUTE FUNCTION gispulse_notify_{suffix}();
"""

_DROP_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS gispulse_trg_{suffix} ON "{schema}"."{table}";
DROP FUNCTION IF EXISTS gispulse_notify_{suffix}();
"""


class TriggerManager:
    """Install PostGIS triggers on watched tables.

    The TriggerManager is responsible **only** for installing/uninstalling
    PostgreSQL trigger functions and tracking which tables are watched.

    Dispatch of notifications should go through :class:`EventRouter` which
    handles the full pipeline: predicate evaluation → action dispatch.

    The legacy :meth:`dispatch` and :meth:`dispatch_async` methods are
    kept for backward compatibility but are deprecated — use
    ``EventRouter.handle_notify()`` instead.

    Args:
        engine:      A :class:`PostGISConnection` (or any SpatialEngine with
                     ``execute_sql``).
        rule_runner: (Deprecated) Callable that runs a rule by its UUID.
                     Prefer wiring EventRouter for dispatch.
    """

    def __init__(
        self,
        engine: Any,  # SpatialEngine (PostGISConnection)
        rule_runner: Callable[[UUID, str, str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._rule_runner = rule_runner
        self._installed: dict[str, Trigger] = {}  # table -> Trigger

    # ------------------------------------------------------------------
    # Install / uninstall triggers
    # ------------------------------------------------------------------

    def install(self, trigger: Trigger) -> None:
        """Install a PostgreSQL trigger for the given :class:`Trigger` definition.

        The trigger model must have ``conditions`` with keys:
        - ``table``:  target table name
        - ``schema``: target schema (default "public")

        .. note::
            This method is part of the **Pro-only** ``esb_triggers`` feature.
            It runs ``CREATE TRIGGER`` DDL plus a ``pg_notify`` plpgsql
            function on a PostGIS connection — by definition unreachable
            from a Community deployment (which has no PostGIS engine).
            The Community-tier ``local_triggers`` feature bypasses this
            entire path and dispatches via the in-process event hub
            instead. Caps for ``local_triggers`` (max 5 active triggers,
            no webhook / cron / DLQ / cascade>1) are enforced at the HTTP
            layer in
            :func:`gispulse.adapters.http.routers.triggers_router._enforce_community_trigger_caps`.
        """
        table = trigger.conditions.get("table", "")
        schema = trigger.conditions.get("schema", "public")
        if not table:
            raise ValueError(f"Trigger {trigger.id} has no 'table' in conditions")

        _safe_ident(table, "table")
        _safe_ident(schema, "schema")

        # Use trigger ID suffix to allow multiple triggers per table
        suffix = str(trigger.id).replace("-", "_")
        sql = _TRIGGER_FUNC_SQL.format(
            suffix=suffix,
            table=table,
            schema=schema,
            trigger_id=str(trigger.id),
        )
        self._engine.execute_sql(sql)
        self._installed[str(trigger.id)] = trigger
        log.info(
            "trigger_installed",
            trigger_id=str(trigger.id),
            table=f"{schema}.{table}",
        )

    def uninstall(self, trigger: Trigger) -> None:
        """Remove a previously installed PostgreSQL trigger."""
        table = trigger.conditions.get("table", "")
        schema = trigger.conditions.get("schema", "public")
        _safe_ident(table, "table")
        _safe_ident(schema, "schema")
        suffix = str(trigger.id).replace("-", "_")
        sql = _DROP_TRIGGER_SQL.format(suffix=suffix, table=table, schema=schema)
        self._engine.execute_sql(sql)
        self._installed.pop(str(trigger.id), None)
        log.info("trigger_uninstalled", trigger_id=str(trigger.id))

    def install_all(self, triggers: list[Trigger]) -> int:
        """Install all enabled DML triggers. Returns count installed."""
        count = 0
        for t in triggers:
            if t.enabled and t.trigger_type == TriggerType.DML:
                self.install(t)
                count += 1
        return count

    # ------------------------------------------------------------------
    # Dispatch (DEPRECATED — use EventRouter.handle_notify() instead)
    # ------------------------------------------------------------------

    def dispatch(self, payload_str: str) -> None:
        """Handle an incoming pg_notify payload and execute the linked rule.

        .. deprecated::
            Use :meth:`EventRouter.handle_notify` for the full pipeline
            (predicate evaluation + multi-action dispatch).  This method
            only supports the legacy 1-trigger → 1-rule path.
        """
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            log.warning("trigger_dispatch_bad_payload", raw=payload_str[:200])
            return

        trigger_id = payload.get("trigger_id", "")
        table = payload.get("table", "")
        operation = payload.get("operation", "")
        row_id = payload.get("row_id", "")

        log.info(
            "trigger_dispatch",
            trigger_id=trigger_id,
            table=table,
            operation=operation,
            row_id=row_id,
        )

        # Find the installed trigger
        trigger = self._installed.get(table)
        if trigger is None:
            log.debug("trigger_dispatch_no_match", table=table)
            return

        if trigger.rule_id is None:
            log.debug("trigger_dispatch_no_rule", trigger_id=trigger_id)
            return

        if self._rule_runner:
            self._rule_runner(trigger.rule_id, table, row_id)

    async def dispatch_async(
        self, _conn: Any, _pid: int, _channel: str, payload: str
    ) -> None:
        """Async callback adapter for :class:`PgNotifyListener`."""
        self.dispatch(payload)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def installed_triggers(self) -> dict[str, Trigger]:
        return dict(self._installed)
