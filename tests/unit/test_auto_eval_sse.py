"""Tests for P-8 #85 — auto_eval trigger + SSE evaluate endpoint."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4


from core.models import (
    ChangeOperation,
    ChangeRecord,
    FiredTrigger,
    Trigger,
    TriggerEvent,
    TriggerType,
)
from rules.trigger_evaluator import TriggerEvaluator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trigger(name: str = "t", auto_eval: bool = False, enabled: bool = True) -> Trigger:
    return Trigger(
        name=name,
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={},
        enabled=enabled,
        auto_eval=auto_eval,
    )


def _record(table: str = "sess.parcelles", op: ChangeOperation = ChangeOperation.INSERT) -> ChangeRecord:
    return ChangeRecord(
        session_id="sess",
        table_name=table,
        operation=op,
    )


# ---------------------------------------------------------------------------
# Trigger.auto_eval field
# ---------------------------------------------------------------------------


class TestAutoEvalField:
    def test_default_is_false(self):
        t = _trigger()
        assert t.auto_eval is False

    def test_can_set_true(self):
        t = _trigger(auto_eval=True)
        assert t.auto_eval is True

    def test_auto_eval_is_bool(self):
        t = Trigger(name="x", auto_eval=True)
        assert isinstance(t.auto_eval, bool)

    def test_auto_eval_independent_of_enabled(self):
        t = _trigger(auto_eval=True, enabled=False)
        assert t.auto_eval is True
        assert t.enabled is False


# ---------------------------------------------------------------------------
# TriggerEvaluator — basic evaluate (foundation for SSE endpoint)
# ---------------------------------------------------------------------------


class TestEvaluateForSSE:
    def test_evaluate_single_record_returns_fired_trigger(self):
        evaluator = TriggerEvaluator()
        t = _trigger(auto_eval=True)
        r = _record()
        fired = evaluator.evaluate(r, [t])
        assert len(fired) == 1
        assert isinstance(fired[0], FiredTrigger)

    def test_evaluate_matched_true_when_no_conditions(self):
        evaluator = TriggerEvaluator()
        t = _trigger(auto_eval=True)
        r = _record()
        fired = evaluator.evaluate(r, [t])
        assert fired[0].matched is True

    def test_evaluate_matched_false_when_table_mismatch(self):
        evaluator = TriggerEvaluator()
        t = _trigger(auto_eval=True)
        t.conditions = {"table": "other_table"}
        r = _record(table="sess.parcelles")
        fired = evaluator.evaluate(r, [t])
        assert fired[0].matched is False

    def test_evaluate_records_multiple(self):
        evaluator = TriggerEvaluator()
        t = _trigger(auto_eval=True)
        records = [_record(), _record(op=ChangeOperation.UPDATE), _record(op=ChangeOperation.DELETE)]
        fired = evaluator.evaluate_changeset_records(records, [t])
        assert len(fired) == 3
        assert all(ft.matched for ft in fired)

    def test_fired_trigger_has_eval_time_ms(self):
        evaluator = TriggerEvaluator()
        t = _trigger()
        r = _record()
        fired = evaluator.evaluate(r, [t])
        assert fired[0].eval_time_ms >= 0.0

    def test_fired_trigger_result_summary_contains_operation(self):
        evaluator = TriggerEvaluator()
        t = _trigger()
        r = _record(op=ChangeOperation.UPDATE)
        fired = evaluator.evaluate(r, [t])
        assert fired[0].result_summary["operation"] == "UPDATE"

    def test_fired_trigger_result_summary_contains_table(self):
        evaluator = TriggerEvaluator()
        t = _trigger()
        r = _record(table="sess.routes")
        fired = evaluator.evaluate(r, [t])
        assert fired[0].result_summary["table"] == "sess.routes"

    def test_disabled_trigger_not_evaluated(self):
        evaluator = TriggerEvaluator()
        t = _trigger(auto_eval=True, enabled=False)
        r = _record()
        fired = evaluator.evaluate(r, [t])
        assert fired == []

    def test_cascade_depth_default_is_1(self):
        evaluator = TriggerEvaluator()
        t = _trigger(auto_eval=True)
        r = _record()
        fired = evaluator.evaluate(r, [t])
        assert fired[0].cascade_depth == 1


# ---------------------------------------------------------------------------
# EventHub broadcast integration (unit test with mock)
# ---------------------------------------------------------------------------


class TestEventHubBroadcast:
    def test_broadcast_emits_trigger_fired_type(self):
        from gispulse.adapters.http.event_hub import EventHub

        hub = EventHub()
        q = hub.subscribe()

        fired_data = {
            "trigger_id": str(uuid4()),
            "matched": True,
            "eval_time_ms": 0.5,
        }
        hub.broadcast("trigger_fired", fired_data)

        assert not q.empty()
        payload = q.get_nowait()
        event = json.loads(payload)
        assert event["type"] == "trigger_fired"
        assert event["data"]["matched"] is True

    def test_broadcast_includes_timestamp(self):
        from gispulse.adapters.http.event_hub import EventHub

        hub = EventHub()
        q = hub.subscribe()
        hub.broadcast("trigger_fired", {"x": 1})
        payload = q.get_nowait()
        event = json.loads(payload)
        assert "timestamp" in event

    def test_broadcast_filters_by_trigger_id(self):
        """The SSE endpoint must filter trigger_fired by trigger_id."""
        from gispulse.adapters.http.event_hub import EventHub

        hub = EventHub()
        q = hub.subscribe()
        tid = str(uuid4())
        hub.broadcast("trigger_fired", {"trigger_id": tid, "matched": True})
        hub.broadcast("trigger_fired", {"trigger_id": str(uuid4()), "matched": False})

        events = []
        while not q.empty():
            events.append(json.loads(q.get_nowait()))

        # Simulate what SSE endpoint does: filter by trigger_id
        filtered = [e for e in events if e["data"].get("trigger_id") == tid]
        assert len(filtered) == 1
        assert filtered[0]["data"]["matched"] is True

    def test_unsubscribe_removes_queue(self):
        from gispulse.adapters.http.event_hub import EventHub

        hub = EventHub()
        q = hub.subscribe()
        assert hub.subscriber_count == 1
        hub.unsubscribe(q)
        assert hub.subscriber_count == 0

    def test_multiple_subscribers_all_receive(self):
        from gispulse.adapters.http.event_hub import EventHub

        hub = EventHub()
        q1, q2 = hub.subscribe(), hub.subscribe()
        hub.broadcast("trigger_fired", {"matched": True})
        assert not q1.empty()
        assert not q2.empty()


# ---------------------------------------------------------------------------
# Schema validation (ChangeRecordIn / FiredTriggerOut)
# ---------------------------------------------------------------------------


class TestEvaluateSchemas:
    def test_change_record_in_defaults(self):
        from gispulse.adapters.http.schemas import ChangeRecordIn

        r = ChangeRecordIn(table_name="sess.parcelles", operation="INSERT")
        assert r.session_id == ""
        assert r.feature_id is None
        assert r.old_values == {}
        assert r.new_values == {}

    def test_change_record_in_all_operations(self):
        from gispulse.adapters.http.schemas import ChangeRecordIn

        for op in ("INSERT", "UPDATE", "DELETE"):
            r = ChangeRecordIn(table_name="t", operation=op)
            assert r.operation == op

    def test_fired_trigger_out_fields(self):
        from gispulse.adapters.http.schemas import FiredTriggerOut

        ft = FiredTriggerOut(
            id=uuid4(),
            trigger_id=uuid4(),
            change_record_id=None,
            matched=True,
            actions_dispatched=["notify"],
            eval_time_ms=1.23,
            result_summary={"table": "t"},
            cascade_depth=1,
            fired_at=datetime.now(timezone.utc),
        )
        assert ft.matched is True
        assert ft.cascade_depth == 1

    def test_evaluate_request_accepts_multiple_records(self):
        from gispulse.adapters.http.schemas import ChangeRecordIn, EvaluateRequest

        req = EvaluateRequest(records=[
            ChangeRecordIn(table_name="t1", operation="INSERT"),
            ChangeRecordIn(table_name="t2", operation="UPDATE"),
        ])
        assert len(req.records) == 2

    def test_trigger_create_auto_eval_default_false(self):
        from gispulse.adapters.http.schemas import TriggerCreate

        tc = TriggerCreate(name="t", event="manual", trigger_type="api", conditions={})
        assert tc.auto_eval is False

    def test_trigger_response_auto_eval_field(self):
        from gispulse.adapters.http.schemas import TriggerResponse

        tr = TriggerResponse(
            id=uuid4(),
            name="t",
            event="manual",
            trigger_type="api",
            rule_id=None,
            conditions={},
            enabled=True,
            auto_eval=True,
        )
        assert tr.auto_eval is True
