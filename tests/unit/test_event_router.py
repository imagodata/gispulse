"""Tests for adapters.esb.event_router — central ESB event routing.

EventRouter orchestrates: DML/CRON/API events → load predicates → evaluate
→ dispatch actions → broadcast. Uses fake evaluator/dispatcher/hub so we
cover the routing + predicate-logic + legacy-dispatch paths without a real
PostgreSQL / WebSocket stack.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

import pytest

from gispulse.adapters.esb.event_router import DMLPayload, EventRouter
from gispulse.core.graph import ActionDef, ActionType, EvalResult, Transition
from gispulse.core.models import Trigger


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeEvaluator:
    """Predicate evaluator stub — returns canned results in FIFO order."""

    def __init__(self, results: list[EvalResult] | None = None) -> None:
        self._results = list(results or [])
        self.calls: list[dict] = []

    def evaluate_with_transition(self, pred, **kwargs) -> EvalResult:
        self.calls.append({"pred": pred, **kwargs})
        if self._results:
            return self._results.pop(0)
        return EvalResult(matched=False)


class FakeDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[tuple] = []

    def dispatch_all(self, actions, context) -> None:
        self.dispatched.append((list(actions), context))


class FakeEventHub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def broadcast(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


@dataclass
class FakePredicate:
    name: str = "p"


def _make_trigger(
    *,
    predicates: list | None = None,
    actions: list[ActionDef] | None = None,
    rule_id=None,
    logic: str = "AND",
) -> Trigger:
    return Trigger(
        id=uuid4(),
        name="t",
        rule_id=rule_id,
        predicates=predicates or [],
        predicate_logic=logic,
        actions=actions or [],
    )


@pytest.fixture
def evaluator() -> FakeEvaluator:
    return FakeEvaluator()


@pytest.fixture
def dispatcher() -> FakeDispatcher:
    return FakeDispatcher()


@pytest.fixture
def hub() -> FakeEventHub:
    return FakeEventHub()


def _make_router(evaluator, dispatcher, hub, triggers=None):
    return EventRouter(
        predicate_evaluator=evaluator,
        action_dispatcher=dispatcher,
        event_hub=hub,
        trigger_loader=lambda table: triggers or [],
    )


# ---------------------------------------------------------------------------
# DML events
# ---------------------------------------------------------------------------


class TestHandleDmlEvent:
    def test_no_triggers_returns_empty(self, evaluator, dispatcher, hub):
        router = _make_router(evaluator, dispatcher, hub, triggers=[])
        results = router.handle_dml_event(DMLPayload(table="parcels"))
        assert results == []
        assert dispatcher.dispatched == []
        assert router.stats == {"events_received": 1, "predicates_matched": 0}

    def test_trigger_with_no_loader_returns_empty(self, evaluator, dispatcher, hub):
        # Loader absent → _load_triggers returns [] by default
        router = EventRouter(
            predicate_evaluator=evaluator,
            action_dispatcher=dispatcher,
            event_hub=hub,
            trigger_loader=None,
        )
        results = router.handle_dml_event(DMLPayload(table="parcels"))
        assert results == []

    def test_matched_predicate_dispatches_actions(self, dispatcher, hub):
        evaluator = FakeEvaluator(results=[EvalResult(matched=True, eval_time_ms=1.2)])
        action = ActionDef(action_type=ActionType.NOTIFY, config={"ch": "alerts"})
        trigger = _make_trigger(predicates=[FakePredicate()], actions=[action])
        router = _make_router(evaluator, dispatcher, hub, triggers=[trigger])

        results = router.handle_dml_event(
            DMLPayload(table="parcels", operation="INSERT", row_id=str(uuid4()))
        )
        assert len(results) == 1
        assert results[0].matched is True
        assert len(dispatcher.dispatched) == 1
        actions, context = dispatcher.dispatched[0]
        assert actions == [action]
        assert context.table == "parcels"
        assert context.operation == "INSERT"

    def test_matched_predicate_broadcasts_to_hub(self, dispatcher, hub):
        evaluator = FakeEvaluator(results=[
            EvalResult(matched=True, transition=Transition.ENTER, eval_time_ms=0.5)
        ])
        trigger = _make_trigger(
            predicates=[FakePredicate()],
            actions=[ActionDef(action_type=ActionType.NOTIFY)],
        )
        router = _make_router(evaluator, dispatcher, hub, triggers=[trigger])
        router.handle_dml_event(DMLPayload(table="parcels", operation="INSERT"))

        assert len(hub.events) == 1
        event_type, data = hub.events[0]
        assert event_type == "trigger_fired"
        assert data["trigger_name"] == "t"
        assert data["matched"] is True
        assert data["transition"] == "ENTER"

    def test_unmatched_predicate_does_not_dispatch(self, dispatcher, hub):
        evaluator = FakeEvaluator(results=[EvalResult(matched=False)])
        trigger = _make_trigger(
            predicates=[FakePredicate()],
            actions=[ActionDef(action_type=ActionType.NOTIFY)],
        )
        router = _make_router(evaluator, dispatcher, hub, triggers=[trigger])
        results = router.handle_dml_event(DMLPayload(table="parcels"))
        assert results == []
        assert dispatcher.dispatched == []

    def test_legacy_trigger_without_predicates_uses_legacy_path(
        self, evaluator, dispatcher, hub
    ):
        action = ActionDef(action_type=ActionType.RUN_JOB, config={"rule_id": "r-1"})
        trigger = _make_trigger(predicates=[], actions=[action], rule_id=uuid4())
        router = _make_router(evaluator, dispatcher, hub, triggers=[trigger])
        router.handle_dml_event(
            DMLPayload(table="parcels", operation="UPDATE", row_id="row-1")
        )
        # Legacy path still dispatches
        assert len(dispatcher.dispatched) == 1
        assert dispatcher.dispatched[0][0] == [action]


class TestPredicateLogic:
    def test_and_logic_all_must_match(self, dispatcher, hub):
        evaluator = FakeEvaluator(results=[
            EvalResult(matched=True),
            EvalResult(matched=False),  # one fails → AND fails
        ])
        trigger = _make_trigger(
            predicates=[FakePredicate("a"), FakePredicate("b")],
            logic="AND",
            actions=[ActionDef(action_type=ActionType.NOTIFY)],
        )
        router = _make_router(evaluator, dispatcher, hub, triggers=[trigger])
        results = router.handle_dml_event(DMLPayload(table="t"))
        assert results == []

    def test_or_logic_any_match_succeeds(self, dispatcher, hub):
        evaluator = FakeEvaluator(results=[
            EvalResult(matched=False),
            EvalResult(matched=True),
        ])
        trigger = _make_trigger(
            predicates=[FakePredicate("a"), FakePredicate("b")],
            logic="OR",
            actions=[ActionDef(action_type=ActionType.NOTIFY)],
        )
        router = _make_router(evaluator, dispatcher, hub, triggers=[trigger])
        results = router.handle_dml_event(DMLPayload(table="t"))
        assert len(results) == 1
        assert results[0].matched is True


class TestDefaultActions:
    def test_rule_id_fallback_to_run_job_action(self, dispatcher, hub):
        """When a trigger has predicates + matching but no explicit actions,
        the router synthesises a RUN_JOB action from rule_id."""
        evaluator = FakeEvaluator(results=[EvalResult(matched=True)])
        rule_id = uuid4()
        trigger = _make_trigger(
            predicates=[FakePredicate()],
            actions=[],  # empty
            rule_id=rule_id,
        )
        router = _make_router(evaluator, dispatcher, hub, triggers=[trigger])
        router.handle_dml_event(DMLPayload(table="t"))
        actions, _ = dispatcher.dispatched[0]
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.RUN_JOB
        assert actions[0].config["rule_id"] == str(rule_id)

    def test_no_rule_id_no_default_actions(self, dispatcher, hub):
        evaluator = FakeEvaluator(results=[EvalResult(matched=True)])
        trigger = _make_trigger(predicates=[FakePredicate()], actions=[], rule_id=None)
        router = _make_router(evaluator, dispatcher, hub, triggers=[trigger])
        router.handle_dml_event(DMLPayload(table="t"))
        actions, _ = dispatcher.dispatched[0]
        assert actions == []


# ---------------------------------------------------------------------------
# CRON events
# ---------------------------------------------------------------------------


class TestHandleCronEvent:
    def test_dispatches_matched_unconditionally(self, evaluator, dispatcher, hub):
        router = _make_router(evaluator, dispatcher, hub)
        action = ActionDef(action_type=ActionType.RUN_JOB, config={"job": "nightly"})
        trigger = _make_trigger(actions=[action])
        router.handle_cron_event(trigger)
        assert len(dispatcher.dispatched) == 1
        actions, context = dispatcher.dispatched[0]
        assert context.operation == "CRON"
        assert context.eval_result.matched is True

    def test_uses_default_actions_when_missing(self, evaluator, dispatcher, hub):
        router = _make_router(evaluator, dispatcher, hub)
        rule_id = uuid4()
        trigger = _make_trigger(actions=[], rule_id=rule_id)
        router.handle_cron_event(trigger)
        actions, _ = dispatcher.dispatched[0]
        assert actions[0].config["rule_id"] == str(rule_id)


# ---------------------------------------------------------------------------
# API / manual events
# ---------------------------------------------------------------------------


class TestHandleApiEvent:
    def test_no_predicates_always_dispatches(self, evaluator, dispatcher, hub):
        router = _make_router(evaluator, dispatcher, hub)
        action = ActionDef(action_type=ActionType.NOTIFY)
        trigger = _make_trigger(predicates=[], actions=[action])
        result = router.handle_api_event(trigger, data={"table": "zones"})
        assert result.matched is True
        assert len(dispatcher.dispatched) == 1

    def test_with_predicates_evaluates_them(self, dispatcher, hub):
        evaluator = FakeEvaluator(results=[EvalResult(matched=True, eval_time_ms=0.1)])
        trigger = _make_trigger(
            predicates=[FakePredicate()],
            actions=[ActionDef(action_type=ActionType.NOTIFY)],
        )
        router = _make_router(evaluator, dispatcher, hub, triggers=[])
        result = router.handle_api_event(
            trigger, data={"table": "zones", "attrs": {"x": 1}}
        )
        assert result.matched is True
        assert len(dispatcher.dispatched) == 1

    def test_unmatched_predicate_returns_but_does_not_dispatch(self, dispatcher, hub):
        evaluator = FakeEvaluator(results=[EvalResult(matched=False)])
        trigger = _make_trigger(
            predicates=[FakePredicate()],
            actions=[ActionDef(action_type=ActionType.NOTIFY)],
        )
        router = _make_router(evaluator, dispatcher, hub)
        result = router.handle_api_event(trigger, data={})
        assert result.matched is False
        assert dispatcher.dispatched == []

    def test_geom_wkt_is_parsed(self, dispatcher, hub):
        """Shapely must parse the incoming WKT — catches geometry regressions."""
        evaluator = FakeEvaluator(results=[EvalResult(matched=True)])
        trigger = _make_trigger(
            predicates=[FakePredicate()],
            actions=[ActionDef(action_type=ActionType.NOTIFY)],
        )
        router = _make_router(evaluator, dispatcher, hub)
        router.handle_api_event(trigger, data={"geom_wkt": "POINT(2.3 48.8)", "attrs": {}})
        # evaluator was called with a parsed geometry
        assert evaluator.calls[0]["new_geom"] is not None


# ---------------------------------------------------------------------------
# handle_notify (raw pg_notify parsing)
# ---------------------------------------------------------------------------


class TestHandleNotify:
    def test_bad_json_returns_empty(self, evaluator, dispatcher, hub):
        router = _make_router(evaluator, dispatcher, hub)
        assert router.handle_notify("not{json") == []

    def test_parses_full_payload(self, dispatcher, hub):
        evaluator = FakeEvaluator(results=[EvalResult(matched=True)])
        trigger = _make_trigger(
            predicates=[FakePredicate()],
            actions=[ActionDef(action_type=ActionType.NOTIFY)],
        )
        router = _make_router(evaluator, dispatcher, hub, triggers=[trigger])
        row_uuid = str(uuid4())
        payload = json.dumps({
            "table": "parcels",
            "schema": "public",
            "operation": "INSERT",
            "row_id": row_uuid,
            "trigger_id": str(uuid4()),
            "new_geom_wkt": "POINT(0 0)",
            "new_attrs": {"k": 1},
            "old_geom_wkt": "POINT(1 1)",
            "old_attrs": {"k": 0},
        })
        results = router.handle_notify(payload)
        assert len(results) == 1
        # Geometry was parsed and threaded through evaluator
        assert evaluator.calls[0]["new_geom"] is not None


# ---------------------------------------------------------------------------
# Stats counter
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_increment_across_events(self, dispatcher, hub):
        evaluator = FakeEvaluator(results=[
            EvalResult(matched=True),
            EvalResult(matched=False),
            EvalResult(matched=True),
        ])
        trigger = _make_trigger(
            predicates=[FakePredicate()],
            actions=[ActionDef(action_type=ActionType.NOTIFY)],
        )
        router = _make_router(evaluator, dispatcher, hub, triggers=[trigger])
        router.handle_dml_event(DMLPayload(table="t"))
        router.handle_dml_event(DMLPayload(table="t"))
        router.handle_dml_event(DMLPayload(table="t"))

        assert router.stats["events_received"] == 3
        assert router.stats["predicates_matched"] == 2
