"""Tests for ESB components: PredicateEvaluator, StateStore, ActionDispatcher, EventRouter."""

from __future__ import annotations

from uuid import uuid4

import pytest
from shapely.geometry import Point, box

from core.models import (
    ActionDef,
    ActionType,
    AttrPredicate,
    CompoundPredicate,
    EvalResult,
    GeomPredicate,
    SpatialState,
    Transition,
    Trigger,
    TriggerType,
)
from gispulse.adapters.esb.state_store import InMemoryStateStore
from gispulse.adapters.esb.predicate_evaluator import PredicateEvaluator
from gispulse.adapters.esb.action_dispatcher import ActionDispatcher, TriggerContext
from gispulse.adapters.esb.event_router import DMLPayload, EventRouter


# ===================================================================
# StateStore
# ===================================================================


class TestInMemoryStateStore:
    def test_initial_state_is_unknown(self):
        store = InMemoryStateStore()
        oid, pid = uuid4(), uuid4()
        state = store.get_state(oid, pid)
        assert state.state == SpatialState.UNKNOWN

    def test_enter_transition(self):
        store = InMemoryStateStore()
        oid, pid = uuid4(), uuid4()
        t = store.update_state(oid, pid, SpatialState.INSIDE)
        assert t == Transition.ENTER

    def test_no_transition_when_staying_inside(self):
        store = InMemoryStateStore()
        oid, pid = uuid4(), uuid4()
        store.update_state(oid, pid, SpatialState.INSIDE)
        t = store.update_state(oid, pid, SpatialState.INSIDE)
        assert t is None

    def test_exit_transition(self):
        store = InMemoryStateStore()
        oid, pid = uuid4(), uuid4()
        store.update_state(oid, pid, SpatialState.INSIDE)
        t = store.update_state(oid, pid, SpatialState.OUTSIDE)
        assert t == Transition.EXIT

    def test_cleanup(self):
        store = InMemoryStateStore()
        pid = uuid4()
        for _ in range(5):
            store.update_state(uuid4(), pid, SpatialState.INSIDE)
        assert store.size == 5
        removed = store.cleanup_predicate(pid)
        assert removed == 5
        assert store.size == 0


# ===================================================================
# PredicateEvaluator
# ===================================================================


def _ref_loader(table, filt, col):
    """Return a box polygon as reference geometry."""
    return [box(0, 0, 10, 10)]


class TestPredicateEvaluator:
    @pytest.fixture
    def evaluator(self):
        store = InMemoryStateStore()
        return PredicateEvaluator(state_store=store, ref_loader=_ref_loader)

    def test_geom_within(self, evaluator):
        pred = GeomPredicate(op="within", ref_table="zones", ref_geom_col="geom")
        point_inside = Point(5, 5)
        result = evaluator.evaluate(pred, point_inside, {})
        assert result.matched is True

    def test_geom_within_outside(self, evaluator):
        pred = GeomPredicate(op="within", ref_table="zones", ref_geom_col="geom")
        point_outside = Point(50, 50)
        result = evaluator.evaluate(pred, point_outside, {})
        assert result.matched is False

    def test_geom_intersects(self, evaluator):
        pred = GeomPredicate(op="intersects", ref_table="zones", ref_geom_col="geom")
        result = evaluator.evaluate(pred, Point(10, 10), {})
        assert result.matched is True

    def test_geom_distance_lt(self, evaluator):
        pred = GeomPredicate(op="distance_lt", ref_table="zones",
                             ref_geom_col="geom", distance=5.0)
        result = evaluator.evaluate(pred, Point(12, 5), {})
        assert result.matched is True

    def test_geom_with_buffer(self, evaluator):
        pred = GeomPredicate(op="intersects", ref_table="zones",
                             ref_geom_col="geom", buffer_m=10.0)
        # Point at 15, 5 — outside box(0,0,10,10) but buffer(10) reaches it
        result = evaluator.evaluate(pred, Point(15, 5), {})
        assert result.matched is True

    def test_attr_eq(self, evaluator):
        pred = AttrPredicate(field="status", op="eq", value="active")
        result = evaluator.evaluate(pred, None, {"status": "active"})
        assert result.matched is True

    def test_attr_gt(self, evaluator):
        pred = AttrPredicate(field="pressure", op="lt", value=2.5)
        result = evaluator.evaluate(pred, None, {"pressure": 1.8})
        assert result.matched is True

    def test_attr_is_null(self, evaluator):
        pred = AttrPredicate(field="deleted_at", op="is_null", value=None)
        result = evaluator.evaluate(pred, None, {"deleted_at": None})
        assert result.matched is True

    def test_compound_and(self, evaluator):
        pred = CompoundPredicate(
            logic="AND",
            predicates=[
                GeomPredicate(op="within", ref_table="zones", ref_geom_col="geom"),
                AttrPredicate(field="status", op="eq", value="active"),
            ],
        )
        result = evaluator.evaluate(pred, Point(5, 5), {"status": "active"})
        assert result.matched is True

    def test_compound_and_partial_fail(self, evaluator):
        pred = CompoundPredicate(
            logic="AND",
            predicates=[
                GeomPredicate(op="within", ref_table="zones", ref_geom_col="geom"),
                AttrPredicate(field="status", op="eq", value="active"),
            ],
        )
        result = evaluator.evaluate(pred, Point(5, 5), {"status": "inactive"})
        assert result.matched is False

    def test_compound_or(self, evaluator):
        pred = CompoundPredicate(
            logic="OR",
            predicates=[
                GeomPredicate(op="within", ref_table="zones", ref_geom_col="geom"),
                AttrPredicate(field="status", op="eq", value="active"),
            ],
        )
        # Point outside but attr matches
        result = evaluator.evaluate(pred, Point(50, 50), {"status": "active"})
        assert result.matched is True

    def test_compound_not(self, evaluator):
        pred = CompoundPredicate(
            logic="NOT",
            predicates=[
                GeomPredicate(op="within", ref_table="zones", ref_geom_col="geom"),
            ],
        )
        result = evaluator.evaluate(pred, Point(50, 50), {})
        assert result.matched is True

    def test_evaluate_with_transition(self):
        store = InMemoryStateStore()
        evaluator = PredicateEvaluator(state_store=store, ref_loader=_ref_loader)
        pred = GeomPredicate(op="within", ref_table="zones", ref_geom_col="geom")
        oid, pid = uuid4(), uuid4()

        # First evaluation: UNKNOWN → INSIDE = ENTER
        r1 = evaluator.evaluate_with_transition(pred, oid, pid, Point(5, 5), {})
        assert r1.matched is True
        assert r1.transition == Transition.ENTER

        # Second: still inside, no transition
        r2 = evaluator.evaluate_with_transition(pred, oid, pid, Point(6, 6), {})
        assert r2.matched is True
        assert r2.transition is None

        # Third: exits
        r3 = evaluator.evaluate_with_transition(pred, oid, pid, Point(50, 50), {})
        assert r3.matched is False
        assert r3.transition == Transition.EXIT

    def test_eval_time_is_recorded(self, evaluator):
        pred = AttrPredicate(field="x", op="eq", value=1)
        result = evaluator.evaluate(pred, None, {"x": 1})
        assert result.eval_time_ms >= 0


# ===================================================================
# ActionDispatcher
# ===================================================================


class TestActionDispatcher:
    def test_notify_broadcasts(self):
        events = []

        class FakeHub:
            def broadcast(self, evt, data):
                events.append((evt, data))

        dispatcher = ActionDispatcher(event_hub=FakeHub())
        action = ActionDef(action_type=ActionType.NOTIFY, config={"channel": "test"})
        trigger = Trigger(name="t1")
        ctx = TriggerContext(
            trigger=trigger,
            eval_result=EvalResult(matched=True, transition=Transition.ENTER),
            table="fleet",
            row_id="42",
        )
        dispatcher.dispatch(action, ctx)
        assert len(events) == 1
        assert events[0][0] == "trigger:test"

    def test_run_job_calls_runner(self):
        calls = []
        dispatcher = ActionDispatcher(
            job_runner=lambda rid, t, r: calls.append((rid, t, r))
        )
        rule_id = uuid4()
        action = ActionDef(
            action_type=ActionType.RUN_JOB,
            config={"rule_id": str(rule_id)},
        )
        trigger = Trigger(name="t1")
        ctx = TriggerContext(
            trigger=trigger,
            eval_result=EvalResult(matched=True),
            table="sensors",
            row_id="7",
        )
        dispatcher.dispatch(action, ctx)
        assert len(calls) == 1
        assert calls[0][0] == rule_id

    def test_webhook_calls_client(self):
        calls = []
        dispatcher = ActionDispatcher(
            webhook_client=lambda url, payload: calls.append((url, payload))
        )
        action = ActionDef(
            action_type=ActionType.WEBHOOK,
            config={"url": "https://example.com/hook"},
        )
        trigger = Trigger(name="t1")
        ctx = TriggerContext(
            trigger=trigger,
            eval_result=EvalResult(matched=True),
            table="events",
        )
        dispatcher.dispatch(action, ctx)
        assert len(calls) == 1
        assert calls[0][0] == "https://example.com/hook"

    def test_dispatch_all_returns_count(self):
        dispatcher = ActionDispatcher()
        actions = [
            ActionDef(action_type=ActionType.LOG_EVENT),
            ActionDef(action_type=ActionType.LOG_EVENT),
        ]
        trigger = Trigger(name="t1")
        ctx = TriggerContext(
            trigger=trigger,
            eval_result=EvalResult(matched=True),
        )
        count = dispatcher.dispatch_all(actions, ctx)
        assert count == 2


# ===================================================================
# EventRouter
# ===================================================================


class TestEventRouter:
    def _make_trigger(self, table="sensors", with_predicate=True):
        predicates = []
        if with_predicate:
            predicates.append(
                GeomPredicate(op="within", ref_table="zones", ref_geom_col="geom")
            )
        return Trigger(
            name="test_trigger",
            trigger_type=TriggerType.DML,
            predicates=predicates,
            actions=[ActionDef(action_type=ActionType.LOG_EVENT)],
            conditions={"table": table},
            enabled=True,
        )

    def test_dml_event_matched(self):
        store = InMemoryStateStore()
        evaluator = PredicateEvaluator(state_store=store, ref_loader=_ref_loader)
        dispatcher = ActionDispatcher()
        trigger = self._make_trigger()

        router = EventRouter(
            predicate_evaluator=evaluator,
            action_dispatcher=dispatcher,
            trigger_loader=lambda t: [trigger],
        )

        payload = DMLPayload(
            table="sensors",
            operation="INSERT",
            row_id=str(uuid4()),
            new_geom=Point(5, 5),
            new_attrs={"value": 42},
        )
        results = router.handle_dml_event(payload)
        assert len(results) == 1
        assert results[0].matched is True

    def test_dml_event_not_matched(self):
        store = InMemoryStateStore()
        evaluator = PredicateEvaluator(state_store=store, ref_loader=_ref_loader)
        dispatcher = ActionDispatcher()
        trigger = self._make_trigger()

        router = EventRouter(
            predicate_evaluator=evaluator,
            action_dispatcher=dispatcher,
            trigger_loader=lambda t: [trigger],
        )

        payload = DMLPayload(
            table="sensors",
            operation="INSERT",
            row_id=str(uuid4()),
            new_geom=Point(50, 50),  # outside
            new_attrs={},
        )
        results = router.handle_dml_event(payload)
        assert len(results) == 0

    def test_stats_tracking(self):
        store = InMemoryStateStore()
        evaluator = PredicateEvaluator(state_store=store, ref_loader=_ref_loader)
        dispatcher = ActionDispatcher()
        trigger = self._make_trigger()

        router = EventRouter(
            predicate_evaluator=evaluator,
            action_dispatcher=dispatcher,
            trigger_loader=lambda t: [trigger],
        )

        payload = DMLPayload(
            table="sensors", operation="INSERT",
            row_id=str(uuid4()), new_geom=Point(5, 5),
        )
        router.handle_dml_event(payload)
        assert router.stats["events_received"] == 1
        assert router.stats["predicates_matched"] == 1

    def test_handle_notify_json(self):
        import json
        store = InMemoryStateStore()
        evaluator = PredicateEvaluator(state_store=store, ref_loader=_ref_loader)
        dispatcher = ActionDispatcher()
        trigger = self._make_trigger()

        router = EventRouter(
            predicate_evaluator=evaluator,
            action_dispatcher=dispatcher,
            trigger_loader=lambda t: [trigger],
        )

        raw = json.dumps({
            "table": "sensors",
            "operation": "INSERT",
            "row_id": str(uuid4()),
            "new_geom_wkt": "POINT(5 5)",
            "new_attrs": {"val": 1},
        })
        results = router.handle_notify(raw)
        assert len(results) == 1

    def test_cron_event(self):
        store = InMemoryStateStore()
        evaluator = PredicateEvaluator(state_store=store, ref_loader=_ref_loader)
        calls = []

        class FakeHub:
            def broadcast(self, e, d):
                calls.append(e)

        dispatcher = ActionDispatcher(event_hub=FakeHub())
        trigger = Trigger(
            name="daily_check",
            trigger_type=TriggerType.SCHEDULE,
            actions=[ActionDef(action_type=ActionType.NOTIFY, config={"channel": "cron"})],
        )

        router = EventRouter(
            predicate_evaluator=evaluator,
            action_dispatcher=dispatcher,
        )
        router.handle_cron_event(trigger)
        assert len(calls) == 1
