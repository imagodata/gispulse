"""End-to-end coverage for ``set_field`` / ``run_sql`` actions over GPKG.

Why this file
-------------
Before S6, :meth:`GeoPackageEngine.execute` did not exist; both the CLI
:mod:`gispulse.runtime.headless_runtime` and the HTTP lifespan in
``gispulse.adapters.http.app`` defaulted to ``getattr(engine, "execute",
None)`` -> ``None``. The :class:`ActionDispatcher` then silently
short-circuited every ``set_field`` and ``run_sql`` action.

S6 ships :meth:`GeoPackageEngine.execute` (sandbox'd DML path) and wires
it into both runtimes through :class:`RetryingSqlExecutor`. These tests
prove:

1. ``set_field`` actually mutates the target row (CLI ``--once`` path).
2. ``run_sql`` (raw expression mode, SELECT only since
   :func:`core.sql_safety.validate_expression` rejects DML expressions)
   reaches the engine.
3. **Parity** — the same dispatcher wiring on the HTTP lifespan path
   produces the exact same GPKG state, so we cannot regress one branch
   without the other.

We deliberately drive the watcher's ``_tick()`` directly rather than
spawning the daemon thread: the contract is "an external INSERT is
captured by the change-log triggers, then dispatched on the next tick".
Driving ``_tick`` makes that deterministic without sleeps.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from core.graph import ActionDef, ActionType
from core.models import Trigger
from gispulse.runtime.headless_runtime import build_runtime
from persistence.gpkg_engine import GeoPackageEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_gpkg_with_layer(path: Path) -> None:
    """Bootstrap a GPKG with a ``parcels`` table, change tracking enabled.

    The dispatcher's ``_set_field`` builds ``UPDATE "<table>" SET "<f>"
    = %s WHERE id = %s`` — the column **must** be named ``id`` and it
    must match the value the change-log records (``row_pk`` -> the
    ``fid`` of the changed row). To keep the schema realistic and make
    ``id`` line up with what the watcher emits, we declare ``id``
    itself as the GPKG primary key (acceptable — GPKG only requires
    *some* ``INTEGER PRIMARY KEY``, not specifically named ``fid``).
    """
    eng = GeoPackageEngine(path=path)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001 - test setup
        conn.execute(
            'CREATE TABLE "parcels" '
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT, status TEXT)"
        )
        # Audit table (used by run_sql expression test). Plain table —
        # no GPKG metadata, that's fine: it's just a SQLite table inside
        # the file.
        conn.execute(
            'CREATE TABLE "audit_log" '
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "event TEXT, created_at TEXT)"
        )
        conn.commit()
        eng.enable_change_tracking("parcels", pk_col="id")
    finally:
        eng.close()


def _insert_parcel(gpkg: Path, *, name: str = "alpha") -> int:
    """External INSERT — bypasses the engine, exercises the GPKG triggers.

    Returns the auto-allocated ``id`` of the new row so the caller can
    assert against it.
    """
    conn = sqlite3.connect(str(gpkg))
    try:
        cur = conn.execute(
            'INSERT INTO "parcels"(name, status) VALUES (?, ?)',
            (name, "pending"),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _read_status(gpkg: Path, id_: int) -> str | None:
    conn = sqlite3.connect(str(gpkg))
    try:
        row = conn.execute(
            'SELECT status FROM "parcels" WHERE id = ?', (id_,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _audit_count(gpkg: Path) -> int:
    conn = sqlite3.connect(str(gpkg))
    try:
        row = conn.execute('SELECT COUNT(*) FROM "audit_log"').fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _make_set_field_trigger(*, value: str = "enriched") -> Trigger:
    """Trigger that fires on any change to ``parcels`` and stamps status.

    No DSL predicate -> always matches (FiredTrigger.matched=True per the
    evaluator's default-on-no-predicate policy).
    """
    return Trigger(
        name="enrich_parcels",
        actions=[
            ActionDef(
                action_type=ActionType.SET_FIELD,
                config={"field": "status", "value": value},
            )
        ],
        conditions={"table": "parcels", "when": ["INSERT", "UPDATE"]},
    )


def _make_run_sql_audit_trigger() -> Trigger:
    """Trigger that runs an SELECT-only expression on every change.

    ``validate_expression`` (upstream of the dispatcher) refuses DDL/DML
    in ``RUN_SQL.expression`` — so the most we can prove from a YAML
    payload is that a SELECT reaches the engine. We assert this via the
    DEBUG log on ``gispulse.engine.exec`` (caplog) rather than mutating
    the audit table directly.
    """
    return Trigger(
        name="audit_select",
        actions=[
            ActionDef(
                action_type=ActionType.RUN_SQL,
                config={"expression": "SELECT 1"},
            )
        ],
        conditions={"table": "parcels", "when": ["INSERT", "UPDATE"]},
    )


# ---------------------------------------------------------------------------
# CLI / headless runtime path
# ---------------------------------------------------------------------------


def test_set_field_actually_mutates_row_via_cli_runtime(tmp_path: Path) -> None:
    """End-to-end: external INSERT -> tick -> set_field UPDATE applied."""
    gpkg = tmp_path / "cli.gpkg"
    _build_gpkg_with_layer(gpkg)
    pid = _insert_parcel(gpkg)

    # Pre-condition: the row sits at status='pending'.
    assert _read_status(gpkg, pid) == "pending"

    runtime = build_runtime(
        gpkg_path=gpkg,
        triggers=[_make_set_field_trigger(value="enriched")],
        webhook_client=lambda url, payload: None,
        dataset_id="cli-test",
    )
    try:
        # The retry wrapper must be installed by default now.
        assert runtime.retrying_sql is not None

        rows = runtime.run_once()
        assert rows == 1, f"expected one change-log row, got {rows}"
    finally:
        runtime.close()

    # Post-condition: set_field actually mutated the user table.
    assert _read_status(gpkg, pid) == "enriched", (
        "set_field action did not reach the engine — execute() wiring "
        "or guardrails regressed"
    )


def test_run_sql_select_reaches_engine_via_cli_runtime(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """RUN_SQL with an ``expression`` SELECT lands on engine.execute().

    We can't assert a DML side-effect because the upstream
    ``validate_expression`` blocklist refuses DML in ``expression``.
    Instead we assert the engine logged the call at DEBUG (statement
    type = SELECT, params = 0).
    """
    gpkg = tmp_path / "cli_sql.gpkg"
    _build_gpkg_with_layer(gpkg)
    _insert_parcel(gpkg)

    runtime = build_runtime(
        gpkg_path=gpkg,
        triggers=[_make_run_sql_audit_trigger()],
        webhook_client=lambda url, payload: None,
        dataset_id="cli-sql-test",
    )
    try:
        with caplog.at_level("DEBUG", logger="gispulse.engine.exec"):
            runtime.run_once()
    finally:
        runtime.close()

    # The engine logged at least one SELECT (the run_sql expression).
    select_calls = [
        r for r in caplog.records
        if r.name == "gispulse.engine.exec"
        and "statement=SELECT" in r.getMessage()
    ]
    assert select_calls, (
        f"expected at least one SELECT engine_execute log; got "
        f"{[r.getMessage() for r in caplog.records if r.name == 'gispulse.engine.exec']!r}"
    )


# ---------------------------------------------------------------------------
# HTTP / app.py path — parity check
# ---------------------------------------------------------------------------
#
# We do not spin up FastAPI here (that brings DuckDB, DI registry, ws router,
# uvicorn, … way out of scope for a parity test). Instead we replay the
# exact wiring that ``app.py`` ships at the GPKG branch (lines ~330-345):
# build the same RetryingSqlExecutor + ActionDispatcher + ChangeLogWatcher
# combo, drive ``_tick()`` once, and assert the same final GPKG state.
#
# If a future refactor breaks the HTTP wiring out of step with the CLI,
# this test fails immediately.


def _replay_http_lifespan_wiring(
    gpkg: Path, triggers: list[Trigger]
) -> tuple[Any, Any]:
    """Reproduce ``app.py`` GPKG branch wiring without FastAPI.

    Returns ``(engine, watcher)`` so the caller can drive a tick and
    close the engine.
    """
    from gispulse.adapters.esb.action_dispatcher import ActionDispatcher
    from gispulse.runtime.headless_runtime import NullEventHub
    from gispulse.runtime.sqlite_retry import RetryingSqlExecutor
    from persistence.change_log_watcher import ChangeLogWatcher

    engine = GeoPackageEngine(path=gpkg)
    engine.open()

    raw_executor = getattr(engine, "execute", None)
    sql_executor = (
        RetryingSqlExecutor(raw_executor) if raw_executor is not None else None
    )

    hub = NullEventHub()
    dispatcher = ActionDispatcher(
        event_hub=hub,
        sql_executor=sql_executor,
        webhook_client=lambda url, payload: None,
    )

    def _provider() -> list[Trigger]:
        return triggers

    watcher = ChangeLogWatcher(
        engine=engine,
        event_hub=hub,
        dataset_id="__project__",
        triggers_provider=_provider,
        action_dispatcher=dispatcher,
    )
    return engine, watcher


def test_set_field_actually_mutates_row_via_http_wiring(tmp_path: Path) -> None:
    """Same scenario as the CLI test, on the HTTP-shaped wiring."""
    gpkg = tmp_path / "http.gpkg"
    _build_gpkg_with_layer(gpkg)
    pid = _insert_parcel(gpkg)

    assert _read_status(gpkg, pid) == "pending"

    engine, watcher = _replay_http_lifespan_wiring(
        gpkg, [_make_set_field_trigger(value="enriched")]
    )
    try:
        rows = watcher._tick()  # noqa: SLF001 - parity with cli runtime
        assert rows == 1
    finally:
        engine.close()

    assert _read_status(gpkg, pid) == "enriched"


def test_http_and_cli_paths_produce_identical_gpkg_state(
    tmp_path: Path,
) -> None:
    """Parity proof: same INSERT + same trigger -> same final state.

    We run the scenario twice into two independent GPKGs (one through
    the CLI runtime, one through the replayed HTTP wiring) and assert
    the post-tick rows are pointwise equal. Any divergence (one path
    silently no-oping, the other applying the UPDATE) flips this red.
    """
    cli_gpkg = tmp_path / "parity_cli.gpkg"
    http_gpkg = tmp_path / "parity_http.gpkg"

    pids: dict[Path, int] = {}
    for gpkg in (cli_gpkg, http_gpkg):
        _build_gpkg_with_layer(gpkg)
        pids[gpkg] = _insert_parcel(gpkg, name="parity")

    # CLI path
    cli_runtime = build_runtime(
        gpkg_path=cli_gpkg,
        triggers=[_make_set_field_trigger(value="DONE")],
        webhook_client=lambda url, payload: None,
        dataset_id="parity-cli",
    )
    try:
        cli_runtime.run_once()
    finally:
        cli_runtime.close()

    # HTTP-shaped path
    engine, watcher = _replay_http_lifespan_wiring(
        http_gpkg, [_make_set_field_trigger(value="DONE")]
    )
    try:
        watcher._tick()  # noqa: SLF001
    finally:
        engine.close()

    assert _read_status(cli_gpkg, pids[cli_gpkg]) == "DONE"
    assert _read_status(http_gpkg, pids[http_gpkg]) == "DONE"
    assert _read_status(cli_gpkg, pids[cli_gpkg]) == _read_status(
        http_gpkg, pids[http_gpkg]
    )


# ---------------------------------------------------------------------------
# Negative parity — a guardrail blocks both paths the same way
# ---------------------------------------------------------------------------


def test_security_violation_blocks_both_paths_identically(
    tmp_path: Path,
) -> None:
    """If a YAML action somehow tried to write to ``gpkg_contents``, the
    engine raises :class:`SecurityError` on both paths — neither
    silently succeeds, neither silently no-ops. The retry wrapper is
    designed to **not** retry SecurityError; it surfaces immediately.
    """
    from persistence.sql_guardrails import SecurityError

    gpkg = tmp_path / "secviol.gpkg"
    _build_gpkg_with_layer(gpkg)

    runtime = build_runtime(
        gpkg_path=gpkg,
        triggers=[],
        webhook_client=lambda url, payload: None,
        dataset_id="sec-cli",
    )
    try:
        executor = runtime.retrying_sql
        assert executor is not None
        with pytest.raises(SecurityError):
            executor("DELETE FROM gpkg_contents", [])
    finally:
        runtime.close()

    # Same on the replayed HTTP wiring — same engine.execute under the
    # hood, same SecurityError.
    engine, _watcher = _replay_http_lifespan_wiring(gpkg, [])
    try:
        with pytest.raises(SecurityError):
            engine.execute("DELETE FROM gpkg_contents", [])
    finally:
        engine.close()
