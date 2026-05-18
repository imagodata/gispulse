"""Tests for rules/predicates.py — PredicateEvaluator."""

from __future__ import annotations

import pytest

from gispulse.core.models import AttrPredicate, CompoundPredicate, GeomPredicate
from gispulse.rules.predicates import PredicateEvaluator


# ---------------------------------------------------------------------------
# AttrPredicate tests
# ---------------------------------------------------------------------------

@pytest.fixture
def evaluator() -> PredicateEvaluator:
    return PredicateEvaluator(postgis_conn=None)


class TestAttrPredicate:
    def test_eq_match(self, evaluator):
        pred = AttrPredicate(field="statut", op="eq", value="ACTIF")
        assert evaluator.evaluate([pred], payload={"statut": "ACTIF"}) is True

    def test_eq_no_match(self, evaluator):
        pred = AttrPredicate(field="statut", op="eq", value="ACTIF")
        assert evaluator.evaluate([pred], payload={"statut": "INACTIF"}) is False

    def test_neq(self, evaluator):
        pred = AttrPredicate(field="statut", op="neq", value="INACTIF")
        assert evaluator.evaluate([pred], payload={"statut": "ACTIF"}) is True

    def test_gt(self, evaluator):
        pred = AttrPredicate(field="longueur", op="gt", value=100)
        assert evaluator.evaluate([pred], payload={"longueur": 150}) is True
        assert evaluator.evaluate([pred], payload={"longueur": 50}) is False

    def test_gte(self, evaluator):
        pred = AttrPredicate(field="longueur", op="gte", value=100)
        assert evaluator.evaluate([pred], payload={"longueur": 100}) is True

    def test_lt(self, evaluator):
        pred = AttrPredicate(field="longueur", op="lt", value=100)
        assert evaluator.evaluate([pred], payload={"longueur": 50}) is True

    def test_lte(self, evaluator):
        pred = AttrPredicate(field="longueur", op="lte", value=100)
        assert evaluator.evaluate([pred], payload={"longueur": 100}) is True

    def test_in(self, evaluator):
        pred = AttrPredicate(field="type", op="in", value=["A", "B", "C"])
        assert evaluator.evaluate([pred], payload={"type": "B"}) is True
        assert evaluator.evaluate([pred], payload={"type": "D"}) is False

    def test_like(self, evaluator):
        pred = AttrPredicate(field="nom", op="like", value="cable%")
        assert evaluator.evaluate([pred], payload={"nom": "cable_nord"}) is True
        assert evaluator.evaluate([pred], payload={"nom": "fibre_sud"}) is False

    def test_missing_field_returns_false(self, evaluator):
        pred = AttrPredicate(field="absent", op="eq", value="x")
        assert evaluator.evaluate([pred], payload={"autre": "y"}) is False

    def test_type_error_returns_false(self, evaluator):
        pred = AttrPredicate(field="val", op="gt", value="abc")
        assert evaluator.evaluate([pred], payload={"val": 42}) is False


# ---------------------------------------------------------------------------
# CompoundPredicate tests
# ---------------------------------------------------------------------------

class TestCompoundPredicate:
    def test_and_both_true(self, evaluator):
        p1 = AttrPredicate(field="statut", op="eq", value="ACTIF")
        p2 = AttrPredicate(field="longueur", op="gt", value=100)
        compound = CompoundPredicate(logic="AND", predicates=[p1, p2])
        assert evaluator.evaluate([compound], payload={"statut": "ACTIF", "longueur": 200}) is True

    def test_and_one_false(self, evaluator):
        p1 = AttrPredicate(field="statut", op="eq", value="ACTIF")
        p2 = AttrPredicate(field="longueur", op="gt", value=100)
        compound = CompoundPredicate(logic="AND", predicates=[p1, p2])
        assert evaluator.evaluate([compound], payload={"statut": "ACTIF", "longueur": 50}) is False

    def test_or_one_true(self, evaluator):
        p1 = AttrPredicate(field="statut", op="eq", value="ACTIF")
        p2 = AttrPredicate(field="longueur", op="gt", value=100)
        compound = CompoundPredicate(logic="OR", predicates=[p1, p2])
        assert evaluator.evaluate([compound], payload={"statut": "INACTIF", "longueur": 200}) is True

    def test_or_both_false(self, evaluator):
        p1 = AttrPredicate(field="statut", op="eq", value="ACTIF")
        p2 = AttrPredicate(field="longueur", op="gt", value=100)
        compound = CompoundPredicate(logic="OR", predicates=[p1, p2])
        assert evaluator.evaluate([compound], payload={"statut": "INACTIF", "longueur": 50}) is False

    def test_not(self, evaluator):
        p1 = AttrPredicate(field="statut", op="eq", value="ACTIF")
        compound = CompoundPredicate(logic="NOT", predicates=[p1])
        assert evaluator.evaluate([compound], payload={"statut": "INACTIF"}) is True
        assert evaluator.evaluate([compound], payload={"statut": "ACTIF"}) is False

    def test_not_requires_single_child(self, evaluator):
        p1 = AttrPredicate(field="a", op="eq", value=1)
        p2 = AttrPredicate(field="b", op="eq", value=2)
        compound = CompoundPredicate(logic="NOT", predicates=[p1, p2])
        with pytest.raises(ValueError, match="NOT must have exactly 1"):
            evaluator.evaluate([compound], payload={"a": 1, "b": 2})

    def test_nested_compound(self, evaluator):
        # (statut == ACTIF AND longueur > 100) OR type == 'PRIORITAIRE'
        inner = CompoundPredicate(logic="AND", predicates=[
            AttrPredicate(field="statut", op="eq", value="ACTIF"),
            AttrPredicate(field="longueur", op="gt", value=100),
        ])
        outer = CompoundPredicate(logic="OR", predicates=[
            inner,
            AttrPredicate(field="type", op="eq", value="PRIORITAIRE"),
        ])
        assert evaluator.evaluate([outer], payload={"statut": "INACTIF", "longueur": 50, "type": "PRIORITAIRE"}) is True
        assert evaluator.evaluate([outer], payload={"statut": "ACTIF", "longueur": 200, "type": "NORMAL"}) is True
        assert evaluator.evaluate([outer], payload={"statut": "INACTIF", "longueur": 50, "type": "NORMAL"}) is False


# ---------------------------------------------------------------------------
# Top-level logic (AND / OR across multiple predicates)
# ---------------------------------------------------------------------------

class TestTopLevelLogic:
    def test_empty_predicates_always_true(self, evaluator):
        assert evaluator.evaluate([], payload={}) is True

    def test_top_level_and(self, evaluator):
        p1 = AttrPredicate(field="a", op="eq", value=1)
        p2 = AttrPredicate(field="b", op="eq", value=2)
        assert evaluator.evaluate([p1, p2], logic="AND", payload={"a": 1, "b": 2}) is True
        assert evaluator.evaluate([p1, p2], logic="AND", payload={"a": 1, "b": 99}) is False

    def test_top_level_or(self, evaluator):
        p1 = AttrPredicate(field="a", op="eq", value=1)
        p2 = AttrPredicate(field="b", op="eq", value=2)
        assert evaluator.evaluate([p1, p2], logic="OR", payload={"a": 99, "b": 2}) is True
        assert evaluator.evaluate([p1, p2], logic="OR", payload={"a": 99, "b": 99}) is False


# ---------------------------------------------------------------------------
# GeomPredicate — error when no connection
# ---------------------------------------------------------------------------

class TestGeomPredicateNoConn:
    def test_raises_without_connection(self, evaluator):
        pred = GeomPredicate(op="intersects", ref_table="public.zones_n2000")
        with pytest.raises(RuntimeError, match="PostGISConnection"):
            evaluator.evaluate([pred], payload={"geom": "POINT(2 48)"})


# ---------------------------------------------------------------------------
# GeomPredicate SQL generation
# ---------------------------------------------------------------------------

class TestBuildGeomSQL:
    def test_intersects_sql(self):
        from gispulse.rules.predicates import _build_geom_sql
        pred = GeomPredicate(op="intersects", ref_table="public.zones_n2000")
        sql, params = _build_geom_sql(pred, "POINT(2 48)", srid=4326)
        assert "ST_Intersects" in sql
        assert "zones_n2000" in sql
        assert params["geom_wkt"] == "POINT(2 48)"
        assert params["srid"] == 4326

    def test_within_with_filter(self):
        from gispulse.rules.predicates import _build_geom_sql
        pred = GeomPredicate(
            op="within",
            ref_table="public.communes",
            ref_filter="dept_code = 35",
        )
        sql, params = _build_geom_sql(pred, "POINT(2 48)", srid=4326)
        assert "ST_Within" in sql
        assert "dept_code = 35" in sql

    def test_distance_lt(self):
        from gispulse.rules.predicates import _build_geom_sql
        pred = GeomPredicate(op="distance_lt", ref_table="public.routes", distance=50.0)
        sql, params = _build_geom_sql(pred, "POINT(2 48)")
        assert "ST_Distance" in sql
        assert ":distance" in sql
        assert params["distance"] == 50.0

    def test_distance_gt_requires_distance(self):
        from gispulse.rules.predicates import _build_geom_sql
        pred = GeomPredicate(op="distance_gt", ref_table="public.routes", distance=None)
        with pytest.raises(ValueError, match="requires 'distance'"):
            _build_geom_sql(pred, "POINT(2 48)")

    def test_buffer_applied(self):
        from gispulse.rules.predicates import _build_geom_sql
        pred = GeomPredicate(op="intersects", ref_table="public.zones", buffer_m=100.0)
        sql, params = _build_geom_sql(pred, "POINT(2 48)")
        assert "ST_Buffer" in sql
        assert params["buffer_m"] == 100.0

    def test_unknown_op_raises(self):
        from gispulse.rules.predicates import _build_geom_sql
        pred = GeomPredicate(op="intersects", ref_table="t")  # type: ignore
        pred.op = "banana"  # type: ignore
        with pytest.raises(ValueError, match="Unknown GeomPredicate op"):
            _build_geom_sql(pred, "POINT(0 0)")

    def test_unsafe_ref_table_rejected(self):
        from gispulse.rules.predicates import _build_geom_sql
        pred = GeomPredicate(op="intersects", ref_table="t; DROP TABLE --")
        with pytest.raises(ValueError, match="(Unsafe|Invalid) SQL"):
            _build_geom_sql(pred, "POINT(0 0)")

    def test_unsafe_ref_filter_rejected(self):
        from gispulse.rules.predicates import _build_geom_sql
        pred = GeomPredicate(
            op="intersects",
            ref_table="public.zones",
            ref_filter="1=1; DROP TABLE zones--",
        )
        with pytest.raises(ValueError, match="Unsafe ref_filter"):
            _build_geom_sql(pred, "POINT(0 0)")
