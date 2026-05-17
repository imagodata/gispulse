"""Tests for v1.6.0 — ChangeLogWatcher → ValidationRunner hook + factory.

Coverage:
- Watcher accepts ``validation_runner`` kwarg (no-op when ``None``).
- INSERT / UPDATE_GEOM / UPDATE_ATTR events trigger ``runner.evaluate``.
- DELETE events skip the runner (row no longer exists).
- Runner exception is contained — the tick keeps moving.
- ``make_gpkg_sql_evaluator`` returns a working callable that ATTACHes
  a real GPKG and runs the rule SQL emitted by the DSL compiler.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Watcher fixtures
# ---------------------------------------------------------------------------


class _RecordingHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, dict[str, Any]]] = []

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.events.append((event_type, data or {}))


class _FakeRunner:
    """Records every ``evaluate`` call so tests can assert dispatch."""

    def __init__(self, raises: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.raises = raises

    def evaluate(self, table: str, row_id: Any) -> list[Any]:
        self.calls.append((table, row_id))
        if self.raises:
            raise RuntimeError("boom")
        return []


class _FakeEngine:
    backend_name = "gpkg"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._next_id = 1

    def push(
        self,
        table: str,
        op: str,
        fid: str,
        *,
        geom_changed: bool = False,
    ) -> int:
        with self._lock:
            row_id = self._next_id
            self._next_id += 1
            self._rows.append(
                {
                    "id": row_id,
                    "table_name": table,
                    "operation": op,
                    "row_pk": fid,
                    "changed_at": "2026-05-06T00:00:00",
                    "geom_changed": 1 if geom_changed else 0,
                    "processed": 0,
                }
            )
            return row_id

    def get_pending_changes(self, limit: int = 100) -> list[dict]:
        with self._lock:
            pending = [r for r in self._rows if r["processed"] == 0]
            return [dict(r) for r in pending[:limit]]

    def mark_changes_processed(self, up_to_id: int) -> int:
        with self._lock:
            n = 0
            for r in self._rows:
                if r["id"] <= up_to_id and r["processed"] == 0:
                    r["processed"] = 1
                    n += 1
            return n


def _wait_until(predicate, timeout: float = 2.0, step: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


@pytest.fixture
def watcher_with_runner():
    from gispulse.persistence.change_log_watcher import ChangeLogWatcher

    engine = _FakeEngine()
    hub = _RecordingHub()
    runner = _FakeRunner()
    watcher = ChangeLogWatcher(
        engine,
        hub,
        dataset_id="ds-validation",
        poll_interval=0.05,
        validation_runner=runner,
    )
    yield watcher, engine, hub, runner
    watcher.stop()


# ---------------------------------------------------------------------------
# Watcher hook behaviour
# ---------------------------------------------------------------------------


class TestWatcherValidationHook:
    def test_insert_triggers_runner(self, watcher_with_runner) -> None:
        watcher, engine, hub, runner = watcher_with_runner
        engine.push("parcels", "INSERT", "1")
        watcher.start()
        assert _wait_until(lambda: runner.calls)
        assert runner.calls == [("parcels", "1")]

    def test_update_geom_triggers_runner(self, watcher_with_runner) -> None:
        watcher, engine, hub, runner = watcher_with_runner
        engine.push("parcels", "UPDATE", "2", geom_changed=True)
        watcher.start()
        assert _wait_until(lambda: runner.calls)
        # The watcher resolves UPDATE → UPDATE_GEOM via geom_changed (#119)
        # before the validation hook fires; runner sees the resolved table+row
        assert runner.calls[0][0] == "parcels"
        assert runner.calls[0][1] == "2"

    def test_update_attr_triggers_runner(self, watcher_with_runner) -> None:
        watcher, engine, hub, runner = watcher_with_runner
        engine.push("parcels", "UPDATE", "3", geom_changed=False)
        watcher.start()
        assert _wait_until(lambda: runner.calls)
        assert runner.calls[0] == ("parcels", "3")

    def test_delete_skips_runner(self, watcher_with_runner) -> None:
        watcher, engine, hub, runner = watcher_with_runner
        engine.push("parcels", "DELETE", "4")
        watcher.start()
        # Wait until the DELETE event has been broadcast — runner must not see it
        assert _wait_until(lambda: any(e[0] == "dml.changed" for e in hub.events))
        # Give the watcher one extra tick to ensure the runner stays untouched
        time.sleep(0.1)
        assert runner.calls == []

    def test_runner_exception_does_not_abort_tick(
        self, watcher_with_runner
    ) -> None:
        watcher, engine, hub, _runner = watcher_with_runner
        # Replace with a raising runner mid-fixture
        watcher._validation_runner = _FakeRunner(raises=True)
        engine.push("parcels", "INSERT", "5")
        watcher.start()
        # Despite the runner raising, the dml.changed broadcast still fires
        assert _wait_until(lambda: any(e[0] == "dml.changed" for e in hub.events))


class TestWatcherWithoutRunner:
    def test_default_no_runner_no_op(self) -> None:
        from gispulse.persistence.change_log_watcher import ChangeLogWatcher

        engine = _FakeEngine()
        hub = _RecordingHub()
        watcher = ChangeLogWatcher(
            engine, hub, dataset_id="ds-no-validation", poll_interval=0.05
        )
        try:
            engine.push("parcels", "INSERT", "1")
            watcher.start()
            # Runs cleanly without a validation_runner attribute set
            assert _wait_until(lambda: any(e[0] == "dml.changed" for e in hub.events))
        finally:
            watcher.stop()


# ---------------------------------------------------------------------------
# make_gpkg_sql_evaluator factory
# ---------------------------------------------------------------------------


@pytest.fixture
def gpkg_with_parcels(tmp_path: Path) -> Path:
    """Build a minimal SQLite file the DuckDB sqlite_scanner can ATTACH."""
    p = tmp_path / "fixture.gpkg"
    conn = sqlite3.connect(str(p))
    try:
        conn.execute(
            "CREATE TABLE parcels (id INTEGER PRIMARY KEY, label TEXT, npts INTEGER)"
        )
        conn.execute(
            "INSERT INTO parcels VALUES (1, 'alpha', 4), (2, 'beta', 3)"
        )
        conn.commit()
    finally:
        conn.close()
    return p


class TestMakeGpkgSqlEvaluator:
    def test_factory_returns_working_callable(self, gpkg_with_parcels: Path) -> None:
        from gispulse.runtime.duckdb_engine import _reset_cache_for_tests
        from gispulse.runtime.validation_runner import make_gpkg_sql_evaluator

        _reset_cache_for_tests()
        evaluator = make_gpkg_sql_evaluator(str(gpkg_with_parcels))
        rows = evaluator('SELECT label FROM "parcels" WHERE "id" = ?', [1])
        assert rows == [("alpha",)]

    def test_factory_rejects_path_with_quote(self) -> None:
        from gispulse.runtime.validation_runner import make_gpkg_sql_evaluator

        with pytest.raises(ValueError):
            make_gpkg_sql_evaluator("/tmp/foo'.gpkg")

    def test_factory_rejects_invalid_alias(self, gpkg_with_parcels: Path) -> None:
        from gispulse.runtime.validation_runner import make_gpkg_sql_evaluator

        with pytest.raises(ValueError):
            make_gpkg_sql_evaluator(str(gpkg_with_parcels), alias="my db")

    def test_factory_rejects_empty_path(self) -> None:
        from gispulse.runtime.validation_runner import make_gpkg_sql_evaluator

        with pytest.raises(ValueError):
            make_gpkg_sql_evaluator("")

    def test_factory_used_by_validation_runner(self, gpkg_with_parcels: Path) -> None:
        """End-to-end: compile a rule, run it via the factory evaluator."""
        from gispulse.runtime.duckdb_engine import _reset_cache_for_tests
        from gispulse.runtime.validation_runner import (
            ValidationRunner,
            compile_validate_rules,
            make_gpkg_sql_evaluator,
        )
        from types import SimpleNamespace

        _reset_cache_for_tests()
        # Rule: npts >= 4 (passes for row 1 'alpha', fails for row 2 'beta')
        rule = SimpleNamespace(
            id="min_pts",
            rule="npts >= 4",
            mode="warn",
            tag_field=None,
            message="too few points",
            enabled=True,
        )
        compiled = compile_validate_rules(
            [rule], table="parcels", source_epsg=None
        )
        assert len(compiled.rules) == 1

        evaluator = make_gpkg_sql_evaluator(str(gpkg_with_parcels))
        runner = ValidationRunner(compiled.rules, evaluator)

        assert runner.evaluate("parcels", 1) == []
        failures = runner.evaluate("parcels", 2)
        assert len(failures) == 1
        assert failures[0].rule_id == "min_pts"
