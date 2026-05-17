"""Tests for v1.6.0 #120 B-08 — DELETE event hydrates old_values for predicates.

The AFTER DELETE SQLite trigger has been writing OLD.* attributes as a
JSON blob to ``_gispulse_change_log.old_values`` since v1. The bug was
the read path: the watcher dropped the column before passing the row
to the trigger evaluator, so ``predicate: status == 'active'`` could
never fire on a DELETE event.

This module locks the new contract:
- ``changelog_reader._TAIL_COLUMNS`` includes ``old_values``.
- The watcher hydrates ``ChangeRecord.old_values`` (and mirrors it on
  ``new_values`` for the legacy predicate API) when DML is DELETE and
  at least one active trigger carries a predicate.
- A malformed JSON blob is logged + skipped, not allowed to crash the
  tick.
- ``dml.changed`` broadcast payload stays minimal — no leak of row
  attributes on ``/ws/events``.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, dict[str, Any]]] = []

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.events.append((event_type, data or {}))


class _RecordingEvaluator:
    """Captures the ChangeRecord the watcher passes to evaluate()."""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def evaluate(self, change_record: Any, triggers: list[Any]) -> list[Any]:
        self.records.append(change_record)
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
        old_values: dict[str, Any] | str | None = None,
        geom_changed: bool = False,
    ) -> int:
        if isinstance(old_values, dict):
            old_blob = json.dumps(old_values)
        else:
            old_blob = old_values
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
                    "old_values": old_blob,
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


def _make_trigger_with_predicate():
    """Return a Trigger object with a fake ``predicate_ast`` so the watcher
    knows it must hydrate ``new_values`` / ``old_values``.

    The actual predicate AST shape doesn't matter for these tests — we
    only need ``conditions["predicate_ast"]`` to be non-None so the
    code path that reads ``old_values`` runs.
    """
    from gispulse.core.models import Trigger

    return Trigger(
        name="t-delete",
        conditions={"predicate_ast": object(), "events": ["DELETE"]},
        enabled=True,
    )


@pytest.fixture
def watcher_with_predicate():
    from gispulse.persistence.change_log_watcher import ChangeLogWatcher

    engine = _FakeEngine()
    hub = _RecordingHub()
    evaluator = _RecordingEvaluator()
    trigger = _make_trigger_with_predicate()
    watcher = ChangeLogWatcher(
        engine,
        hub,
        dataset_id="ds-delete",
        poll_interval=0.05,
        triggers_provider=lambda: [trigger],
        trigger_evaluator=evaluator,
    )
    yield watcher, engine, hub, evaluator
    watcher.stop()


# ---------------------------------------------------------------------------
# Tail column whitelist
# ---------------------------------------------------------------------------


class TestChangelogReaderTail:
    def test_old_values_in_tail_columns(self) -> None:
        from gispulse.persistence.changelog_reader import _TAIL_COLUMNS

        assert "old_values" in _TAIL_COLUMNS


# ---------------------------------------------------------------------------
# Watcher hydration
# ---------------------------------------------------------------------------


class TestWatcherDeleteHydration:
    def test_delete_populates_old_values_on_record(
        self, watcher_with_predicate
    ) -> None:
        watcher, engine, hub, evaluator = watcher_with_predicate
        engine.push(
            "parcels",
            "DELETE",
            "42",
            old_values={"status": "active", "area_ha": 12.5},
        )
        watcher.start()
        assert _wait_until(lambda: evaluator.records)
        record = evaluator.records[0]
        assert record.old_values == {"status": "active", "area_ha": 12.5}
        # The legacy evaluator surface reads new_values; we mirror so
        # ``predicate: status == 'active'`` keeps working on DELETE.
        assert record.new_values == {"status": "active", "area_ha": 12.5}

    def test_delete_without_old_values_keeps_record_clean(
        self, watcher_with_predicate
    ) -> None:
        watcher, engine, hub, evaluator = watcher_with_predicate
        engine.push("parcels", "DELETE", "1", old_values=None)
        watcher.start()
        assert _wait_until(lambda: evaluator.records)
        record = evaluator.records[0]
        assert record.old_values == {}
        assert record.new_values == {}

    def test_delete_with_malformed_old_values_does_not_crash(
        self, watcher_with_predicate
    ) -> None:
        watcher, engine, hub, evaluator = watcher_with_predicate
        # Garbage that is not valid JSON
        engine.push("parcels", "DELETE", "9", old_values="not json {")
        watcher.start()
        # The record still flows through with empty old_values
        assert _wait_until(lambda: evaluator.records)
        record = evaluator.records[0]
        assert record.old_values == {}

    def test_insert_does_not_consult_old_values(
        self, watcher_with_predicate
    ) -> None:
        watcher, engine, hub, evaluator = watcher_with_predicate
        engine.push(
            "parcels",
            "INSERT",
            "11",
            old_values={"should": "be ignored"},
        )
        watcher.start()
        assert _wait_until(lambda: evaluator.records)
        record = evaluator.records[0]
        # INSERT shouldn't hydrate from old_values column
        assert record.old_values == {}


class TestNoPredicateNoHydration:
    """When no trigger has a predicate, the watcher should NOT pay the SELECT
    cost — and should also not bother hydrating old_values."""

    def test_skip_hydration_without_predicate(self) -> None:
        from gispulse.core.models import Trigger
        from gispulse.persistence.change_log_watcher import ChangeLogWatcher

        engine = _FakeEngine()
        hub = _RecordingHub()
        evaluator = _RecordingEvaluator()
        trigger_no_pred = Trigger(
            name="t",
            conditions={"events": ["DELETE"]},
            enabled=True,
        )
        watcher = ChangeLogWatcher(
            engine,
            hub,
            dataset_id="ds-x",
            poll_interval=0.05,
            triggers_provider=lambda: [trigger_no_pred],
            trigger_evaluator=evaluator,
        )
        try:
            engine.push(
                "parcels",
                "DELETE",
                "1",
                old_values={"status": "active"},
            )
            watcher.start()
            assert _wait_until(lambda: evaluator.records)
            record = evaluator.records[0]
            # No predicate → no hydration
            assert record.old_values == {}
            assert record.new_values == {}
        finally:
            watcher.stop()


# ---------------------------------------------------------------------------
# Security: payload stays minimal
# ---------------------------------------------------------------------------


class TestPayloadStaysMinimal:
    def test_dml_changed_does_not_leak_old_values(
        self, watcher_with_predicate
    ) -> None:
        """`/ws/events` is unauthenticated by contract — row attributes
        must not leak. Only the internal predicate evaluator gets to
        see ``old_values``."""
        watcher, engine, hub, evaluator = watcher_with_predicate
        engine.push(
            "parcels",
            "DELETE",
            "5",
            old_values={"secret": "top_secret_value"},
        )
        watcher.start()
        assert _wait_until(lambda: any(e[0] == "dml.changed" for e in hub.events))
        payload = next(e[1] for e in hub.events if e[0] == "dml.changed")
        assert "secret" not in str(payload)
        assert "old_values" not in payload
        assert "deleted_row" not in payload
        # Payload still contains the routing fields
        assert payload["op"] == "DELETE"
        assert payload["fid"] == "5"
