"""Tests for v1.3.0 #8 — bulk-mode tick.

Covers:
    * ``_summarise_batch`` — pure summary builder (op_counts, by_layer,
      change_id_range, ts_range).
    * ``ChangeLogWatcher._bulk_tick`` — broadcasts ``bulk.changed``,
      acks the batch, never raises on broadcast/ack failure.
    * ``ChangeLogWatcher._tick`` — branches to bulk_tick when
      ``len(rows) >= bulk_threshold > 0``, stays per-row otherwise.
"""

from __future__ import annotations

from typing import Any

import pytest

from persistence.change_log_watcher import (
    ChangeLogWatcher,
    _summarise_batch,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeEngine:
    backend_name = "fake"

    def __init__(self, batches: list[list[dict]]) -> None:
        self._batches = batches
        self._index = 0
        self.acked: list[int] = []
        self.fail_ack: bool = False

    def get_pending_changes(self, limit: int) -> list[dict]:
        if self._index >= len(self._batches):
            return []
        rows = self._batches[self._index]
        self._index += 1
        return rows[:limit]

    def mark_changes_processed(self, up_to_id: int) -> int:
        if self.fail_ack:
            raise RuntimeError("fake ack failure")
        self.acked.append(up_to_id)
        return 1


class _CapturingHub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.fail_event_type: str | None = None

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self.fail_event_type and event_type == self.fail_event_type:
            raise RuntimeError(f"fake broadcast failure on {event_type}")
        self.events.append((event_type, data or {}))


def _row(id_: int, table: str, op: str, ts: str = "2026-04-27T10:00:00Z") -> dict:
    return {
        "id": id_,
        "table_name": table,
        "operation": op,
        "row_pk": str(id_),
        "changed_at": ts,
    }


# ---------------------------------------------------------------------------
# _summarise_batch — pure helper
# ---------------------------------------------------------------------------


def test_summarise_counts_ops_and_layers() -> None:
    rows = [
        _row(1, "parcels", "INSERT", "2026-04-27T10:00:00Z"),
        _row(2, "parcels", "INSERT", "2026-04-27T10:00:01Z"),
        _row(3, "parcels", "UPDATE", "2026-04-27T10:00:02Z"),
        _row(4, "roads", "DELETE", "2026-04-27T10:00:03Z"),
    ]
    payload = _summarise_batch(rows, dataset_id="ds-x")
    assert payload["dataset_id"] == "ds-x"
    assert payload["bulk"] is True
    assert payload["row_count"] == 4
    assert payload["layers"] == ["parcels", "roads"]
    assert payload["op_counts"] == {"INSERT": 2, "UPDATE": 1, "DELETE": 1}
    assert payload["by_layer"] == {
        "parcels": {"INSERT": 2, "UPDATE": 1},
        "roads": {"DELETE": 1},
    }
    assert payload["change_id_range"] == [1, 4]
    assert payload["ts_range"] == [
        "2026-04-27T10:00:00Z",
        "2026-04-27T10:00:03Z",
    ]


def test_summarise_handles_unsorted_change_ids() -> None:
    rows = [_row(7, "x", "INSERT"), _row(2, "x", "INSERT"), _row(99, "x", "INSERT")]
    payload = _summarise_batch(rows, "ds")
    assert payload["change_id_range"] == [2, 99]


def test_summarise_handles_missing_timestamps() -> None:
    rows = [
        {"id": 1, "table_name": "x", "operation": "INSERT"},
        {"id": 2, "table_name": "x", "operation": "INSERT"},
    ]
    payload = _summarise_batch(rows, "ds")
    assert payload["ts_range"] == [None, None]


def test_summarise_skips_rows_with_invalid_id() -> None:
    rows = [
        _row(1, "x", "INSERT"),
        {"id": "garbage", "table_name": "x", "operation": "INSERT"},
        _row(3, "x", "INSERT"),
    ]
    payload = _summarise_batch(rows, "ds")
    # change_id range computed from valid rows only.
    assert payload["change_id_range"] == [1, 3]
    # row_count still includes all rows received (the broadcast caller
    # may want to report what hit the buffer, even if some rows are
    # malformed).
    assert payload["row_count"] == 3


# ---------------------------------------------------------------------------
# _bulk_tick — branch behaviour
# ---------------------------------------------------------------------------


def _make_watcher(
    *,
    rows: list[dict],
    bulk_threshold: int,
    hub: _CapturingHub | None = None,
    engine: _FakeEngine | None = None,
    bulk_eval: str = "skip",
    triggers_provider=None,
    trigger_evaluator=None,
    action_dispatcher=None,
) -> tuple[ChangeLogWatcher, _CapturingHub, _FakeEngine]:
    hub = hub or _CapturingHub()
    engine = engine or _FakeEngine([rows])
    watcher = ChangeLogWatcher(
        engine=engine,
        event_hub=hub,
        dataset_id="ds-test",
        poll_interval=0.05,
        batch_limit=1000,
        bulk_threshold=bulk_threshold,
        bulk_eval=bulk_eval,
        triggers_provider=triggers_provider,
        trigger_evaluator=trigger_evaluator,
        action_dispatcher=action_dispatcher,
    )
    return watcher, hub, engine


def test_bulk_tick_broadcasts_single_event() -> None:
    rows = [_row(i, "parcels", "INSERT") for i in range(1, 11)]
    watcher, hub, engine = _make_watcher(rows=rows, bulk_threshold=5)
    processed = watcher._tick()  # noqa: SLF001
    assert processed == 10
    # Exactly one bulk.changed event, no per-row dml.changed events.
    types = [e[0] for e in hub.events]
    assert types == ["bulk.changed"]
    assert hub.events[0][1]["row_count"] == 10
    assert engine.acked == [10]  # max_id


def test_bulk_tick_below_threshold_uses_per_row_dispatch() -> None:
    rows = [_row(i, "parcels", "INSERT") for i in range(1, 4)]
    watcher, hub, engine = _make_watcher(rows=rows, bulk_threshold=10)
    processed = watcher._tick()  # noqa: SLF001
    assert processed == 3
    types = [e[0] for e in hub.events]
    # Three per-row dml.changed, no bulk.changed.
    assert types == ["dml.changed", "dml.changed", "dml.changed"]
    assert engine.acked == [3]


def test_bulk_threshold_zero_disables_bulk_mode() -> None:
    """``bulk_threshold=0`` is the safe default — never bulk."""
    rows = [_row(i, "parcels", "INSERT") for i in range(1, 1001)]
    watcher, hub, engine = _make_watcher(rows=rows, bulk_threshold=0)
    watcher._tick()  # noqa: SLF001
    types = {e[0] for e in hub.events}
    # Per-row dispatch even though we have 1000 rows.
    assert types == {"dml.changed"}
    assert len(hub.events) == 1000


def test_bulk_tick_acks_even_if_broadcast_fails() -> None:
    """A dead subscriber must not pin the watcher to the same backlog."""
    rows = [_row(i, "x", "INSERT") for i in range(1, 21)]
    hub = _CapturingHub()
    hub.fail_event_type = "bulk.changed"
    watcher, _, engine = _make_watcher(rows=rows, bulk_threshold=10, hub=hub)
    watcher._tick()  # noqa: SLF001
    assert engine.acked == [20]


def test_bulk_tick_returns_processed_even_if_ack_fails() -> None:
    """Ack failure is logged but the next tick will see the rows again."""
    rows = [_row(i, "x", "INSERT") for i in range(1, 21)]
    engine = _FakeEngine([rows])
    engine.fail_ack = True
    watcher, _, _ = _make_watcher(rows=rows, bulk_threshold=10, engine=engine)
    processed = watcher._tick()  # noqa: SLF001
    assert processed == 20  # row_count returned regardless
    assert engine.acked == []  # ack was rejected


def test_bulk_threshold_negative_rejected() -> None:
    with pytest.raises(ValueError, match="bulk_threshold"):
        ChangeLogWatcher(
            engine=_FakeEngine([]),
            event_hub=_CapturingHub(),
            dataset_id="ds",
            bulk_threshold=-1,
        )


# ---------------------------------------------------------------------------
# B-01 (v1.5.3, #103) — Mode 3: bulk WS event + per-row trigger eval
# ---------------------------------------------------------------------------


class _RecordingTrigger:
    """Minimal Trigger duck-type for the watcher's eval+dispatch path."""

    def __init__(self, trigger_id: str = "t-1") -> None:
        self.id = trigger_id
        self.name = f"name-{trigger_id}"
        self.actions: list = []
        self.conditions: dict = {}


class _RecordingFiredTrigger:
    def __init__(self, trigger_id: str) -> None:
        self.trigger_id = trigger_id
        self.matched = True
        self.actions_dispatched: list = []
        self.eval_time_ms = 0.5
        self.transition = None


class _RecordingEvaluator:
    """Captures every (record, triggers) call, returns one fired Trigger
    per call so we can count per-row evaluations end-to-end."""

    def __init__(self) -> None:
        self.calls: list = []

    def evaluate(self, record, triggers):
        self.calls.append((record, list(triggers)))
        if not triggers:
            return []
        return [_RecordingFiredTrigger(str(triggers[0].id))]


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.fired: list = []

    def dispatch(self, *args, **kwargs):
        # Real ActionDispatcher.dispatch(action, ctx) signature; the
        # watcher calls a different internal helper, so this is just to
        # satisfy the protocol surface.
        self.fired.append((args, kwargs))


def test_bulk_eval_skip_default_no_per_row_eval() -> None:
    """Mode 2 (back-compat): ``bulk_eval='skip'`` (default) does not
    evaluate triggers when bulk threshold is reached."""
    rows = [_row(i, "parcels", "INSERT") for i in range(1, 11)]
    triggers = [_RecordingTrigger("t-1")]
    evaluator = _RecordingEvaluator()
    watcher, hub, _ = _make_watcher(
        rows=rows,
        bulk_threshold=5,
        triggers_provider=lambda: triggers,
        trigger_evaluator=evaluator,
    )
    watcher._tick()  # noqa: SLF001
    types = [e[0] for e in hub.events]
    assert types == ["bulk.changed"], (
        "Mode 2 must collapse to a single bulk.changed event"
    )
    assert evaluator.calls == [], (
        "Mode 2 (bulk_eval='skip') must skip per-row evaluation"
    )


def test_bulk_eval_per_row_emits_bulk_and_evaluates_each_row() -> None:
    """Mode 3: ``bulk_eval='per_row'`` emits ONE bulk.changed AND
    evaluates triggers per row, broadcasting trigger.fired for matched
    rows. The 50-paste-in-QGIS scenario in the EPIC #103 acceptance
    criteria.
    """
    rows = [_row(i, "parcels", "INSERT") for i in range(1, 11)]
    triggers = [_RecordingTrigger("t-1")]
    evaluator = _RecordingEvaluator()
    watcher, hub, _ = _make_watcher(
        rows=rows,
        bulk_threshold=5,
        bulk_eval="per_row",
        triggers_provider=lambda: triggers,
        trigger_evaluator=evaluator,
    )
    watcher._tick()  # noqa: SLF001
    types = [e[0] for e in hub.events]
    # Exactly one bulk.changed (no dml.changed) plus one trigger.fired
    # per matched row.
    assert types.count("bulk.changed") == 1, (
        "Mode 3 must still collapse the WS broadcast to one bulk event"
    )
    assert types.count("dml.changed") == 0, (
        "Mode 3 must NOT emit per-row dml.changed (the bulk summary "
        "replaces them)"
    )
    assert types.count("trigger.fired") == 10, (
        "Mode 3 must evaluate every row and broadcast trigger.fired "
        "for matched ones"
    )
    assert len(evaluator.calls) == 10, (
        "evaluator.evaluate() must be called once per row in Mode 3"
    )


def test_bulk_eval_per_row_50_qgis_paste_acceptance() -> None:
    """EPIC #103 acceptance: 50 features paste in QGIS → 1 bulk WS
    event + 50 trigger evals."""
    rows = [_row(i, "buildings", "INSERT") for i in range(1, 51)]
    triggers = [_RecordingTrigger("compute-area")]
    evaluator = _RecordingEvaluator()
    watcher, hub, _ = _make_watcher(
        rows=rows,
        bulk_threshold=50,
        bulk_eval="per_row",
        triggers_provider=lambda: triggers,
        trigger_evaluator=evaluator,
    )
    watcher._tick()  # noqa: SLF001
    types = [e[0] for e in hub.events]
    assert types.count("bulk.changed") == 1
    assert len(evaluator.calls) == 50


def test_bulk_eval_per_row_below_threshold_uses_per_row_path() -> None:
    """When the batch is below ``bulk_threshold`` the watcher stays on
    the Mode-1 per-row path (one ``dml.changed`` per row, no bulk
    summary), regardless of ``bulk_eval``."""
    rows = [_row(i, "parcels", "INSERT") for i in range(1, 4)]
    triggers = [_RecordingTrigger("t-1")]
    evaluator = _RecordingEvaluator()
    watcher, hub, _ = _make_watcher(
        rows=rows,
        bulk_threshold=10,
        bulk_eval="per_row",
        triggers_provider=lambda: triggers,
        trigger_evaluator=evaluator,
    )
    watcher._tick()  # noqa: SLF001
    types = [e[0] for e in hub.events]
    assert types.count("bulk.changed") == 0
    assert types.count("dml.changed") == 3, (
        "below-threshold batch stays on the per-row path"
    )
    assert len(evaluator.calls) == 3


def test_bulk_eval_invalid_value_rejected() -> None:
    """``bulk_eval`` is constrained to ``'skip'`` or ``'per_row'``."""
    with pytest.raises(ValueError, match="bulk_eval"):
        ChangeLogWatcher(
            engine=_FakeEngine([]),
            event_hub=_CapturingHub(),
            dataset_id="ds",
            bulk_threshold=10,
            bulk_eval="lol",
        )


def test_bulk_eval_per_row_no_triggers_is_noop() -> None:
    """Mode 3 with no triggers wired: bulk broadcast still happens,
    eval loop runs but does nothing (no evaluator instantiated)."""
    rows = [_row(i, "parcels", "INSERT") for i in range(1, 11)]
    watcher, hub, _ = _make_watcher(
        rows=rows,
        bulk_threshold=5,
        bulk_eval="per_row",
        # No triggers_provider → empty active_triggers, no evaluator.
    )
    watcher._tick()  # noqa: SLF001
    types = [e[0] for e in hub.events]
    assert types == ["bulk.changed"]
