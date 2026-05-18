"""Headless trigger runtime for GISPulse Mode 1 (CLI/SDK).

This module is the FastAPI-free counterpart of the lifespan-bound
trigger wiring at ``gispulse/adapters/http/app.py:309-391``. The HTTP
server reproduces the same plumbing inside an ``@asynccontextmanager``
because it also needs uvicorn and the WebSocket fan-out; here we only
need a single tick (``run_once``) or a daemon-friendly start/stop pair.

Architecture (Mode 1, GPKG):

    GPKG SQLite triggers ──▶ _gispulse_change_log
                                    │
                                    │  (poll)
                                    ▼
                          ChangeLogWatcher._tick()
                                    │
                                    ├──▶ NullEventHub.broadcast(...)  (no-op)
                                    │
                                    ├──▶ TriggerEvaluator.evaluate(...)
                                    │
                                    └──▶ ActionDispatcher.dispatch_all(
                                            WEBHOOK / SET_FIELD / RUN_SQL / ...
                                        )

Thread safety
-------------
``ChangeLogWatcher`` runs the polling loop on a daemon thread.
:meth:`HeadlessRuntime.run_once` does **not** start that thread — it
manually drives a single ``_tick()`` for ``--once`` mode so the CLI can
exit cleanly. :meth:`HeadlessRuntime.start` / :meth:`stop` are exposed
for future ``--watch`` mode (S5) but stay unused this PR.

AGPL note
---------
Imports are restricted to the public OSS tree (``gispulse.*``,
``persistence.*``, ``rules.*``, ``core.*``, ``capabilities.*``). The
runtime never reaches into ``gispulse-enterprise``.
"""

from __future__ import annotations

import sqlite3
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Sequence

from gispulse.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gispulse.core.models import Trigger
    from gispulse.adapters.esb.action_dispatcher import ActionDispatcher
    from gispulse.runtime.sqlite_retry import RetryingSqlExecutor
    from gispulse.persistence.change_log_watcher import ChangeLogWatcher
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

log = get_logger(__name__)


class ValidationTableResolutionError(ValueError):
    """Raised when ``build_runtime`` cannot pick a table for ``validate:`` rules.

    The runtime evaluates each rule against ``"<table>" WHERE "<pk>" = ?``
    so each rule needs a concrete table at boot time. The auto-wire
    falls back through three sources, and surfaces this error when none
    answer:

    1. ``rule.table`` (per-rule pin in YAML).
    2. ``default_table`` argument (or top-level ``default_table:`` in
       YAML).
    3. The single user table on the GPKG when there is exactly one.

    When the GPKG holds multiple tables and the operator pinned
    nothing, we list the candidates so they can pick one.
    """


# ---------------------------------------------------------------------------
# NullEventHub — no-op stand-in for adapters/http/event_hub.EventHub
# ---------------------------------------------------------------------------


class NullEventHub:
    """Drop-in :class:`EventHub` replacement that swallows every broadcast.

    The watcher and the dispatcher both call ``hub.broadcast(event_type,
    data)``. In Mode 1 we have no WebSocket fan-out — those events are
    not observed, so we accept and discard them.

    The HTTP ``EventHub`` also exposes ``bind_loop`` and a coroutine
    ``subscribe`` API; we deliberately do **not** implement those here
    because the headless path is single-threaded from the dispatcher's
    point of view (the watcher thread broadcasts, the hub no-ops).
    """

    def broadcast(  # pragma: no cover - trivial stub
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """No-op: discard the event payload."""
        return None


# ---------------------------------------------------------------------------
# Runtime handle
# ---------------------------------------------------------------------------


@dataclass
class HeadlessRuntime:
    """Bundle of objects returned by :func:`build_runtime`.

    Lifecycle:
        - ``run_once()``  — drive a single watcher tick, then return
                            (used by ``gispulse triggers run --once``).
        - ``start()`` / ``stop()`` — start the polling daemon thread
                            (reserved for ``--watch``, not exposed yet).
        - ``close()``     — close the underlying GPKG engine.
    """

    engine: "GeoPackageEngine"
    event_hub: NullEventHub
    action_dispatcher: "ActionDispatcher"
    watcher: "ChangeLogWatcher"
    triggers: list["Trigger"] = field(default_factory=list)
    gpkg_path: Path = field(default_factory=Path)
    # When the caller did not inject a custom ``sql_executor``, this
    # holds the :class:`RetryingSqlExecutor` wrapper installed around
    # ``engine.execute`` (S5). The CLI ``--watch`` mode reads
    # ``.snapshot_retries()`` per tick to populate the JSON log.
    # ``None`` when no executor is configured at all.
    retrying_sql: "RetryingSqlExecutor | None" = None

    def run_once(self) -> int:
        """Run one polling tick. Returns the number of change-log rows
        processed.

        Notes:
            - The watcher's daemon thread is **not** started; we drive
              ``_tick()`` directly so the CLI can exit cleanly without
              relying on ``stop()`` join semantics.
            - ``_tick()`` swallows broadcast / dispatch failures
              internally (per-row try/except), so a single bad webhook
              does not abort the batch.
        """
        # ``_tick`` is "private" but documented as the unit of work and
        # is safe to call directly: the public ``start()`` / ``stop()``
        # only adds the daemon thread + sleep loop on top.
        return self.watcher._tick()  # noqa: SLF001 - intentional

    def start(self) -> None:
        """Start the polling daemon thread (reserved for ``--watch``)."""
        self.watcher.start()

    def stop(self) -> None:
        """Stop the polling daemon thread (reserved for ``--watch``)."""
        self.watcher.stop()

    def close(self) -> None:
        """Release the underlying GPKG engine."""
        try:
            self.engine.close()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("headless_runtime_close_failed", error=str(exc))

    # Context manager sugar so callers can ``with build_runtime(...) as rt:``
    def __enter__(self) -> "HeadlessRuntime":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _check_pragmas(engine: "GeoPackageEngine") -> None:
    """Verify the GPKG opened in WAL mode with a sensible busy timeout.

    The engine sets ``journal_mode=WAL`` + ``busy_timeout=5000`` in
    ``_open_conn``. We re-check at runtime startup so an externally
    re-created GPKG (or one opened in DELETE mode by a rogue tool) is
    surfaced loudly instead of silently bottlenecking the watcher.
    """
    conn = engine._get_conn()  # noqa: SLF001 - documented internal accessor

    journal_mode = ""
    busy_timeout = 0
    try:
        cur = conn.execute("PRAGMA journal_mode")
        row = cur.fetchone()
        if row is not None:
            journal_mode = str(row[0]).lower()
        cur = conn.execute("PRAGMA busy_timeout")
        row = cur.fetchone()
        if row is not None:
            busy_timeout = int(row[0])
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log.warning("pragma_check_failed", error=str(exc))
        return

    if journal_mode != "wal":
        warnings.warn(
            f"GPKG journal_mode is {journal_mode!r}, not 'wal'. Concurrent "
            "writers may block the trigger watcher.",
            RuntimeWarning,
            stacklevel=2,
        )
    if busy_timeout < 5000:
        warnings.warn(
            f"GPKG busy_timeout is {busy_timeout} ms (< 5000 ms). The watcher "
            "may raise SQLITE_BUSY under load.",
            RuntimeWarning,
            stacklevel=2,
        )


def _list_user_tables(gpkg_path: Path) -> list[str]:
    """Return the user-facing layer names exposed by ``gpkg_path``.

    Reads ``gpkg_contents`` directly so internal GISPulse bookkeeping
    tables (``_gispulse_*``, ``gpkg_*``) are filtered out by virtue of
    not being declared as ``features`` / ``attributes`` rows there.
    pyogrio's ``list_layers`` returns everything including the
    ``_gispulse_*`` tables, which would confuse the auto-detect path.
    """
    from gispulse.persistence.gpkg_connection import connect_gpkg

    try:
        conn = connect_gpkg(gpkg_path)
        try:
            cur = conn.execute(
                "SELECT table_name FROM gpkg_contents "
                "WHERE data_type IN ('features', 'attributes') "
                "ORDER BY table_name"
            )
            return [str(row[0]) for row in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error:  # pragma: no cover — defensive
        return []


def resolve_validation_table(
    rule: Any,
    *,
    gpkg_path: Path,
    default_table: str | None,
) -> str:
    """Pick the table a ``validate:`` rule should run against.

    Priority:
      1. ``rule.table`` (operator pinned the rule explicitly).
      2. ``default_table`` (top-level YAML / ``build_runtime`` arg).
      3. Single user table on the GPKG → auto-select it.
      4. Otherwise → :class:`ValidationTableResolutionError` with the
         list of candidate tables, so the operator can pick one.

    The resolver does not read the rule's SQL; it relies entirely on
    the metadata pins. ``layer='self'`` references inside the rule
    expression are resolved separately by the DSL compiler against
    :class:`gispulse.dsl.CompilationContext.current_table` (which
    receives whatever table this resolver returns).
    """
    explicit = getattr(rule, "table", None)
    if explicit:
        return str(explicit)
    if default_table:
        return default_table

    layers = _list_user_tables(gpkg_path)
    if len(layers) == 1:
        return layers[0]

    rule_id = getattr(rule, "id", "<unknown>")
    if not layers:
        raise ValidationTableResolutionError(
            f"validate rule {rule_id!r}: cannot pick a target table — "
            f"the GPKG at {gpkg_path} has no user tables. Set "
            f"``rule.table`` or top-level ``default_table:`` in your "
            f"triggers.yaml."
        )
    raise ValidationTableResolutionError(
        f"validate rule {rule_id!r}: cannot pick a target table — "
        f"the GPKG at {gpkg_path} has {len(layers)} user tables "
        f"({sorted(layers)}). Set ``rule.table`` or top-level "
        f"``default_table:`` in your triggers.yaml."
    )


def build_runtime(
    gpkg_path: str | Path,
    triggers: Sequence["Trigger"],
    *,
    webhook_allowlist: Iterable[str] | None = None,
    poll_interval: float = 1.0,
    batch_limit: int = 200,
    bulk_threshold: int = 0,
    dataset_id: str = "__cli__",
    sql_executor: Callable[..., Any] | None = None,
    webhook_client: Callable[[str, dict[str, Any]], None] | None = None,
    validate_rules: Sequence[Any] | None = None,
    default_table: str | None = None,
    layer_sources: Sequence[Any] | None = None,
    source_epsg: str | None = None,
) -> HeadlessRuntime:
    """Wire a headless trigger runtime over a single GPKG file.

    This reproduces the FastAPI lifespan wiring at
    ``adapters/http/app.py:309-391`` minus the HTTP server, the job
    worker, and the scheduler.

    Args:
        gpkg_path:         Path to the project GPKG. Must already be a
                           valid GeoPackage (the engine bootstraps the
                           internal tables on open).
        triggers:          List of :class:`Trigger` instances to evaluate.
                           Stored at runtime build time (snapshot).
                           ``--watch`` will re-poll this list each tick
                           via the embedded ``triggers_provider``.
        webhook_allowlist: When provided, restrict outbound webhook URLs
                           to hosts in this set. Empty/None disables the
                           filter (default ``HttpWebhookClient`` SSRF
                           policy still applies).
        poll_interval:     Seconds between two ``_tick`` calls when the
                           daemon thread is started (``--watch``). The
                           ``run_once`` path ignores this value.
        batch_limit:       Max change-log rows pulled per tick.
        dataset_id:        Synthetic id stamped onto every broadcast
                           payload. Mandatory non-empty per the watcher
                           contract.
        sql_executor:      Optional ``(sql, params) -> Any`` callable
                           injected into the dispatcher. Defaults to
                           ``engine.execute`` if available.
        webhook_client:    Optional ``(url, payload) -> None`` callable
                           injected into the dispatcher. Defaults to
                           :class:`HttpWebhookClient`.
        validate_rules:    Optional list of ``ValidateRuleConfigModel``
                           (or any object exposing ``id`` / ``rule`` /
                           ``mode`` / ``tag_field`` / ``message`` /
                           ``enabled`` / ``table``). When non-empty the
                           runtime spins up a :class:`ValidationRunner`,
                           wires it onto the change-log watcher, and
                           auto-picks a target table per rule (cf
                           :func:`resolve_validation_table`).
        default_table:     Fallback table for ``validate:`` rules that
                           have no ``rule.table``. Single-table GPKGs
                           don't need this knob.
        layer_sources:     Optional list of
                           :class:`gispulse.runtime.config_loader.LayerSourceConfigModel`
                           (or duck-typed objects with ``name`` /
                           ``uri`` / ``table`` / ``schema_``) used to
                           build a :class:`LayerRegistry`. Cross-source
                           DSL references (``layer='communes'``)
                           resolve through these declarations at the
                           validation runner's session ATTACH time.
        source_epsg:       CRS of the dataset's geometry column
                           (``"EPSG:2154"``…). Forwarded to
                           :func:`compile_validate_rules`. Optional;
                           only required when validate rules use
                           CRS-aware geom fcts.

    Returns:
        A :class:`HeadlessRuntime` ready for ``run_once()`` or daemon
        ``start()`` / ``stop()``.

    Raises:
        ValueError:    Empty ``dataset_id`` or non-positive intervals.
        FileNotFoundError: ``gpkg_path`` does not exist.
        ValidationTableResolutionError: when ``validate_rules`` is
            non-empty and a rule's target table cannot be resolved.
    """
    # Lazy imports keep CLI startup snappy: we only need rules / esb
    # when the user actually runs ``triggers``.
    from gispulse.adapters.esb.action_dispatcher import ActionDispatcher
    from gispulse.adapters.webhooks import HttpWebhookClient
    from gispulse.runtime.sqlite_retry import RetryingSqlExecutor
    from gispulse.persistence.change_log_watcher import ChangeLogWatcher
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    if poll_interval <= 0:
        raise ValueError("poll_interval must be > 0")
    if batch_limit <= 0:
        raise ValueError("batch_limit must be > 0")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("dataset_id must be a non-empty string")

    gpkg = Path(gpkg_path).expanduser()
    if not gpkg.exists():
        raise FileNotFoundError(f"GPKG not found: {gpkg}")

    # ---- Engine -------------------------------------------------------
    engine = GeoPackageEngine(path=gpkg)
    engine.open()
    _check_pragmas(engine)

    # ---- EventHub (no-op) --------------------------------------------
    hub = NullEventHub()

    # ---- ActionDispatcher --------------------------------------------
    # Match app.py wiring: sql_executor falls back to engine.execute,
    # webhook_client to HttpWebhookClient.post (SSRF-safe by default).
    #
    # S6: ``GeoPackageEngine.execute`` is now a real method (sandbox'd
    # DML path with SQL guardrails — see persistence.sql_guardrails).
    # ``set_field`` and ``run_sql`` actions, previously silent no-ops,
    # now actually mutate the GPKG.
    #
    # S5: wrap whatever executor we end up with in a
    # :class:`RetryingSqlExecutor` so transient ``SQLITE_BUSY`` errors
    # (concurrent QGIS save, peer GISPulse tick) get up to 5 backoff
    # retries instead of failing the action on first lock contention.
    # Permanent errors (no such table, syntax, ``SecurityError``)
    # bypass the retry and surface immediately. The wrapper exposes
    # ``snapshot_retries`` so the CLI ``--watch`` daemon can include
    # the count in its tick log.
    if sql_executor is None:
        sql_executor = getattr(engine, "execute", None)
    retrying_sql: RetryingSqlExecutor | None = None
    if sql_executor is not None:
        retrying_sql = RetryingSqlExecutor(sql_executor)
        sql_executor = retrying_sql

    # Build the allowlist once (used both for logging and as the
    # default-client wrapper). When the caller injects an explicit
    # ``webhook_client`` we still keep the allowlist around for
    # observability without enforcing it on the injected callable.
    allowlist: set[str] | None = (
        {host.strip().lower() for host in webhook_allowlist if host.strip()}
        if webhook_allowlist
        else None
    )

    if webhook_client is None:
        # Optional allowlist: when set, wrap the SSRF-safe client with
        # an extra host check so operators can pin outbound traffic to
        # specific destinations even on local fixtures (where the SSRF
        # blocklist alone would refuse RFC1918).
        from urllib.parse import urlparse

        base_client = HttpWebhookClient()

        def _post_with_allowlist(url: str, payload: dict[str, Any]) -> None:
            if allowlist:
                host = (urlparse(url).hostname or "").lower()
                if host not in allowlist:
                    raise PermissionError(
                        f"Webhook host {host!r} not in allowlist "
                        f"{sorted(allowlist)!r}"
                    )
            base_client.post(url, payload)

        webhook_client = _post_with_allowlist

    dispatcher = ActionDispatcher(
        event_hub=hub,
        sql_executor=sql_executor,
        webhook_client=webhook_client,
    )

    # ---- ChangeLogWatcher --------------------------------------------
    trigger_list = list(triggers)

    def _triggers_provider() -> list["Trigger"]:
        # Snapshot reference: callers can mutate the original list and
        # pick up changes on the next tick (matches the API behaviour).
        return [t for t in trigger_list if getattr(t, "enabled", True)]

    # ---- Optional ValidationRunner wiring (v1.6.x) -------------------
    # The runner is engine-agnostic: it talks to a ``sql_evaluator``
    # callable. We build a thin DuckDB session that ATTACHes the GPKG
    # and any cross-source layers declared via ``layer_sources``, then
    # routes ``ST_*`` calls through the spatial extension.
    validation_runner = None
    rules_seq = list(validate_rules) if validate_rules else []
    if rules_seq:
        from gispulse.runtime.layer_registry import LayerRegistry, LayerSource
        from gispulse.runtime.validation_runner import (
            ValidationRunner,
            compile_validate_rules,
        )

        def _resolver(rule: Any) -> str:
            return resolve_validation_table(
                rule, gpkg_path=gpkg, default_table=default_table
            )

        compile_result = compile_validate_rules(
            rules_seq,
            table=default_table or "",
            source_epsg=source_epsg,
            table_resolver=_resolver,
        )
        if compile_result.errors:
            # Surface compile errors loudly. ``triggers validate`` already
            # catches DSL errors at config-load time, so reaching here
            # means a rule that passed schema validation still fails to
            # compile in the runtime CRS context — typically a missing
            # ``source_epsg`` for CRS-aware fcts.
            details = "; ".join(
                f"{e.rule_id}: {e.error}" for e in compile_result.errors
            )
            raise ValueError(
                f"validate rules failed to compile in build_runtime: {details}"
            )

        registry = LayerRegistry()
        for src in layer_sources or ():
            registry.register(
                LayerSource(
                    name=src.name,
                    uri=src.uri,
                    table=getattr(src, "table", None),
                    schema=getattr(src, "schema_", "public") or "public",
                )
            )

        from gispulse.runtime.duckdb_engine import get_spatial_connection

        # The DuckDB session is held by the closure below for the
        # evaluator's lifetime; it's torn down with ``runtime.close()``
        # via the watcher's lifecycle (no leak — DuckDB connections are
        # cleaned up on GC). We ATTACH the project GPKG read-only, then
        # mirror each user table as a view in the in-memory catalog so
        # bare-name references in the compiled rule SQL resolve without
        # qualifying every identifier. Cross-source layers (#122) ride
        # on the same in-memory catalog via :class:`LayerRegistry`.
        _conn = get_spatial_connection()
        if "'" in str(gpkg) or "\x00" in str(gpkg):
            raise ValueError(
                f"gpkg_path contains illegal characters: {gpkg!r}"
            )
        _conn.execute(
            f"ATTACH '{gpkg}' AS __gispulse_gpkg (TYPE SQLITE, READ_ONLY)"
        )
        # Pre-create a view per project user table so bare-name SQL
        # ``FROM "parcels"`` works without ``USE __gispulse_gpkg``
        # (which would block the cross-source CREATE VIEW because the
        # read-only ATTACH refuses DDL).
        for tbl in _list_user_tables(gpkg):
            _conn.execute(
                f'CREATE OR REPLACE VIEW "{tbl}" AS '
                f'SELECT * FROM __gispulse_gpkg."{tbl}"'
            )
        if len(registry) > 0:
            registry.install(_conn)

        def _sql_evaluator(sql: str, params: list[Any]) -> list[Any]:
            return _conn.execute(sql, params).fetchall()

        validation_runner = ValidationRunner(
            compile_result.rules,
            _sql_evaluator,
            hub=hub,
            dataset_id=dataset_id,
            action_dispatcher=dispatcher,
        )

    watcher = ChangeLogWatcher(
        engine=engine,
        event_hub=hub,
        dataset_id=dataset_id,
        poll_interval=poll_interval,
        batch_limit=batch_limit,
        bulk_threshold=bulk_threshold,
        triggers_provider=_triggers_provider,
        action_dispatcher=dispatcher,
        validation_runner=validation_runner,
    )

    log.info(
        "headless_runtime_built",
        gpkg=str(gpkg),
        triggers=len(trigger_list),
        validate_rules=len(rules_seq),
        layer_sources=len(layer_sources or ()),
        webhook_allowlist=sorted(allowlist) if allowlist else None,
    )

    return HeadlessRuntime(
        engine=engine,
        event_hub=hub,
        action_dispatcher=dispatcher,
        watcher=watcher,
        triggers=trigger_list,
        gpkg_path=gpkg,
        retrying_sql=retrying_sql,
    )


__all__ = [
    "HeadlessRuntime",
    "NullEventHub",
    "ValidationTableResolutionError",
    "build_runtime",
    "resolve_validation_table",
]
