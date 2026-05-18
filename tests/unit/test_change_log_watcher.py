"""
Unit tests for :class:`persistence.change_log_watcher.ChangeLogWatcher`.

These tests use mock engines / hubs so they don't touch the disk and run
fast even with the polling interval set very low. The integration test
covering an end-to-end FastAPI + GPKG + WebSocket flow lives in
``tests/integration/http/test_change_log_watcher_integration.py``.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from gispulse.persistence.change_log_watcher import ChangeLogWatcher


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _RecordingHub:
    """In-memory stand-in for ``EventHub.broadcast``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, dict[str, Any]]] = []

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.events.append((event_type, data or {}))


class _FakeEngine:
    """Minimal engine that exposes the change-log API."""

    backend_name = "gpkg"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._next_id = 1
        self.processed_calls: list[int] = []
        self.fail_get_pending = False

    def push(
        self, table: str, op: str, fid: str, ts: str = "2026-04-25T00:00:00"
    ) -> int:
        """Append a fake change_log row and return its id."""
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
                    "processed": 0,
                }
            )
            return row_id

    def get_pending_changes(self, limit: int = 100) -> list[dict]:
        if self.fail_get_pending:
            raise RuntimeError("boom")
        with self._lock:
            pending = [r for r in self._rows if r["processed"] == 0]
            return [dict(r) for r in pending[:limit]]

    def mark_changes_processed(self, up_to_id: int) -> int:
        with self._lock:
            self.processed_calls.append(up_to_id)
            n = 0
            for r in self._rows:
                if r["id"] <= up_to_id and r["processed"] == 0:
                    r["processed"] = 1
                    n += 1
            return n


def _wait_until(predicate, timeout: float = 2.0, step: float = 0.02) -> bool:
    """Spin-wait helper — returns True if predicate becomes truthy in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChangeLogWatcher:
    def test_validates_constructor_args(self) -> None:
        engine = _FakeEngine()
        hub = _RecordingHub()
        with pytest.raises(ValueError):
            ChangeLogWatcher(engine, hub, dataset_id="ds-test", poll_interval=0)
        with pytest.raises(ValueError):
            ChangeLogWatcher(engine, hub, dataset_id="ds-test", batch_limit=0)
        # Multi-tenant contract: dataset_id is mandatory and non-empty.
        with pytest.raises(ValueError):
            ChangeLogWatcher(engine, hub, dataset_id="")

    def test_starts_and_stops_cleanly(self) -> None:
        engine = _FakeEngine()
        hub = _RecordingHub()
        watcher = ChangeLogWatcher(engine, hub, dataset_id="ds-test", poll_interval=0.05)
        assert not watcher.is_running()
        watcher.start()
        try:
            assert _wait_until(lambda: watcher.is_running())
        finally:
            watcher.stop()
        assert not watcher.is_running()

    def test_start_is_idempotent(self) -> None:
        engine = _FakeEngine()
        hub = _RecordingHub()
        watcher = ChangeLogWatcher(engine, hub, dataset_id="ds-test", poll_interval=0.05)
        watcher.start()
        try:
            first_thread = watcher._thread
            watcher.start()
            assert watcher._thread is first_thread
        finally:
            watcher.stop()

    def test_broadcasts_dml_changed_and_acks(self) -> None:
        engine = _FakeEngine()
        hub = _RecordingHub()
        watcher = ChangeLogWatcher(
            engine, hub, dataset_id="ds-42", poll_interval=0.02
        )

        engine.push("parcels", "INSERT", "42")
        engine.push("parcels", "UPDATE", "43")

        watcher.start()
        try:
            assert _wait_until(lambda: len(hub.events) >= 2)
        finally:
            watcher.stop()

        types = [e[0] for e in hub.events]
        assert types.count("dml.changed") == 2

        first = hub.events[0][1]
        # Multi-tenant contract: every dml.changed payload carries the
        # dataset_id of the originating watcher.
        assert first["dataset_id"] == "ds-42"
        assert first["table"] == "parcels"
        assert first["op"] == "INSERT"
        assert first["fid"] == "42"
        assert first["change_id"] == 1
        assert "ts" in first
        # Security: no values leaked.
        assert "new_values" not in first
        assert "old_values" not in first

        # max_id of the batch must have been acked.
        assert engine.processed_calls
        assert max(engine.processed_calls) == 2

    def test_no_broadcast_when_no_pending_rows(self) -> None:
        engine = _FakeEngine()
        hub = _RecordingHub()
        watcher = ChangeLogWatcher(engine, hub, dataset_id="ds-test", poll_interval=0.02)
        watcher.start()
        try:
            time.sleep(0.15)  # several ticks
        finally:
            watcher.stop()
        assert hub.events == []
        assert engine.processed_calls == []

    def test_recovers_after_get_pending_failure(self) -> None:
        engine = _FakeEngine()
        hub = _RecordingHub()
        watcher = ChangeLogWatcher(engine, hub, dataset_id="ds-test", poll_interval=0.02)
        # Speed up the recovery wait to keep the test snappy.
        watcher._error_backoff = 0.05

        engine.fail_get_pending = True
        watcher.start()
        try:
            time.sleep(0.15)
            engine.fail_get_pending = False
            engine.push("layer_a", "INSERT", "1")
            assert _wait_until(lambda: len(hub.events) == 1)
        finally:
            watcher.stop()
        assert hub.events[0][0] == "dml.changed"

    def test_evaluates_and_broadcasts_trigger_fired(self) -> None:
        engine = _FakeEngine()
        hub = _RecordingHub()

        class _Fired:
            def __init__(self, trigger_id: str, matched: bool, actions: list[str]):
                self.trigger_id = trigger_id
                self.matched = matched
                self.actions_dispatched = actions
                self.eval_time_ms = 1.5

        class _Evaluator:
            def evaluate(self, change_record, triggers):
                # One matched, one not — only the matched one must be broadcast.
                return [
                    _Fired("t-1", True, ["webhook"]),
                    _Fired("t-2", False, []),
                ]

        triggers_seen: list[list] = []

        def _provider():
            triggers_seen.append(["t-1", "t-2"])
            return ["t-1", "t-2"]  # placeholder list — evaluator ignores it

        watcher = ChangeLogWatcher(
            engine,
            hub,
            dataset_id="ds-trig",
            poll_interval=0.02,
            trigger_evaluator=_Evaluator(),
            triggers_provider=_provider,
        )

        engine.push("parcels", "INSERT", "9")
        watcher.start()
        try:
            assert _wait_until(
                lambda: any(e[0] == "trigger.fired" for e in hub.events)
            )
        finally:
            watcher.stop()

        fired = [e for e in hub.events if e[0] == "trigger.fired"]
        assert len(fired) == 1
        assert fired[0][1]["dataset_id"] == "ds-trig"
        assert fired[0][1]["trigger_id"] == "t-1"
        assert fired[0][1]["change_id"] == 1
        assert fired[0][1]["actions"] == ["webhook"]
        assert triggers_seen, "triggers_provider should have been queried"

    def test_skips_eval_when_provider_returns_empty(self) -> None:
        engine = _FakeEngine()
        hub = _RecordingHub()

        class _Evaluator:
            def __init__(self):
                self.calls = 0

            def evaluate(self, *_a, **_kw):
                self.calls += 1
                return []

        evaluator = _Evaluator()
        watcher = ChangeLogWatcher(
            engine,
            hub,
            dataset_id="ds-test",
            poll_interval=0.02,
            trigger_evaluator=evaluator,
            triggers_provider=lambda: [],
        )

        engine.push("parcels", "INSERT", "1")
        watcher.start()
        try:
            assert _wait_until(lambda: hub.events)
        finally:
            watcher.stop()

        assert evaluator.calls == 0
        assert all(e[0] == "dml.changed" for e in hub.events)

    def test_continues_when_evaluator_raises(self) -> None:
        engine = _FakeEngine()
        hub = _RecordingHub()

        class _Evaluator:
            def evaluate(self, *_a, **_kw):
                raise RuntimeError("evaluator boom")

        watcher = ChangeLogWatcher(
            engine,
            hub,
            dataset_id="ds-test",
            poll_interval=0.02,
            trigger_evaluator=_Evaluator(),
            triggers_provider=lambda: ["t1"],
        )

        engine.push("parcels", "INSERT", "1")
        watcher.start()
        try:
            assert _wait_until(lambda: any(e[0] == "dml.changed" for e in hub.events))
        finally:
            watcher.stop()
        # dml.changed still made it through; ack still happened.
        assert engine.processed_calls == [1]

    def test_broadcast_failure_does_not_block_ack(self) -> None:
        """P0-4a (Beta): a buggy/dead subscriber that raises in
        broadcast() must NOT block ack. Otherwise the same rows
        re-broadcast forever (stuck backlog).
        """
        engine = _FakeEngine()

        class _RaisingHub:
            def __init__(self) -> None:
                self.calls = 0

            def broadcast(self, event_type: str, data: dict) -> None:
                self.calls += 1
                raise RuntimeError("subscriber boom")

        hub = _RaisingHub()
        watcher = ChangeLogWatcher(engine, hub, dataset_id="ds-test", poll_interval=0.02)

        engine.push("parcels", "INSERT", "1")
        engine.push("parcels", "INSERT", "2")

        watcher.start()
        try:
            assert _wait_until(
                lambda: engine.processed_calls and max(engine.processed_calls) >= 2,
                timeout=2.0,
            )
        finally:
            watcher.stop()

        # Broadcast was tried for each row even though it raised.
        assert hub.calls >= 2
        # Ack landed (max_id=2). Backlog drained.
        assert max(engine.processed_calls) == 2

    def test_skips_rows_without_id(self) -> None:
        engine = _FakeEngine()
        hub = _RecordingHub()

        # Inject a malformed row alongside a valid one.
        engine._rows.extend(
            [
                {
                    "id": None,
                    "table_name": "x",
                    "operation": "INSERT",
                    "row_pk": "1",
                    "changed_at": "now",
                    "processed": 0,
                },
            ]
        )
        engine._next_id = 2
        engine.push("y", "UPDATE", "2")  # id=2

        watcher = ChangeLogWatcher(engine, hub, dataset_id="ds-test", poll_interval=0.02)
        watcher.start()
        try:
            assert _wait_until(lambda: any(e[1].get("change_id") == 2 for e in hub.events))
        finally:
            watcher.stop()

        # Only the valid row produced an event, and it was acked.
        change_ids = [e[1]["change_id"] for e in hub.events]
        assert change_ids == [2]
        assert engine.processed_calls == [2]


# ---------------------------------------------------------------------------
# Bridge to ActionDispatcher (#458)
# ---------------------------------------------------------------------------


class TestActionDispatchBridge:
    """Triggers fired by the watcher are dispatched to ActionDispatcher
    (NOTIFY / WEBHOOK / SET_FIELD / RUN_SQL …) — not just broadcast on
    /ws/events. Without this bridge, the entire ESB pipeline + webhook
    client (#451) is dead-code in HTTP runtime.
    """

    def _build_trigger(self, trigger_id, *, actions):
        from gispulse.core.models import Trigger

        return Trigger(id=trigger_id, name=f"t-{trigger_id}", actions=actions)

    def test_dispatches_when_action_dispatcher_wired(self) -> None:
        from uuid import uuid4

        from gispulse.core.graph import ActionDef, ActionType

        engine = _FakeEngine()
        hub = _RecordingHub()

        trig_id = uuid4()
        trigger = self._build_trigger(
            trig_id,
            actions=[
                ActionDef(
                    action_type=ActionType.WEBHOOK,
                    config={"url": "https://example.com/hook"},
                )
            ],
        )

        class _Fired:
            def __init__(self, trigger_id, matched):
                self.trigger_id = trigger_id
                self.matched = matched
                self.actions_dispatched = ["webhook"] if matched else []
                self.eval_time_ms = 0.5

        class _Evaluator:
            def evaluate(self, change_record, triggers):
                return [_Fired(trig_id, True)]

        dispatched: list[tuple[list, Any]] = []

        class _RecordingDispatcher:
            def dispatch_all(self, actions, ctx):
                dispatched.append((list(actions), ctx))
                return len(actions)

        watcher = ChangeLogWatcher(
            engine,
            hub,
            dataset_id="ds-bridge",
            poll_interval=0.02,
            trigger_evaluator=_Evaluator(),
            triggers_provider=lambda: [trigger],
            action_dispatcher=_RecordingDispatcher(),
        )

        engine.push("parcels", "INSERT", "42")
        watcher.start()
        try:
            assert _wait_until(lambda: dispatched)
        finally:
            watcher.stop()

        assert len(dispatched) == 1
        actions, ctx = dispatched[0]
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.WEBHOOK
        # Context built from the change row
        assert ctx.trigger.id == trig_id
        assert ctx.table == "parcels"
        assert ctx.operation == "INSERT"
        assert ctx.row_id == "42"
        assert ctx.eval_result.matched is True

    def test_no_dispatch_when_dispatcher_is_none(self) -> None:
        """Backward-compat: omitting action_dispatcher reverts to broadcast-only."""
        from uuid import uuid4

        from gispulse.core.graph import ActionDef, ActionType

        engine = _FakeEngine()
        hub = _RecordingHub()
        trig_id = uuid4()
        trigger = self._build_trigger(
            trig_id,
            actions=[ActionDef(action_type=ActionType.WEBHOOK, config={"url": "x"})],
        )

        class _Fired:
            def __init__(self):
                self.trigger_id = trig_id
                self.matched = True
                self.actions_dispatched = ["webhook"]
                self.eval_time_ms = 0.0

        class _Evaluator:
            def evaluate(self, change_record, triggers):
                return [_Fired()]

        watcher = ChangeLogWatcher(
            engine,
            hub,
            dataset_id="ds-no-bridge",
            poll_interval=0.02,
            trigger_evaluator=_Evaluator(),
            triggers_provider=lambda: [trigger],
            # action_dispatcher omitted → broadcast-only
        )

        engine.push("parcels", "INSERT", "1")
        watcher.start()
        try:
            assert _wait_until(
                lambda: any(e[0] == "trigger.fired" for e in hub.events)
            )
        finally:
            watcher.stop()
        # No raise — broadcast happened, dispatch was just skipped.

    def test_dispatcher_failure_does_not_block_tick(self) -> None:
        """A buggy dispatcher must not pin the change-log backlog."""
        from uuid import uuid4

        from gispulse.core.graph import ActionDef, ActionType

        engine = _FakeEngine()
        hub = _RecordingHub()
        trig_id = uuid4()
        trigger = self._build_trigger(
            trig_id,
            actions=[ActionDef(action_type=ActionType.WEBHOOK, config={"url": "x"})],
        )

        class _Fired:
            def __init__(self):
                self.trigger_id = trig_id
                self.matched = True
                self.actions_dispatched = []
                self.eval_time_ms = 0.0

        class _Evaluator:
            def evaluate(self, change_record, triggers):
                return [_Fired()]

        class _ExplodingDispatcher:
            def dispatch_all(self, actions, ctx):
                raise RuntimeError("dispatcher boom")

        watcher = ChangeLogWatcher(
            engine,
            hub,
            dataset_id="ds-explode",
            poll_interval=0.02,
            trigger_evaluator=_Evaluator(),
            triggers_provider=lambda: [trigger],
            action_dispatcher=_ExplodingDispatcher(),
        )

        engine.push("parcels", "INSERT", "1")
        watcher.start()
        try:
            assert _wait_until(lambda: engine.processed_calls)
        finally:
            watcher.stop()
        # Ack happened despite dispatcher raising — backlog not stuck.
        assert engine.processed_calls == [1]

    def test_skips_unknown_trigger_id(self) -> None:
        """If the FiredTrigger references an id absent from the lookup
        (e.g. trigger removed mid-tick), dispatch is a no-op."""
        from uuid import uuid4

        engine = _FakeEngine()
        hub = _RecordingHub()
        ghost_id = uuid4()

        class _Fired:
            def __init__(self):
                self.trigger_id = ghost_id
                self.matched = True
                self.actions_dispatched = []
                self.eval_time_ms = 0.0

        class _Evaluator:
            def evaluate(self, change_record, triggers):
                return [_Fired()]

        dispatched: list = []

        class _Dispatcher:
            def dispatch_all(self, actions, ctx):
                dispatched.append((actions, ctx))
                return 0

        watcher = ChangeLogWatcher(
            engine,
            hub,
            dataset_id="ds-ghost",
            poll_interval=0.02,
            trigger_evaluator=_Evaluator(),
            triggers_provider=lambda: [],  # ghost is not in the active list
            action_dispatcher=_Dispatcher(),
        )

        engine.push("parcels", "INSERT", "1")
        watcher.start()
        try:
            # Wait for one tick to complete (broadcast still happens)
            assert _wait_until(lambda: engine.processed_calls)
        finally:
            watcher.stop()
        assert dispatched == []  # no dispatch because trigger_id unknown

    # ------------------------------------------------------------------
    # S5: cancellable wait — stop() must return promptly even when the
    # watcher is asleep on a long ``poll_interval``.
    # ------------------------------------------------------------------

    def test_stop_returns_promptly_with_long_poll_interval(self) -> None:
        """Pre-S5 ``time.sleep(poll_interval)`` would force ``stop()`` to
        wait up to ``poll_interval`` before the thread exited. With the
        new :class:`Event`-based wait, ``stop()`` should join in well
        under a second even with ``poll_interval=10s``."""
        engine = _FakeEngine()
        hub = _RecordingHub()
        watcher = ChangeLogWatcher(
            engine, hub, dataset_id="ds-test", poll_interval=10.0
        )
        watcher.start()
        # Give the thread a chance to enter the wait.
        assert _wait_until(lambda: watcher.is_running())

        t0 = time.monotonic()
        watcher.stop()
        elapsed = time.monotonic() - t0

        assert not watcher.is_running()
        assert elapsed < 1.5, (
            f"stop() took {elapsed:.2f}s — Event.wait should interrupt "
            f"a 10s poll within the 2s join timeout"
        )

    def test_stop_event_property_is_exposed(self) -> None:
        """External loops (CLI ``--watch``) consume the same Event when
        they drive ``_tick`` directly instead of starting the daemon."""
        engine = _FakeEngine()
        hub = _RecordingHub()
        watcher = ChangeLogWatcher(engine, hub, dataset_id="ds-test")
        ev = watcher.stop_event
        assert isinstance(ev, threading.Event)
        assert not ev.is_set()
        watcher.stop()
        # After stop(), the event is set so subsequent waiters return
        # immediately. (The thread was never started here.)
        assert ev.is_set()
