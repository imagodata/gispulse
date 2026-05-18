"""Tests for rules.trigger_evaluator — type-specific handlers.

Complements test_trigger_evaluator (12 tests on basic DML dispatch) with
coverage of the 7 type handlers: threshold, validation, business_rule,
topology, spatial_constraint, composite, generic. Plus cascade limiting
and _compare helper.

Uses FakePostGIS to capture (sql, params) and return canned rows — no
real PostgreSQL needed.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from gispulse.core.models import ChangeRecord, Trigger, TriggerEvent, TriggerType
from gispulse.rules.trigger_evaluator import (
    MAX_CASCADE_DEPTH,
    CascadeDepthExceeded,
    TriggerEvaluator,
    _compare,
)


class FakePG:
    """PostGIS connection stub — records calls, returns canned rows."""

    def __init__(self, rows_queue: list[list[dict]] | None = None):
        self.calls: list[tuple] = []
        self._rows = rows_queue or []

    def execute(self, sql: str, params: tuple = ()):
        self.calls.append((sql, params))
        if self._rows:
            return self._rows.pop(0)
        return []


def _make_record(**kwargs) -> ChangeRecord:
    defaults = {
        "table_name": "parcels",
        "operation": "INSERT",
        "new_values": {"id": "row-1", "value": 100},
        "feature_id": "row-1",
    }
    defaults.update(kwargs)
    return ChangeRecord(**defaults)


def _make_trigger(
    *,
    trigger_type: TriggerType = TriggerType.DML,
    conditions: dict | None = None,
    enabled: bool = True,
) -> Trigger:
    return Trigger(
        id=uuid4(),
        name="t",
        event=TriggerEvent.MANUAL,
        trigger_type=trigger_type,
        conditions=conditions or {},
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# _compare helper
# ---------------------------------------------------------------------------


class TestCompare:
    @pytest.mark.parametrize(
        "value,op,threshold,expected",
        [
            (10, "gt", 5, True),
            (5, "gt", 10, False),
            (10, "gte", 10, True),
            (10, "lt", 20, True),
            (20, "lt", 10, False),
            (10, "lte", 10, True),
            (10, "eq", 10, True),
            (10, "eq", 11, False),
            (10, "neq", 11, True),
            (10, "neq", 10, False),
        ],
    )
    def test_operators(self, value, op, threshold, expected):
        assert _compare(value, op, threshold) is expected

    def test_unknown_op_returns_false(self):
        assert _compare(10, "INVALID", 5) is False


# ---------------------------------------------------------------------------
# THRESHOLD handler
# ---------------------------------------------------------------------------


class TestThresholdHandler:
    def test_no_postgis_returns_true(self):
        ev = TriggerEvaluator(postgis_conn=None)
        trig = _make_trigger(
            trigger_type=TriggerType.THRESHOLD,
            conditions={"threshold_value": 50, "operator": "gt"},
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is True  # no postgis → trivially match

    def test_feature_count_matches_when_gt_threshold(self):
        pg = FakePG(rows_queue=[[{"val": 100}]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.THRESHOLD,
            conditions={
                "metric": "feature_count",
                "operator": "gt",
                "threshold_value": 50,
                "table": "parcels",
            },
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is True
        assert "COUNT(*)" in pg.calls[0][0]

    def test_feature_count_no_match_when_below_threshold(self):
        pg = FakePG(rows_queue=[[{"val": 10}]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.THRESHOLD,
            conditions={
                "metric": "feature_count",
                "operator": "gt",
                "threshold_value": 50,
            },
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is False

    def test_total_area_metric(self):
        pg = FakePG(rows_queue=[[{"val": 5000.0}]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.THRESHOLD,
            conditions={
                "metric": "total_area",
                "operator": "gt",
                "threshold_value": 1000,
            },
        )
        ev.evaluate(_make_record(), [trig])
        assert "ST_Area" in pg.calls[0][0]

    def test_sum_value_uses_field(self):
        pg = FakePG(rows_queue=[[{"val": 42}]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.THRESHOLD,
            conditions={
                "metric": "sum_value",
                "field": "population",
                "operator": "gte",
                "threshold_value": 10,
            },
        )
        ev.evaluate(_make_record(), [trig])
        assert "SUM(population)" in pg.calls[0][0]

    def test_unsafe_table_silently_rejected(self):
        pg = FakePG(rows_queue=[])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.THRESHOLD,
            conditions={
                "metric": "feature_count",
                "operator": "gt",
                "threshold_value": 1,
                "table": "parcels; DROP TABLE x",
            },
        )
        result = ev.evaluate(_make_record(), [trig])
        # validation raises → handler catches → matched=False
        assert result[0].matched is False
        assert pg.calls == []

    def test_empty_rowset_returns_false(self):
        pg = FakePG(rows_queue=[[]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.THRESHOLD,
            conditions={"metric": "feature_count", "operator": "gt", "threshold_value": 1},
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is False


# ---------------------------------------------------------------------------
# VALIDATION handler
# ---------------------------------------------------------------------------


class TestValidationHandler:
    def test_no_rules_returns_true(self):
        ev = TriggerEvaluator()
        trig = _make_trigger(
            trigger_type=TriggerType.VALIDATION,
            conditions={"validation_rules": []},
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is True

    def test_not_null_rule_fires_on_missing_field(self):
        ev = TriggerEvaluator()
        trig = _make_trigger(
            trigger_type=TriggerType.VALIDATION,
            conditions={
                "validation_rules": [{"rule": "not_null", "field": "mandatory"}]
            },
        )
        # new_values has no 'mandatory' field
        rec = _make_record(new_values={"id": "x"})
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is True  # violation → trigger fires

    def test_not_null_rule_does_not_fire_when_field_present(self):
        ev = TriggerEvaluator()
        trig = _make_trigger(
            trigger_type=TriggerType.VALIDATION,
            conditions={
                "validation_rules": [{"rule": "not_null", "field": "name"}]
            },
        )
        rec = _make_record(new_values={"id": "x", "name": "ok"})
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is False

    def test_geometry_valid_rule_with_invalid_geom(self):
        pg = FakePG(rows_queue=[[{"valid": False}]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.VALIDATION,
            conditions={
                "validation_rules": [{"rule": "geometry_valid"}]
            },
        )
        rec = _make_record(new_geom_wkt="POLYGON((0 0, 1 1, 0 1, 1 0, 0 0))")
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is True


# ---------------------------------------------------------------------------
# BUSINESS_RULE handler
# ---------------------------------------------------------------------------


class TestBusinessRuleHandler:
    def test_empty_expression_returns_true(self):
        ev = TriggerEvaluator(postgis_conn=FakePG())
        trig = _make_trigger(
            trigger_type=TriggerType.BUSINESS_RULE,
            conditions={"expression": ""},
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is True

    def test_no_postgis_returns_true(self):
        ev = TriggerEvaluator(postgis_conn=None)
        trig = _make_trigger(
            trigger_type=TriggerType.BUSINESS_RULE,
            conditions={"expression": "area > 1000"},
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is True

    def test_no_feature_id_returns_true(self):
        ev = TriggerEvaluator(postgis_conn=FakePG())
        trig = _make_trigger(
            trigger_type=TriggerType.BUSINESS_RULE,
            conditions={"expression": "value > 0"},
        )
        rec = _make_record(feature_id=None, new_values={})
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is True  # Cannot evaluate → assume OK

    def test_violation_fires_trigger(self):
        pg = FakePG(rows_queue=[[{"violated": True}]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.BUSINESS_RULE,
            conditions={"expression": "area > 1000"},
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is True

    def test_no_violation_does_not_fire(self):
        pg = FakePG(rows_queue=[[{"violated": False}]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.BUSINESS_RULE,
            conditions={"expression": "area > 1000"},
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is False

    def test_unsafe_expression_silently_returns_false(self):
        pg = FakePG()
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.BUSINESS_RULE,
            conditions={"expression": "DROP TABLE users"},
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is False
        assert pg.calls == []


# ---------------------------------------------------------------------------
# TOPOLOGY handler
# ---------------------------------------------------------------------------


class TestTopologyHandler:
    def test_no_postgis_returns_true(self):
        ev = TriggerEvaluator(postgis_conn=None)
        trig = _make_trigger(
            trigger_type=TriggerType.TOPOLOGY,
            conditions={"topo_check": "no_overlap"},
        )
        rec = _make_record(new_geom_wkt="POINT(0 0)")
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is True

    def test_no_geom_returns_true(self):
        ev = TriggerEvaluator(postgis_conn=FakePG())
        trig = _make_trigger(
            trigger_type=TriggerType.TOPOLOGY,
            conditions={"topo_check": "no_overlap"},
        )
        result = ev.evaluate(_make_record(), [trig])  # no new_geom_wkt
        assert result[0].matched is True

    def test_no_overlap_violation_fires(self):
        pg = FakePG(rows_queue=[[{"violated": True}]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.TOPOLOGY,
            conditions={"topo_check": "no_overlap", "table": "parcels"},
        )
        rec = _make_record(new_geom_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is True
        assert "ST_Overlaps" in pg.calls[0][0]

    def test_must_be_inside_requires_ref_table(self):
        ev = TriggerEvaluator(postgis_conn=FakePG())
        trig = _make_trigger(
            trigger_type=TriggerType.TOPOLOGY,
            conditions={"topo_check": "must_be_inside"},  # no ref_table
        )
        rec = _make_record(new_geom_wkt="POINT(0 0)")
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is False

    def test_unknown_topo_check_returns_false(self):
        pg = FakePG()
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.TOPOLOGY,
            conditions={"topo_check": "unknown_check"},
        )
        rec = _make_record(new_geom_wkt="POINT(0 0)")
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is False


# ---------------------------------------------------------------------------
# SPATIAL_CONSTRAINT handler
# ---------------------------------------------------------------------------


class TestSpatialConstraintHandler:
    def test_no_postgis_returns_true(self):
        ev = TriggerEvaluator(postgis_conn=None)
        trig = _make_trigger(
            trigger_type=TriggerType.SPATIAL_CONSTRAINT,
            conditions={"ref_table": "zones", "spatial_type": "min_distance"},
        )
        rec = _make_record(new_geom_wkt="POINT(0 0)")
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is True

    def test_no_ref_table_returns_true(self):
        ev = TriggerEvaluator(postgis_conn=FakePG())
        trig = _make_trigger(
            trigger_type=TriggerType.SPATIAL_CONSTRAINT,
            conditions={"spatial_type": "min_distance"},
        )
        rec = _make_record(new_geom_wkt="POINT(0 0)")
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is True

    def test_min_distance_violated(self):
        pg = FakePG(rows_queue=[[{"violated": True}]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.SPATIAL_CONSTRAINT,
            conditions={
                "ref_table": "zones",
                "spatial_type": "min_distance",
                "distance": 100,
            },
        )
        rec = _make_record(new_geom_wkt="POINT(0 0)")
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is True
        assert "ST_Distance" in pg.calls[0][0]

    def test_exclusion_zone_intersects_fires(self):
        pg = FakePG(rows_queue=[[{"violated": True}]])
        ev = TriggerEvaluator(postgis_conn=pg)
        trig = _make_trigger(
            trigger_type=TriggerType.SPATIAL_CONSTRAINT,
            conditions={
                "ref_table": "protected",
                "spatial_type": "exclusion_zone",
            },
        )
        rec = _make_record(new_geom_wkt="POINT(0 0)")
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is True
        assert "ST_Intersects" in pg.calls[0][0]

    def test_unknown_spatial_type_returns_false(self):
        ev = TriggerEvaluator(postgis_conn=FakePG())
        trig = _make_trigger(
            trigger_type=TriggerType.SPATIAL_CONSTRAINT,
            conditions={
                "ref_table": "zones",
                "spatial_type": "nonexistent_type",
            },
        )
        rec = _make_record(new_geom_wkt="POINT(0 0)")
        result = ev.evaluate(rec, [trig])
        assert result[0].matched is False


# ---------------------------------------------------------------------------
# COMPOSITE handler
# ---------------------------------------------------------------------------


class TestCompositeHandler:
    def test_no_resolver_returns_true(self):
        ev = TriggerEvaluator(postgis_conn=FakePG(), trigger_resolver=None)
        trig = _make_trigger(
            trigger_type=TriggerType.COMPOSITE,
            conditions={"trigger_ids": ["a", "b"], "composite_mode": "all"},
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is True

    def test_empty_trigger_ids_returns_true(self):
        resolver = lambda tid: None  # noqa: E731
        ev = TriggerEvaluator(trigger_resolver=resolver)
        trig = _make_trigger(
            trigger_type=TriggerType.COMPOSITE,
            conditions={"trigger_ids": []},
        )
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is True

    def test_all_mode_requires_all_children_to_match(self):
        child_a = _make_trigger(trigger_type=TriggerType.DML)
        child_b = _make_trigger(
            trigger_type=TriggerType.DML,
            conditions={"table": "other_table"},  # Won't match INSERT on 'parcels'
        )
        resolver = {str(child_a.id): child_a, str(child_b.id): child_b}.get
        ev = TriggerEvaluator(trigger_resolver=resolver)
        parent = _make_trigger(
            trigger_type=TriggerType.COMPOSITE,
            conditions={
                "trigger_ids": [str(child_a.id), str(child_b.id)],
                "composite_mode": "all",
            },
        )
        result = ev.evaluate(_make_record(), [parent])
        assert result[0].matched is False  # child_b won't match

    def test_any_mode_requires_only_one_match(self):
        child_a = _make_trigger(trigger_type=TriggerType.DML)  # matches
        child_b = _make_trigger(
            trigger_type=TriggerType.DML,
            conditions={"table": "ghost"},  # won't match
        )
        resolver = {str(child_a.id): child_a, str(child_b.id): child_b}.get
        ev = TriggerEvaluator(trigger_resolver=resolver)
        parent = _make_trigger(
            trigger_type=TriggerType.COMPOSITE,
            conditions={
                "trigger_ids": [str(child_a.id), str(child_b.id)],
                "composite_mode": "any",
            },
        )
        result = ev.evaluate(_make_record(), [parent])
        assert result[0].matched is True


# ---------------------------------------------------------------------------
# Generic handlers (schedule, api, esb_event, webhook_in)
# ---------------------------------------------------------------------------


class TestGenericHandlers:
    @pytest.mark.parametrize(
        "trigger_type",
        [TriggerType.SCHEDULE, TriggerType.API, TriggerType.ESB_EVENT, TriggerType.WEBHOOK_IN],
    )
    def test_always_match(self, trigger_type):
        ev = TriggerEvaluator()
        trig = _make_trigger(trigger_type=trigger_type)
        result = ev.evaluate(_make_record(), [trig])
        assert result[0].matched is True


# ---------------------------------------------------------------------------
# Cascade depth limiter
# ---------------------------------------------------------------------------


class TestCascadeDepth:
    def test_evaluate_raises_when_depth_exceeds_limit(self):
        ev = TriggerEvaluator()
        trig = _make_trigger()
        with pytest.raises(CascadeDepthExceeded) as exc:
            ev.evaluate(_make_record(), [trig], depth=MAX_CASCADE_DEPTH + 1)
        assert exc.value.depth == MAX_CASCADE_DEPTH + 1
        assert exc.value.max_depth == MAX_CASCADE_DEPTH

    def test_error_message_mentions_circular(self):
        err = CascadeDepthExceeded(5)
        assert "circular" in str(err).lower() or "cascade" in str(err).lower()

    def test_evaluate_cascade_stops_when_no_matches(self):
        ev = TriggerEvaluator()
        # A trigger that won't match (wrong table)
        trig = _make_trigger(conditions={"table": "ghost"})
        records = [_make_record()]

        # next_records_fn should never be invoked since nothing fires
        def next_fn(fired):
            raise AssertionError("should not be called")

        results = ev.evaluate_cascade(records, [trig], next_fn)
        assert all(not ft.matched for ft in results)

    def test_evaluate_cascade_raises_when_max_depth_exceeded(self):
        ev = TriggerEvaluator()
        trig = _make_trigger()  # always matches (DML, no conditions)

        # next_fn keeps returning new records → cascade grows until limit
        def next_fn(fired):
            return [_make_record(feature_id=str(uuid4()))]

        with pytest.raises(CascadeDepthExceeded):
            ev.evaluate_cascade([_make_record()], [trig], next_fn)
