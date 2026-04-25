"""Tests for extended core/models — RasterLayer, GeomPredicate, Trigger extensions."""

from __future__ import annotations


from core.models import (
    AttrPredicate,
    CompoundPredicate,
    ExecutionMode,
    GeomPredicate,
    RasterBand,
    RasterLayer,
    Trigger,
    TriggerType,
)


class TestRasterBand:
    def test_defaults(self):
        band = RasterBand(index=1)
        assert band.name is None
        assert band.nodata is None
        assert band.dtype is None

    def test_full(self):
        band = RasterBand(index=2, name="NDVI", nodata=-9999.0, min=0.0, max=1.0, dtype="float32")
        assert band.index == 2
        assert band.name == "NDVI"
        assert band.dtype == "float32"


class TestRasterLayer:
    def test_defaults(self):
        rl = RasterLayer(name="MNT", source="/data/mnt.tif", crs="EPSG:2154")
        assert rl.crs == "EPSG:2154"
        assert rl.bands == []
        assert rl.nodata is None

    def test_with_bands(self):
        rl = RasterLayer(
            name="sentinel",
            source="s2.tif",
            resolution=(10.0, 10.0),
            bands=[RasterBand(index=1, name="B4"), RasterBand(index=2, name="B8")],
        )
        assert len(rl.bands) == 2
        assert rl.bands[0].name == "B4"

    def test_bounds_default(self):
        rl = RasterLayer(name="x", source="x.tif")
        assert rl.bounds == (0.0, 0.0, 0.0, 0.0)


class TestExecutionMode:
    def test_values(self):
        assert ExecutionMode.SESSION == "session"
        assert ExecutionMode.PERSISTENT == "persistent"


class TestTriggerType:
    def test_values(self):
        assert TriggerType.DML == "dml"
        assert TriggerType.SCHEDULE == "schedule"
        assert TriggerType.API == "api"
        assert TriggerType.ESB_EVENT == "esb_event"


class TestTriggerExtended:
    def test_default_fields(self):
        t = Trigger(name="watch_cables")
        assert t.trigger_type == TriggerType.DML
        assert t.predicates == []
        assert t.predicate_logic == "AND"
        assert t.enabled is True

    def test_with_attr_predicate(self):
        pred = AttrPredicate(field="statut", op="eq", value="ACTIF")
        t = Trigger(
            name="watch_actif",
            trigger_type=TriggerType.DML,
            predicates=[pred],
            predicate_logic="AND",
        )
        assert len(t.predicates) == 1
        assert t.predicates[0].field == "statut"  # type: ignore

    def test_with_geom_predicate(self):
        pred = GeomPredicate(op="intersects", ref_table="public.zones_n2000")
        t = Trigger(name="watch_n2000", predicates=[pred])
        assert t.predicates[0].op == "intersects"  # type: ignore

    def test_compound_predicates(self):
        p1 = AttrPredicate(field="statut", op="eq", value="ACTIF")
        p2 = GeomPredicate(op="within", ref_table="public.communes", ref_filter="dept='35'")
        compound = CompoundPredicate(logic="AND", predicates=[p1, p2])
        t = Trigger(name="watch_bretagne_actif", predicates=[compound], predicate_logic="OR")
        assert t.predicate_logic == "OR"
        assert isinstance(t.predicates[0], CompoundPredicate)


class TestGeomPredicate:
    def test_defaults(self):
        pred = GeomPredicate(op="intersects", ref_table="public.zones")
        assert pred.ref_geom_col == "geom"
        assert pred.ref_filter is None
        assert pred.distance is None
        assert pred.buffer_m is None

    def test_distance_op(self):
        pred = GeomPredicate(op="distance_lt", ref_table="public.routes", distance=50.0)
        assert pred.distance == 50.0

    def test_with_buffer(self):
        pred = GeomPredicate(op="intersects", ref_table="public.zones", buffer_m=100.0)
        assert pred.buffer_m == 100.0


class TestAttrPredicate:
    def test_fields(self):
        pred = AttrPredicate(field="longueur", op="gt", value=100)
        assert pred.field == "longueur"
        assert pred.op == "gt"
        assert pred.value == 100


class TestCompoundPredicate:
    def test_and(self):
        p1 = AttrPredicate(field="a", op="eq", value=1)
        p2 = AttrPredicate(field="b", op="eq", value=2)
        c = CompoundPredicate(logic="AND", predicates=[p1, p2])
        assert c.logic == "AND"
        assert len(c.predicates) == 2

    def test_not(self):
        p = AttrPredicate(field="x", op="eq", value=0)
        c = CompoundPredicate(logic="NOT", predicates=[p])
        assert c.logic == "NOT"
