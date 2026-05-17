"""Tests for TriggerEvaluator (P-6 #76)."""
from __future__ import annotations



from gispulse.core.models import ChangeOperation, ChangeRecord, Trigger, TriggerEvent, TriggerType
from gispulse.rules.trigger_evaluator import TriggerEvaluator


def _trigger(name: str = "t1", conditions: dict | None = None, enabled: bool = True) -> Trigger:
    return Trigger(
        name=name,
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions=conditions or {},
        enabled=enabled,
    )


def _record(
    table: str = "sess_abc.parcelles",
    operation: ChangeOperation = ChangeOperation.INSERT,
    session_id: str = "sess_abc",
) -> ChangeRecord:
    return ChangeRecord(
        session_id=session_id,
        table_name=table,
        operation=operation,
    )


class TestTriggerEvaluatorBasic:
    def test_no_conditions_always_matches(self):
        ev = TriggerEvaluator()
        rec = _record()
        t = _trigger(conditions={})
        results = ev.evaluate(rec, [t])
        assert len(results) == 1
        assert results[0].matched is True

    def test_disabled_trigger_skipped(self):
        ev = TriggerEvaluator()
        rec = _record()
        t = _trigger(enabled=False)
        results = ev.evaluate(rec, [t])
        assert results == []

    def test_table_condition_match(self):
        ev = TriggerEvaluator()
        rec = _record(table="sess_abc.parcelles")
        t = _trigger(conditions={"table": "sess_abc.parcelles"})
        results = ev.evaluate(rec, [t])
        assert results[0].matched is True

    def test_table_condition_no_match(self):
        ev = TriggerEvaluator()
        rec = _record(table="sess_abc.routes")
        t = _trigger(conditions={"table": "sess_abc.parcelles"})
        results = ev.evaluate(rec, [t])
        assert results[0].matched is False

    def test_operation_condition_match(self):
        ev = TriggerEvaluator()
        rec = _record(operation=ChangeOperation.UPDATE)
        t = _trigger(conditions={"operation": "UPDATE"})
        results = ev.evaluate(rec, [t])
        assert results[0].matched is True

    def test_operation_condition_no_match(self):
        ev = TriggerEvaluator()
        rec = _record(operation=ChangeOperation.DELETE)
        t = _trigger(conditions={"operation": "INSERT"})
        results = ev.evaluate(rec, [t])
        assert results[0].matched is False

    def test_session_id_condition_match(self):
        ev = TriggerEvaluator()
        rec = _record(session_id="sess_xyz")
        t = _trigger(conditions={"session_id": "sess_xyz"})
        results = ev.evaluate(rec, [t])
        assert results[0].matched is True

    def test_combined_conditions_all_match(self):
        ev = TriggerEvaluator()
        rec = _record(table="sess_abc.parcelles", operation=ChangeOperation.INSERT, session_id="sess_abc")
        t = _trigger(conditions={"table": "sess_abc.parcelles", "operation": "INSERT", "session_id": "sess_abc"})
        results = ev.evaluate(rec, [t])
        assert results[0].matched is True

    def test_combined_conditions_partial_fail(self):
        ev = TriggerEvaluator()
        rec = _record(table="sess_abc.parcelles", operation=ChangeOperation.DELETE)
        t = _trigger(conditions={"table": "sess_abc.parcelles", "operation": "INSERT"})
        results = ev.evaluate(rec, [t])
        assert results[0].matched is False

    def test_eval_time_ms_populated(self):
        ev = TriggerEvaluator()
        rec = _record()
        t = _trigger()
        results = ev.evaluate(rec, [t])
        assert results[0].eval_time_ms >= 0.0

    def test_multiple_triggers(self):
        ev = TriggerEvaluator()
        rec = _record(table="sess_abc.parcelles")
        t1 = _trigger("match", conditions={"table": "sess_abc.parcelles"})
        t2 = _trigger("no_match", conditions={"table": "sess_abc.routes"})
        results = ev.evaluate(rec, [t1, t2])
        assert len(results) == 2
        assert results[0].matched is True
        assert results[1].matched is False

    def test_evaluate_changeset_records(self):
        ev = TriggerEvaluator()
        records = [_record(table="t1"), _record(table="t2")]
        t = _trigger(conditions={"table": "t1"})
        results = ev.evaluate_changeset_records(records, [t])
        assert len(results) == 2
        matched = [r for r in results if r.matched]
        assert len(matched) == 1
