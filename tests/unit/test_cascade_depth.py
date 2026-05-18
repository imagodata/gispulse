"""Tests for cascade depth limiter (P-8 #86)."""
from __future__ import annotations

import pytest

from gispulse.core.models import ChangeOperation, ChangeRecord, FiredTrigger, Trigger, TriggerEvent, TriggerType
from gispulse.rules.trigger_evaluator import (
    MAX_CASCADE_DEPTH,
    CascadeDepthExceeded,
    TriggerEvaluator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trigger(name: str = "t", enabled: bool = True) -> Trigger:
    return Trigger(
        name=name,
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={},
        enabled=enabled,
    )


def _record(table: str = "sess.parcelles") -> ChangeRecord:
    return ChangeRecord(
        session_id="sess",
        table_name=table,
        operation=ChangeOperation.INSERT,
    )


# ---------------------------------------------------------------------------
# MAX_CASCADE_DEPTH constant
# ---------------------------------------------------------------------------


class TestCascadeDepthConstant:
    def test_max_depth_is_three(self):
        assert MAX_CASCADE_DEPTH == 3


# ---------------------------------------------------------------------------
# CascadeDepthExceeded exception
# ---------------------------------------------------------------------------


class TestCascadeDepthExceeded:
    def test_message_includes_depth(self):
        exc = CascadeDepthExceeded(4)
        assert "4" in str(exc)
        assert "3" in str(exc)

    def test_attributes(self):
        exc = CascadeDepthExceeded(5, max_depth=3)
        assert exc.depth == 5
        assert exc.max_depth == 3

    def test_is_exception(self):
        assert isinstance(CascadeDepthExceeded(4), Exception)


# ---------------------------------------------------------------------------
# evaluate() — depth parameter
# ---------------------------------------------------------------------------


class TestEvaluateDepth:
    def test_depth_1_ok(self):
        ev = TriggerEvaluator()
        results = ev.evaluate(_record(), [_trigger()], depth=1)
        assert len(results) == 1
        assert results[0].cascade_depth == 1

    def test_depth_2_ok(self):
        ev = TriggerEvaluator()
        results = ev.evaluate(_record(), [_trigger()], depth=2)
        assert results[0].cascade_depth == 2

    def test_depth_3_ok(self):
        ev = TriggerEvaluator()
        results = ev.evaluate(_record(), [_trigger()], depth=3)
        assert results[0].cascade_depth == 3

    def test_depth_4_raises(self):
        ev = TriggerEvaluator()
        with pytest.raises(CascadeDepthExceeded) as exc_info:
            ev.evaluate(_record(), [_trigger()], depth=4)
        assert exc_info.value.depth == 4

    def test_depth_10_raises(self):
        ev = TriggerEvaluator()
        with pytest.raises(CascadeDepthExceeded):
            ev.evaluate(_record(), [_trigger()], depth=10)

    def test_default_depth_is_1(self):
        ev = TriggerEvaluator()
        results = ev.evaluate(_record(), [_trigger()])
        assert results[0].cascade_depth == 1


# ---------------------------------------------------------------------------
# evaluate_changeset_records() — depth forwarding
# ---------------------------------------------------------------------------


class TestEvaluateChangesetDepth:
    def test_depth_forwarded_to_fired_triggers(self):
        ev = TriggerEvaluator()
        records = [_record("t1"), _record("t2")]
        results = ev.evaluate_changeset_records(records, [_trigger()], depth=2)
        assert all(ft.cascade_depth == 2 for ft in results)

    def test_depth_3_still_ok(self):
        ev = TriggerEvaluator()
        results = ev.evaluate_changeset_records([_record()], [_trigger()], depth=3)
        assert len(results) == 1

    def test_depth_4_raises(self):
        ev = TriggerEvaluator()
        with pytest.raises(CascadeDepthExceeded):
            ev.evaluate_changeset_records([_record()], [_trigger()], depth=4)


# ---------------------------------------------------------------------------
# evaluate_cascade() — full cascade orchestration
# ---------------------------------------------------------------------------


class TestEvaluateCascade:
    def test_single_level_no_cascade(self):
        """next_records_fn retourne [] — cascade s'arrête après 1 round."""
        ev = TriggerEvaluator()
        all_fired = ev.evaluate_cascade(
            initial_records=[_record()],
            triggers=[_trigger()],
            next_records_fn=lambda fired: [],
        )
        assert len(all_fired) == 1
        assert all_fired[0].cascade_depth == 1

    def test_two_levels(self):
        """next_records_fn retourne un record au niveau 1, vide au niveau 2."""
        ev = TriggerEvaluator()
        calls: list[int] = []

        def next_fn(fired: list[FiredTrigger]) -> list[ChangeRecord]:
            calls.append(len(fired))
            if len(calls) == 1:
                return [_record("cascade_level2")]
            return []

        all_fired = ev.evaluate_cascade(
            initial_records=[_record()],
            triggers=[_trigger()],
            next_records_fn=next_fn,
        )
        assert len(all_fired) == 2
        assert all_fired[0].cascade_depth == 1
        assert all_fired[1].cascade_depth == 2

    def test_three_levels_allowed(self):
        """3 niveaux = max autorisé, pas d'exception."""
        ev = TriggerEvaluator()
        call_count = [0]

        def next_fn(fired: list[FiredTrigger]) -> list[ChangeRecord]:
            call_count[0] += 1
            if call_count[0] < 3:  # 2 appels avec records = 3 niveaux au total
                return [_record(f"level_{call_count[0] + 1}")]
            return []

        all_fired = ev.evaluate_cascade(
            initial_records=[_record("level_1")],
            triggers=[_trigger()],
            next_records_fn=next_fn,
        )
        depths = [ft.cascade_depth for ft in all_fired]
        assert depths == [1, 2, 3]

    def test_four_levels_raises(self):
        """Si next_records_fn retourne des records au niveau 3, on dépasse le max."""
        ev = TriggerEvaluator()

        def next_fn(fired: list[FiredTrigger]) -> list[ChangeRecord]:
            # Retourne toujours un record — force une cascade infinie
            return [_record("infinite")]

        with pytest.raises(CascadeDepthExceeded) as exc_info:
            ev.evaluate_cascade(
                initial_records=[_record()],
                triggers=[_trigger()],
                next_records_fn=next_fn,
            )
        assert exc_info.value.depth == 4

    def test_cascade_stops_when_no_match(self):
        """Si aucun trigger ne match, next_records_fn n'est pas appelé."""
        ev = TriggerEvaluator()
        called = [False]

        def next_fn(fired: list[FiredTrigger]) -> list[ChangeRecord]:
            called[0] = True
            return [_record()]

        # Trigger avec condition qui ne match pas
        t = Trigger(
            name="no_match",
            event=TriggerEvent.DATA_CHANGED,
            trigger_type=TriggerType.DML,
            conditions={"table": "other_table"},
            enabled=True,
        )
        all_fired = ev.evaluate_cascade(
            initial_records=[_record("sess.parcelles")],
            triggers=[t],
            next_records_fn=next_fn,
        )
        assert not called[0]
        assert len(all_fired) == 1
        assert all_fired[0].matched is False

    def test_all_fired_collected_across_levels(self):
        """Vérifie que tous les FiredTrigger de tous les niveaux sont retournés."""
        ev = TriggerEvaluator()
        triggers = [_trigger("t1"), _trigger("t2")]
        call_count = [0]

        def next_fn(fired: list[FiredTrigger]) -> list[ChangeRecord]:
            call_count[0] += 1
            if call_count[0] == 1:
                return [_record("level2_a"), _record("level2_b")]
            return []

        all_fired = ev.evaluate_cascade(
            initial_records=[_record("level1")],
            triggers=triggers,
            next_records_fn=next_fn,
        )
        # 2 triggers × 1 record au niveau 1 = 2, + 2 triggers × 2 records au niveau 2 = 4
        assert len(all_fired) == 6
        level1 = [ft for ft in all_fired if ft.cascade_depth == 1]
        level2 = [ft for ft in all_fired if ft.cascade_depth == 2]
        assert len(level1) == 2
        assert len(level2) == 4
