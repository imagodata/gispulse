"""Tests for v1.6.0 #119/#120 plumbing — granular DML verbs + geom_changed.

Two layers under test:

1. ``gispulse.runtime.config_loader``
   - YAML accepts the new verbs ``UPDATE_GEOM`` / ``UPDATE_ATTR`` / ``BULK``
     in ``when:`` while keeping ``UPDATE`` working.
   - ``_expand_when_to_events`` produces the granular events list that
     downstream :class:`DMLConditions` matches against.

2. ``persistence.change_log_watcher.ChangeLogWatcher``
   - Resolves a coarse ``UPDATE`` row to ``UPDATE_GEOM`` / ``UPDATE_ATTR``
     using the change_log's ``geom_changed`` column.
   - Broadcasts the resolved op + ``geom_changed`` flag in the
     ``dml.changed`` payload (#120 plumbing).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Config loader — verb expansion + Pydantic schema
# ---------------------------------------------------------------------------


class TestConfigLoaderWhenVerbs:
    def test_legacy_update_accepted(self) -> None:
        from gispulse.runtime.config_loader import TriggerConfigModel

        m = TriggerConfigModel(name="t", table="x", when=["UPDATE"])
        assert m.when == ["UPDATE"]

    def test_granular_verbs_accepted(self) -> None:
        from gispulse.runtime.config_loader import TriggerConfigModel

        m = TriggerConfigModel(
            name="t",
            table="x",
            when=["INSERT", "UPDATE_GEOM", "UPDATE_ATTR", "DELETE", "BULK"],
        )
        assert "UPDATE_GEOM" in m.when
        assert "BULK" in m.when

    def test_unknown_verb_rejected(self) -> None:
        from gispulse.runtime.config_loader import TriggerConfigModel
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TriggerConfigModel(name="t", table="x", when=["UPSERT"])

    def test_expand_when_to_events_legacy_update(self) -> None:
        from gispulse.runtime.config_loader import _expand_when_to_events

        assert _expand_when_to_events(["INSERT", "UPDATE", "DELETE"]) == [
            "INSERT",
            "UPDATE",
            "UPDATE_GEOM",
            "UPDATE_ATTR",
            "DELETE",
        ]

    def test_expand_when_to_events_granular_only(self) -> None:
        from gispulse.runtime.config_loader import _expand_when_to_events

        assert _expand_when_to_events(["UPDATE_GEOM"]) == ["UPDATE_GEOM"]

    def test_expand_when_to_events_dedup(self) -> None:
        from gispulse.runtime.config_loader import _expand_when_to_events

        # ``UPDATE`` expansion overlaps with ``UPDATE_GEOM`` already present.
        assert _expand_when_to_events(["UPDATE_GEOM", "UPDATE"]) == [
            "UPDATE_GEOM",
            "UPDATE",
            "UPDATE_ATTR",
        ]


class TestToTriggersEventsField:
    def test_to_triggers_emits_events_list(self, tmp_path) -> None:
        from gispulse.runtime.config_loader import (
            GISPulseConfig,
            TriggerConfigModel,
            to_triggers,
        )

        gpkg_path = tmp_path / "x.gpkg"
        gpkg_path.write_bytes(b"")
        cfg = GISPulseConfig(
            version=1,
            gpkg=str(gpkg_path),
            triggers=[TriggerConfigModel(name="t", table="parcels", when=["UPDATE"])],
        )
        trgs = to_triggers(cfg)
        assert len(trgs) == 1
        events = trgs[0].conditions.get("events")
        assert events == ["UPDATE", "UPDATE_GEOM", "UPDATE_ATTR"]


# ---------------------------------------------------------------------------
# Watcher — UPDATE resolution + geom_changed plumbing
# ---------------------------------------------------------------------------


class _RecordingHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, dict[str, Any]]] = []

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.events.append((event_type, data or {}))


class _FakeEngine:
    """Engine stub that exposes the change-log API used by the watcher."""

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
        ts: str = "2026-05-06T00:00:00",
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
                    "changed_at": ts,
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
def watcher_setup():
    from persistence.change_log_watcher import ChangeLogWatcher

    engine = _FakeEngine()
    hub = _RecordingHub()
    watcher = ChangeLogWatcher(
        engine, hub, dataset_id="ds-test", poll_interval=0.05
    )
    yield watcher, engine, hub
    watcher.stop()


class TestWatcherUpdateResolution:
    def test_update_geom_changed_resolves_to_update_geom(self, watcher_setup) -> None:
        watcher, engine, hub = watcher_setup
        engine.push("parcels", "UPDATE", "1", geom_changed=True)
        watcher.start()
        assert _wait_until(lambda: any(e[0] == "dml.changed" for e in hub.events))
        payload = next(e[1] for e in hub.events if e[0] == "dml.changed")
        assert payload["op"] == "UPDATE_GEOM"
        assert payload["geom_changed"] is True

    def test_update_attr_only_resolves_to_update_attr(self, watcher_setup) -> None:
        watcher, engine, hub = watcher_setup
        engine.push("parcels", "UPDATE", "2", geom_changed=False)
        watcher.start()
        assert _wait_until(lambda: any(e[0] == "dml.changed" for e in hub.events))
        payload = next(e[1] for e in hub.events if e[0] == "dml.changed")
        assert payload["op"] == "UPDATE_ATTR"
        assert payload["geom_changed"] is False

    def test_insert_passes_through_unchanged(self, watcher_setup) -> None:
        watcher, engine, hub = watcher_setup
        engine.push("parcels", "INSERT", "3")
        watcher.start()
        assert _wait_until(lambda: any(e[0] == "dml.changed" for e in hub.events))
        payload = next(e[1] for e in hub.events if e[0] == "dml.changed")
        assert payload["op"] == "INSERT"
        # geom_changed is exposed even on INSERT for payload symmetry
        assert payload["geom_changed"] is False

    def test_delete_passes_through_unchanged(self, watcher_setup) -> None:
        watcher, engine, hub = watcher_setup
        engine.push("parcels", "DELETE", "4")
        watcher.start()
        assert _wait_until(lambda: any(e[0] == "dml.changed" for e in hub.events))
        payload = next(e[1] for e in hub.events if e[0] == "dml.changed")
        assert payload["op"] == "DELETE"
