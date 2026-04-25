"""Unit tests for the vector capabilities added in the R1 enrichment sprint.

Covers:
- Buffer styles (quad_segs, cap_style, join_style, mitre_limit, single_sided)
- SimplifyCapability
- MakeValidCapability
- ConvexHullCapability
- EnvelopeCapability
- NearestNeighborCapability
- Extended geometric predicates (covers, covered_by, disjoint, equals, dwithin)
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import LinearRing

from capabilities.registry import get as get_capability
from capabilities.vector import (
    BufferCapability,
    ConvexHullCapability,
    EnvelopeCapability,
    MakeValidCapability,
    NearestNeighborCapability,
    SimplifyCapability,
)
from core.predicates import GeomPredicate
from rules.predicates import ShapelyPredicateEvaluator, _build_geom_sql
from rules.validation import _validate_predicate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def metric_polygons() -> gpd.GeoDataFrame:
    """Two axis-aligned polygons in EPSG:2154 (metric)."""
    p1 = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    p2 = Polygon([(200, 200), (300, 200), (300, 300), (200, 300)])
    return gpd.GeoDataFrame({"id": [1, 2], "geometry": [p1, p2]}, crs="EPSG:2154")


@pytest.fixture
def metric_line() -> gpd.GeoDataFrame:
    """A single horizontal line 0-100 meters in EPSG:2154."""
    return gpd.GeoDataFrame(
        {"id": [1], "geometry": [LineString([(0, 0), (100, 0)])]},
        crs="EPSG:2154",
    )


@pytest.fixture
def metric_points() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3], "geometry": [Point(0, 0), Point(10, 0), Point(100, 0)]},
        crs="EPSG:2154",
    )


# ---------------------------------------------------------------------------
# Buffer styles
# ---------------------------------------------------------------------------


class TestBufferStyles:

    def test_buffer_default_round_cap_on_line(self, metric_line):
        result = BufferCapability().execute(
            metric_line, distance=10.0, crs_meters="EPSG:2154"
        )
        # round cap → rounded ends → bounds extend slightly beyond [-10, 110]
        minx, _, maxx, _ = result.geometry.iloc[0].bounds
        assert minx < -9.99 and maxx > 109.99

    def test_buffer_flat_cap_on_line(self, metric_line):
        result = BufferCapability().execute(
            metric_line, distance=10.0, cap_style="flat", crs_meters="EPSG:2154"
        )
        minx, _, maxx, _ = result.geometry.iloc[0].bounds
        # flat caps → no extension along line
        assert -0.01 <= minx <= 0.01
        assert 99.99 <= maxx <= 100.01

    def test_buffer_square_cap_on_line(self, metric_line):
        result = BufferCapability().execute(
            metric_line, distance=10.0, cap_style="square", crs_meters="EPSG:2154"
        )
        minx, _, maxx, _ = result.geometry.iloc[0].bounds
        # square caps → extend by exactly `distance`
        assert pytest.approx(minx, abs=0.01) == -10.0
        assert pytest.approx(maxx, abs=0.01) == 110.0

    def test_buffer_invalid_cap_style_raises(self, metric_polygons):
        with pytest.raises(ValueError, match="cap_style"):
            BufferCapability().execute(
                metric_polygons, distance=5.0, cap_style="triangle"
            )

    def test_buffer_invalid_join_style_raises(self, metric_polygons):
        with pytest.raises(ValueError, match="join_style"):
            BufferCapability().execute(
                metric_polygons, distance=5.0, join_style="angular"
            )

    def test_buffer_invalid_quad_segs_raises(self, metric_polygons):
        with pytest.raises(ValueError, match="quad_segs"):
            BufferCapability().execute(
                metric_polygons, distance=5.0, quad_segs=0
            )

    def test_buffer_mitre_join_preserves_corners(self, metric_polygons):
        """Mitre joins produce sharper corners than round with coarser quad_segs."""
        round_buf = BufferCapability().execute(
            metric_polygons, distance=10.0, quad_segs=2, crs_meters="EPSG:2154"
        )
        mitre_buf = BufferCapability().execute(
            metric_polygons,
            distance=10.0,
            join_style="mitre",
            crs_meters="EPSG:2154",
        )
        # mitre produces corners → more vertices per ring OR larger area
        # (since corners aren't rounded off)
        assert mitre_buf.geometry.iloc[0].area >= round_buf.geometry.iloc[0].area

    def test_buffer_single_sided_line(self, metric_line):
        """Single-sided buffer on a line is asymmetric."""
        result = BufferCapability().execute(
            metric_line,
            distance=10.0,
            single_sided=True,
            cap_style="flat",
            crs_meters="EPSG:2154",
        )
        _, miny, _, maxy = result.geometry.iloc[0].bounds
        # Single-sided → ~half the two-sided area on one side of y=0
        # Positive distance offsets to the left (increasing y for this line).
        assert abs((maxy - miny)) <= 10.5

    def test_buffer_per_feature_distance_col(self, metric_polygons):
        metric_polygons["buf_m"] = [5.0, 20.0]
        result = BufferCapability().execute(
            metric_polygons, distance_col="buf_m", crs_meters="EPSG:2154"
        )
        a1 = result.geometry.iloc[0].area
        a2 = result.geometry.iloc[1].area
        # Polygon 2 has a 4x larger buffer and should have a larger area
        assert a2 > a1

    def test_buffer_schema_exposes_new_params(self):
        schema = BufferCapability().get_schema()
        props = schema["properties"]
        assert "quad_segs" in props
        assert "cap_style" in props and props["cap_style"]["enum"] == [
            "round", "flat", "square",
        ]
        assert "join_style" in props and set(props["join_style"]["enum"]) == {
            "round", "mitre", "bevel",
        }
        assert "mitre_limit" in props
        assert "single_sided" in props


# ---------------------------------------------------------------------------
# SimplifyCapability
# ---------------------------------------------------------------------------


class TestSimplify:

    def test_simplify_reduces_vertex_count(self, metric_line):
        # Add zig-zag noise to the line
        zigzag = LineString([(i, (i % 2) * 0.1) for i in range(0, 101, 2)])
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [zigzag]}, crs="EPSG:2154")
        original_pts = len(list(gdf.geometry.iloc[0].coords))
        result = SimplifyCapability().execute(
            gdf, tolerance=5.0, crs_meters="EPSG:2154"
        )
        simplified_pts = len(list(result.geometry.iloc[0].coords))
        assert simplified_pts < original_pts

    def test_simplify_tolerance_required_positive(self, metric_polygons):
        with pytest.raises(ValueError, match="tolerance"):
            SimplifyCapability().execute(metric_polygons, tolerance=0)

    def test_simplify_preserves_crs(self, metric_polygons):
        result = SimplifyCapability().execute(
            metric_polygons, tolerance=1.0, crs_meters="EPSG:2154"
        )
        assert result.crs == metric_polygons.crs

    def test_simplify_empty_gdf(self):
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:2154")
        result = SimplifyCapability().execute(empty, tolerance=1.0)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# MakeValidCapability
# ---------------------------------------------------------------------------


class TestMakeValid:

    def test_make_valid_repairs_bowtie(self):
        # Self-intersecting bowtie polygon
        bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
        assert not bowtie.is_valid
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [bowtie]}, crs="EPSG:2154"
        )
        result = MakeValidCapability().execute(gdf)
        assert len(result) == 1
        assert result.geometry.iloc[0].is_valid

    def test_make_valid_keep_geom_type_drops_degenerated(self):
        # Collinear triangle degenerates to a line when repaired
        degenerate = Polygon([(0, 0), (5, 0), (10, 0)])
        valid = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [degenerate, valid]}, crs="EPSG:2154"
        )
        result = MakeValidCapability().execute(gdf, keep_geom_type=True)
        # Only the valid square should survive
        assert set(result["id"]) == {2}

    def test_make_valid_drop_empty(self):
        empty_poly = Polygon()
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [empty_poly]}, crs="EPSG:2154"
        )
        result = MakeValidCapability().execute(gdf, drop_empty=True)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# ConvexHull + Envelope
# ---------------------------------------------------------------------------


class TestConvexHull:

    def test_convex_hull_per_feature(self):
        # Multi-vertex polygon with a concavity
        concave = Polygon([(0, 0), (10, 0), (10, 10), (5, 5), (0, 10)])
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [concave]}, crs="EPSG:2154"
        )
        result = ConvexHullCapability().execute(gdf)
        # Hull fills the concavity → area grows
        assert result.geometry.iloc[0].area > concave.area

    def test_convex_hull_dissolve(self, metric_polygons):
        result = ConvexHullCapability().execute(metric_polygons, dissolve=True)
        assert len(result) == 1
        # The hull over both 100x100 squares should be larger than each
        assert result.geometry.iloc[0].area > 100 * 100

    def test_convex_hull_by_group(self):
        polys = [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
            Polygon([(10, 10), (11, 10), (11, 11), (10, 11)]),
        ]
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2, 3], "zone": ["A", "A", "B"], "geometry": polys},
            crs="EPSG:2154",
        )
        result = ConvexHullCapability().execute(gdf, by_group="zone")
        # One hull per zone → 2 rows
        assert len(result) == 2


class TestEnvelope:

    def test_envelope_per_feature(self):
        concave = Polygon([(0, 0), (10, 0), (10, 10), (5, 5), (0, 10)])
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [concave]}, crs="EPSG:2154"
        )
        result = EnvelopeCapability().execute(gdf)
        # Envelope is the axis-aligned bounding rectangle (10x10 = 100)
        assert pytest.approx(result.geometry.iloc[0].area) == 100.0

    def test_envelope_dissolve(self, metric_polygons):
        result = EnvelopeCapability().execute(metric_polygons, dissolve=True)
        assert len(result) == 1
        # Bounding box over [0,0]-[300,300] = 300x300
        assert pytest.approx(result.geometry.iloc[0].area) == 300.0 * 300.0


# ---------------------------------------------------------------------------
# NearestNeighborCapability
# ---------------------------------------------------------------------------


class TestNearestNeighbor:

    def test_nearest_neighbor_k1(self, metric_points):
        # Reference: a point near id=2
        ref = gpd.GeoDataFrame(
            {"name": ["ref"], "geometry": [Point(12, 0)]},
            crs="EPSG:2154",
        )
        result = NearestNeighborCapability().execute(
            metric_points, ref_gdf=ref, k=1, crs_meters="EPSG:2154"
        )
        # Each input point should have a distance
        assert "nn_distance" in result.columns
        assert len(result) == 3
        # Point id=2 at (10,0) should be closest → distance = 2
        point_2 = result[result["id"] == 2].iloc[0]
        assert pytest.approx(point_2["nn_distance"], abs=0.01) == 2.0

    def test_nearest_neighbor_max_distance_filter(self, metric_points):
        ref = gpd.GeoDataFrame(
            {"name": ["ref"], "geometry": [Point(12, 0)]},
            crs="EPSG:2154",
        )
        result = NearestNeighborCapability().execute(
            metric_points,
            ref_gdf=ref,
            k=1,
            max_distance=5.0,
            crs_meters="EPSG:2154",
        )
        # Only points within 5m of (12, 0) → id=2 (dist=2)
        assert set(result["id"]) == {2}

    def test_nearest_neighbor_missing_ref_raises(self, metric_points):
        with pytest.raises(ValueError, match="reference layer"):
            NearestNeighborCapability().execute(metric_points)

    def test_nearest_neighbor_empty_input(self):
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:2154")
        ref = gpd.GeoDataFrame(
            {"geometry": [Point(0, 0)]}, crs="EPSG:2154"
        )
        result = NearestNeighborCapability().execute(empty, ref_gdf=ref)
        assert len(result) == 0

    def test_nearest_neighbor_columns_filter(self, metric_points):
        ref = gpd.GeoDataFrame(
            {
                "name": ["r1", "r2"],
                "category": ["A", "B"],
                "geometry": [Point(0, 0), Point(100, 0)],
            },
            crs="EPSG:2154",
        )
        result = NearestNeighborCapability().execute(
            metric_points, ref_gdf=ref, columns=["name"], crs_meters="EPSG:2154",
        )
        # name should be joined, category should not
        assert "name" in result.columns
        assert "category" not in result.columns

    def test_nearest_neighbor_ref_without_crs_assumes_primary(self):
        # Regression: ref_gdf loaded from a multi-layer GPKG may come back
        # with CRS=None even though the primary input has one. Reprojecting
        # only the primary silently mixed metres with degrees and produced
        # ~6.88 M m constant distances (seen live on S5 Versailles).
        left = gpd.GeoDataFrame(
            {"id": [1]},
            geometry=[Point(2.130, 48.800)],
            crs="EPSG:4326",
        )
        ref_no_crs = gpd.GeoDataFrame(
            {"park_id": ["A"]},
            geometry=[Point(2.135, 48.805)],
        )
        result = NearestNeighborCapability().execute(
            left, ref_gdf=ref_no_crs, k=1, distance_col="d",
            columns=["park_id"], crs_meters="EPSG:2154",
        )
        # ~500 m apart in Versailles — should never exceed a few km
        assert result["d"].iloc[0] < 2000

    def test_nearest_neighbor_primary_without_crs_assumes_ref(self):
        # Regression: the BDTOPO Versailles GPKG ships `batiments` with
        # crs=None while `vegetation` has EPSG:4326. Before the fix, gdf
        # skipped reprojection (because gdf.crs was None), ref_gdf was
        # reprojected to Lambert93, distance mixed degrees×metres and
        # produced ~6.88 M m constants on every residential building.
        left_no_crs = gpd.GeoDataFrame(
            {"id": [1]},
            geometry=[Point(2.130, 48.800)],
        )
        ref = gpd.GeoDataFrame(
            {"park_id": ["A"]},
            geometry=[Point(2.135, 48.805)],
            crs="EPSG:4326",
        )
        result = NearestNeighborCapability().execute(
            left_no_crs, ref_gdf=ref, k=1, distance_col="d",
            columns=["park_id"], crs_meters="EPSG:2154",
        )
        assert result["d"].iloc[0] < 2000


# ---------------------------------------------------------------------------
# Extended GeomPredicate operators
# ---------------------------------------------------------------------------


class TestGeomPredicateExtended:

    def test_shapely_evaluator_covers(self):
        ev = ShapelyPredicateEvaluator(
            ref_loader=lambda t, f, c: [Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])]
        )
        pred = GeomPredicate(op="covers", ref_table="zones")
        # Large square covers a small inner point
        assert ev._eval_geom(pred, Polygon([(0, 0), (20, 0), (20, 20), (0, 20)]))

    def test_shapely_evaluator_covered_by(self):
        ev = ShapelyPredicateEvaluator(
            ref_loader=lambda t, f, c: [Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])]
        )
        pred = GeomPredicate(op="covered_by", ref_table="zones")
        # Small square is covered by a larger one
        assert ev._eval_geom(pred, Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]))

    def test_shapely_evaluator_disjoint(self):
        ev = ShapelyPredicateEvaluator(
            ref_loader=lambda t, f, c: [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]
        )
        pred = GeomPredicate(op="disjoint", ref_table="zones")
        assert ev._eval_geom(pred, Polygon([(10, 10), (11, 10), (11, 11), (10, 11)]))
        assert not ev._eval_geom(
            pred, Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
        )

    def test_shapely_evaluator_equals(self):
        ev = ShapelyPredicateEvaluator(
            ref_loader=lambda t, f, c: [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]
        )
        pred = GeomPredicate(op="equals", ref_table="zones")
        assert ev._eval_geom(pred, Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]))

    def test_shapely_evaluator_dwithin(self):
        ev = ShapelyPredicateEvaluator(
            ref_loader=lambda t, f, c: [Point(0, 0)]
        )
        pred = GeomPredicate(op="dwithin", ref_table="zones", distance=5.0)
        assert ev._eval_geom(pred, Point(3, 0))
        assert not ev._eval_geom(pred, Point(10, 0))

    def test_postgis_sql_dwithin_uses_st_dwithin(self):
        pred = GeomPredicate(op="dwithin", ref_table="zones", distance=50.0)
        sql, params = _build_geom_sql(pred, "POINT(0 0)", srid=4326)
        assert "ST_DWithin" in sql
        assert "::geography" in sql  # meters-correct distance
        assert params["distance"] == 50.0

    def test_postgis_sql_covers(self):
        pred = GeomPredicate(op="covers", ref_table="zones")
        sql, _ = _build_geom_sql(pred, "POLYGON((0 0,1 0,1 1,0 1,0 0))", srid=4326)
        assert "ST_Covers" in sql

    def test_postgis_sql_disjoint(self):
        pred = GeomPredicate(op="disjoint", ref_table="zones")
        sql, _ = _build_geom_sql(pred, "POINT(0 0)", srid=4326)
        assert "ST_Disjoint" in sql

    def test_validation_accepts_new_ops(self):
        for op in ("covers", "covered_by", "disjoint", "equals"):
            pred = GeomPredicate(op=op, ref_table="zones")
            errors = _validate_predicate(pred, index=0)
            assert errors == [], f"{op} should validate, got {errors}"

    def test_validation_dwithin_requires_distance(self):
        pred = GeomPredicate(op="dwithin", ref_table="zones")  # no distance
        errors = _validate_predicate(pred, index=0)
        assert any("distance" in e.field for e in errors)

    def test_validation_rejects_unknown_op(self):
        # bypass type checker — we intentionally build an invalid op
        pred = GeomPredicate(op="touches_elbow", ref_table="zones")  # type: ignore[arg-type]
        errors = _validate_predicate(pred, index=0)
        assert any("not valid" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:

    @pytest.mark.parametrize(
        "name",
        ["simplify", "make_valid", "convex_hull", "envelope", "nearest_neighbor"],
    )
    def test_new_capabilities_are_registered(self, name):
        cap = get_capability(name)
        assert cap is not None
        schema = cap.get_schema()
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"
