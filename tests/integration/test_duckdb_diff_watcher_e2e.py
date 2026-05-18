"""End-to-end: ``ChangeLogWatcher`` + ``DuckDBDiffEngine`` + GeoJSON.

Slice 5 of EPIC #105 — the killer demo wiring. A user edits a
``.geojson`` file (in QGIS, vim, a script). The watcher polls the
DuckDBDiffEngine, gets DELETE+INSERT events from the snapshot diff,
and broadcasts ``dml.changed`` on the event hub.

We exercise the watcher's ``_tick()`` directly to keep the test fast
and deterministic — no threading, no sleep loops. Threading is
covered by the per-engine watcher tests; here we focus on
**cross-engine compatibility**: the same watcher class that works on
GPKG works on DuckDBDiffEngine.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.persistence.change_log_watcher import ChangeLogWatcher
from gispulse.persistence.duckdb_diff_engine import DuckDBDiffEngine


def _bump_mtime(path: Path) -> None:
    """Force a fresh mtime — works around second-resolution filesystems."""
    stat = os.stat(path)
    os.utime(path, (stat.st_atime, stat.st_mtime + 1.5))


class _RecordingHub:
    """Minimal stand-in for ``EventHub.broadcast`` — captures events in order."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, dict[str, Any]]] = []

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.events.append((event_type, data or {}))


@pytest.fixture
def initial_geojson(tmp_path: Path) -> Path:
    path = tmp_path / "places.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "name": ["Paris", "Lyon"],
            "population": [2_140_000, 513_000],
            "geometry": [Point(2.35, 48.85), Point(4.83, 45.75)],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(str(path), driver="GeoJSON")
    return path


def _make_watcher(engine: DuckDBDiffEngine, hub: _RecordingHub) -> ChangeLogWatcher:
    return ChangeLogWatcher(
        engine,
        hub,
        dataset_id="test-dataset",
        poll_interval=0.05,
        # Disable the schema-drift watchdog — it's GPKG-specific
        # (PRAGMA table_info on a SQLite connection) and a no-op for
        # engines without ``_get_conn``, but disabling it keeps the
        # test intent obvious.
        schema_drift_check_interval_s=0,
    )


# ---------------------------------------------------------------------------
# E2E: first tick on a populated file emits one ``dml.changed`` per row
# ---------------------------------------------------------------------------


class TestFirstTick:
    def test_inserts_emitted_via_watcher(
        self, initial_geojson: Path
    ) -> None:
        engine = DuckDBDiffEngine(initial_geojson)
        hub = _RecordingHub()

        with engine:
            watcher = _make_watcher(engine, hub)
            n = watcher._tick()

        assert n == 2
        dml_events = [e for e in hub.events if e[0] == "dml.changed"]
        assert len(dml_events) == 2
        ops = sorted(e[1].get("op") for e in dml_events)
        assert ops == ["INSERT", "INSERT"]
        # ``table_name`` is the file stem.
        assert all(e[1].get("table") == "places" for e in dml_events)

    def test_dataset_id_in_payload(
        self, initial_geojson: Path
    ) -> None:
        # Multi-tenant contract: every broadcast carries dataset_id so
        # consumers can disambiguate two ``places`` layers across
        # different files.
        engine = DuckDBDiffEngine(initial_geojson)
        hub = _RecordingHub()
        with engine:
            watcher = _make_watcher(engine, hub)
            watcher._tick()

        dml_events = [e for e in hub.events if e[0] == "dml.changed"]
        assert all(
            e[1].get("dataset_id") == "test-dataset" for e in dml_events
        )


# ---------------------------------------------------------------------------
# E2E: edit a feature → second tick emits DELETE + INSERT
# ---------------------------------------------------------------------------


class TestEditTick:
    def test_edit_emits_delete_plus_insert(
        self, initial_geojson: Path
    ) -> None:
        engine = DuckDBDiffEngine(initial_geojson)
        hub = _RecordingHub()

        with engine:
            watcher = _make_watcher(engine, hub)
            watcher._tick()  # baseline (2 INSERTs)
            hub.events.clear()

            # Edit Lyon's population
            edited = gpd.read_file(str(initial_geojson))
            edited.loc[edited["name"] == "Lyon", "population"] = 999_000
            edited.to_file(str(initial_geojson), driver="GeoJSON")
            _bump_mtime(initial_geojson)

            n = watcher._tick()

        assert n == 2
        dml_events = [e for e in hub.events if e[0] == "dml.changed"]
        assert len(dml_events) == 2
        ops = sorted(e[1].get("op") for e in dml_events)
        # File-blob CDC has no stable PK → an edit is set-diff-equivalent
        # to DELETE (old hash) + INSERT (new hash). Documented behaviour.
        assert ops == ["DELETE", "INSERT"]


# ---------------------------------------------------------------------------
# E2E: idempotency — second tick on unchanged file is a no-op
# ---------------------------------------------------------------------------


class TestIdempotentTick:
    def test_no_event_when_file_unchanged(
        self, initial_geojson: Path
    ) -> None:
        engine = DuckDBDiffEngine(initial_geojson)
        hub = _RecordingHub()

        with engine:
            watcher = _make_watcher(engine, hub)
            watcher._tick()  # baseline
            hub.events.clear()

            n = watcher._tick()  # no changes since last tick

        assert n == 0
        assert hub.events == []


# ---------------------------------------------------------------------------
# E2E: trigger evaluator + action dispatcher ride along
# ---------------------------------------------------------------------------


class TestWithTriggerEvaluator:
    """Confirm the existing trigger-fire pipeline ignores the engine
    family — the same evaluator that runs against GPKG events fires
    against DuckDBDiff events without modification.
    """

    def test_trigger_eval_called_with_change_record(
        self, initial_geojson: Path
    ) -> None:
        from gispulse.core.models import ChangeRecord, Trigger, TriggerEvent, TriggerType

        # Simple trigger that matches every DATA_CHANGED on ``places``
        trigger = Trigger(
            name="any_change",
            event=TriggerEvent.DATA_CHANGED,
            trigger_type=TriggerType.DML,
            conditions={"table": "places"},
            enabled=True,
        )

        captured: list[ChangeRecord] = []

        class _CapturingEvaluator:
            def evaluate(self, record, triggers):
                captured.append(record)
                return []

        engine = DuckDBDiffEngine(initial_geojson)
        hub = _RecordingHub()
        with engine:
            watcher = ChangeLogWatcher(
                engine,
                hub,
                dataset_id="test-dataset",
                poll_interval=0.05,
                schema_drift_check_interval_s=0,
                trigger_evaluator=_CapturingEvaluator(),
                triggers_provider=lambda: [trigger],
            )
            watcher._tick()

        # Two INSERTs on the initial file → evaluator called twice.
        assert len(captured) == 2
        assert all(r.table_name == "places" for r in captured)
