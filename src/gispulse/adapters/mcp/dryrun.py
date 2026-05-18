"""Side-effect-free trigger dry-run for the MCP server (issue #202).

``dryrun_trigger`` answers a question an LLM agent asks before it tells a
human to run ``gispulse watch``: *"given this config and the change-log
as it stands right now, what would fire?"* — without actually firing it.

How the no-side-effect guarantee is met
---------------------------------------
:func:`gispulse.runtime.build_runtime` already exposes the two seams the
:class:`~gispulse.adapters.esb.action_dispatcher.ActionDispatcher` routes
**every** outbound effect through:

* ``sql_executor(sql, params)`` — every write action (``set_field``,
  ``run_sql``, ``tag_field``, ``notify``'s ``pg_notify``, aggregates);
* ``webhook_client(url, payload)`` — every outbound HTTP call.

We inject **collectors** for both: callables that record their arguments
and return without touching the database or the network. The dispatcher
runs its normal evaluation path, so trigger predicates, the DSL and event
matching all execute for real — only the *effects* are captured instead
of applied.

One residual write the dispatcher does **not** own is the change-log
*ack*: after a tick, :class:`ChangeLogWatcher` calls
``engine.mark_changes_processed(max_id)`` to stamp ``processed = 1`` on
the rows it handled. A genuine dry-run must not consume the change-log,
so we neutralise that single method on the live engine instance for the
duration of the run (an instance-level override — the production
``GeoPackageEngine`` class is untouched). The rows therefore stay
``processed = 0`` and a subsequent real ``gispulse watch`` still sees
them.

This is the "inject a no-op executor that collects the actions" fallback
the #202 brief calls for: the existing runtime API has no first-class
``evaluate-without-dispatch`` entry point, and re-driving the watcher's
private ``_process_row`` loop would duplicate runtime logic — against the
CLI<->surface symmetry axiom. Wrapping the public seams keeps this
adapter thin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["dryrun_trigger_config"]


def dryrun_trigger_config(
    config_path: str | Path,
    *,
    gpkg_override: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate a ``triggers.yaml`` against the live change-log, no effects.

    Args:
        config_path:   Path to the YAML config (already FS-scoped by the
                       caller).
        gpkg_override: Optional GPKG path that wins over the ``gpkg:`` key.

    Returns:
        ``{gpkg, trigger_count, rows_evaluated, sql_actions, webhook_actions}``
        where ``sql_actions`` / ``webhook_actions`` are the effects that
        *would* have run. ``rows_evaluated`` is the number of change-log
        rows the tick processed.
    """
    from gispulse.runtime.config_loader import load_config, to_triggers

    config = load_config(config_path, gpkg_override=gpkg_override)
    triggers = to_triggers(config)

    sql_calls: list[dict[str, Any]] = []
    webhook_calls: list[dict[str, Any]] = []

    def _collect_sql(sql: str, params: Any = None) -> list:
        sql_calls.append({"sql": str(sql), "params": list(params or [])})
        # Read actions (the aggregate handler iterates the result) expect
        # an iterable; an empty list is a safe, side-effect-free stand-in.
        return []

    def _collect_webhook(url: str, payload: dict[str, Any]) -> None:
        webhook_calls.append({"url": url, "payload": payload})

    rt = _build_runtime(config, triggers, _collect_sql, _collect_webhook)
    try:
        _neutralise_ack(rt.engine)
        rows = rt.run_once()
    finally:
        rt.close()

    return {
        "gpkg": str(config.gpkg),
        "trigger_count": len(triggers),
        "rows_evaluated": int(rows),
        "sql_actions": sql_calls,
        "webhook_actions": webhook_calls,
    }


def _build_runtime(config: Any, triggers: Any, sql_executor: Any, webhook_client: Any):
    """Wire a runtime with collecting executors via :class:`GISPulseApp`."""
    from gispulse.app import get_app

    return get_app().build_watch_runtime(
        config.gpkg,
        triggers,
        webhook_allowlist=config.security.webhook_allowlist,
        batch_limit=config.runtime.max_batch,
        sql_executor=sql_executor,
        webhook_client=webhook_client,
        validate_rules=config.validate_rules,
        default_table=config.default_table,
        layer_sources=config.layers,
    )


def _neutralise_ack(engine: Any) -> None:
    """Disable ``mark_changes_processed`` on this engine instance.

    The dry-run must leave the change-log untouched so a later real
    ``gispulse watch`` still picks the rows up. This is an instance-level
    override; the :class:`GeoPackageEngine` *class* is not modified.
    """

    def _noop(up_to_id: int) -> int:  # noqa: ARG001 - signature match
        return 0

    engine.mark_changes_processed = _noop  # type: ignore[method-assign]
