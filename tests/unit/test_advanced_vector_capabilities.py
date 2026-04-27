"""Unit tests for the advanced geometry capabilities added in the R1 follow-up.

Covers:
- ConcaveHullCapability
- OffsetCurveCapability
- SnapToGridCapability
- LineMergeCapability
- PolygonizeCapability
- VoronoiPolygonsCapability
- DelaunayTriangulationCapability
- SimplifyCapability (algorithm=dp/vw/coverage)
- Clip + Intersects strategy registration
- Temporal AttrPredicate operators (age_gt, before, after, between)
"""

from __future__ import annotations

import datetime as dt
import time

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString, Point, Polygon

from capabilities.registry import get as get_capability
from capabilities.vector import (
    ClipCapability,
    ConcaveHullCapability,
    DelaunayTriangulationCapability,
    IntersectsCapability,
    LineMergeCapability,
    OffsetCurveCapability,
    PolygonizeCapability,
    SimplifyCapability,
    SnapToGridCapability,
    VoronoiPolygonsCapability,
)
from core.predicates import AttrPredicate
from rules.predicates import _eval_attr
from rules.validation import _validate_predicate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def metric_points() -> gpd.GeoDataFrame:
    coords = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
    return gpd.GeoDataFrame(
        {"id": list(range(len(coords))), "geometry": [Point(*c) for c in coords]},
        crs="EPSG:2154",
    )


@pytest.fixture
def metric_line() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1], "geometry": [LineString([(0, 0), (100, 0)])]},
        crs="EPSG:2154",
    )


@pytest.fixture
def metric_polygon() -> gpd.GeoDataFrame:
    p = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    return gpd.GeoDataFrame({"id": [1], "geometry": [p]}, crs="EPSG:2154")


# ---------------------------------------------------------------------------
# ConcaveHullCapability
# ---------------------------------------------------------------------------


class TestConcaveHull:

    def test_concave_hull_per_feature_returns_polygon(self, metric_points):
        result = ConcaveHullCapability().execute(metric_points, ratio=0.5)
        assert len(result) == len(metric_points)

    def test_concave_hull_dissolve(self, metric_points):
        result = ConcaveHullCapability().execute(metric_points, ratio=0.5, dissolve=True)
        assert len(result) == 1
        # Hull covers the 10x10 box area, so area > 50
        assert result.geometry.iloc[0].area > 50

    def test_concave_hull_by_group(self, metric_points):
        metric_points["zone"] = ["A", "A", "A", "B", "B"]
        result = ConcaveHullCapability().execute(
            metric_points, ratio=0.5, by_group="zone"
        )
        assert len(result) == 2
        assert set(result["zone"]) == {"A", "B"}

    def test_concave_hull_invalid_ratio(self, metric_points):
        with pytest.raises(ValueError, match="ratio"):
            ConcaveHullCapability().execute(metric_points, ratio=1.5)


# ---------------------------------------------------------------------------
# OffsetCurveCapability
# ---------------------------------------------------------------------------


class TestOffsetCurve:

    def test_offset_curve_parallel_line(self, metric_line):
        result = OffsetCurveCapability().execute(
            metric_line, distance=5.0, crs_meters="EPSG:2154"
        )
        offset = result.geometry.iloc[0]
        # Line at y=0 offset by +5 → parallel line ~y=5
        ys = [c[1] for c in offset.coords]
        assert all(abs(y - 5) < 0.5 for y in ys)

    def test_offset_curve_negative_distance(self, metric_line):
        result = OffsetCurveCapability().execute(
            metric_line, distance=-5.0, crs_meters="EPSG:2154"
        )
        offset = result.geometry.iloc[0]
        ys = [c[1] for c in offset.coords]
        assert all(abs(y + 5) < 0.5 for y in ys)

    def test_offset_curve_invalid_join_style(self, metric_line):
        with pytest.raises(ValueError, match="join_style"):
            OffsetCurveCapability().execute(
                metric_line, distance=5.0, join_style="spiral"
            )


# ---------------------------------------------------------------------------
# SnapToGridCapability
# ---------------------------------------------------------------------------


class TestSnapToGrid:

    def test_snap_to_grid_rounds_coords(self):
        noisy = Polygon([(0.0001, 0.0002), (1.0003, 0.0004), (1.0, 1.0001), (0.0, 1.0)])
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [noisy]}, crs="EPSG:2154")
        result = SnapToGridCapability().execute(gdf, grid_size=0.01)
        # All coords should be multiples of 0.01
        snapped = result.geometry.iloc[0]
        for coord in snapped.exterior.coords:
            x, y = coord[0], coord[1]
            assert abs((x * 100) - round(x * 100)) < 1e-6
            assert abs((y * 100) - round(y * 100)) < 1e-6

    def test_snap_to_grid_invalid_size(self, metric_polygon):
        with pytest.raises(ValueError, match="grid_size"):
            SnapToGridCapability().execute(metric_polygon, grid_size=0)


# ---------------------------------------------------------------------------
# LineMergeCapability
# ---------------------------------------------------------------------------


class TestLineMerge:

    def test_line_merge_reconnects_touching_lines(self):
        # Two lines that share endpoint (100, 0)
        mls = MultiLineString([
            [(0, 0), (100, 0)],
            [(100, 0), (200, 0)],
        ])
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [mls]}, crs="EPSG:2154")
        result = LineMergeCapability().execute(gdf)
        merged = result.geometry.iloc[0]
        # Merged → single LineString with coords from (0,0) to (200,0)
        assert merged.geom_type == "LineString"
        assert len(list(merged.coords)) == 3


# ---------------------------------------------------------------------------
# PolygonizeCapability
# ---------------------------------------------------------------------------


class TestPolygonize:

    def test_polygonize_builds_polygon_from_ring_lines(self):
        # 4 lines forming a closed square
        lines = [
            LineString([(0, 0), (1, 0)]),
            LineString([(1, 0), (1, 1)]),
            LineString([(1, 1), (0, 1)]),
            LineString([(0, 1), (0, 0)]),
        ]
        gdf = gpd.GeoDataFrame({"geometry": lines}, crs="EPSG:2154")
        result = PolygonizeCapability().execute(gdf)
        assert len(result) == 1
        assert pytest.approx(result.geometry.iloc[0].area) == 1.0


# ---------------------------------------------------------------------------
# VoronoiPolygonsCapability
# ---------------------------------------------------------------------------


class TestVoronoiPolygons:

    def test_voronoi_from_points(self, metric_points):
        result = VoronoiPolygonsCapability().execute(metric_points)
        # At least as many cells as seeds when points are distinct
        assert len(result) >= 1
        # All cells should be polygons
        assert all(g.geom_type in {"Polygon", "LineString"} for g in result.geometry)

    def test_voronoi_only_edges(self, metric_points):
        result = VoronoiPolygonsCapability().execute(metric_points, only_edges=True)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# DelaunayTriangulationCapability
# ---------------------------------------------------------------------------


class TestDelaunayTriangulation:

    def test_delaunay_produces_triangles(self, metric_points):
        result = DelaunayTriangulationCapability().execute(metric_points)
        # At least one triangle for 5 points
        assert len(result) >= 1
        # Each feature should be a Polygon (triangle)
        assert all(g.geom_type == "Polygon" for g in result.geometry)

    def test_delaunay_only_edges(self, metric_points):
        result = DelaunayTriangulationCapability().execute(
            metric_points, only_edges=True
        )
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# SimplifyCapability with algorithms
# ---------------------------------------------------------------------------


class TestSimplifyAlgorithms:

    def test_simplify_dp_reduces_vertices(self):
        line = LineString([(i, (i % 2) * 0.1) for i in range(0, 101, 2)])
        gdf = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:2154")
        result = SimplifyCapability().execute(
            gdf, tolerance=5.0, algorithm="dp", crs_meters="EPSG:2154"
        )
        assert len(list(result.geometry.iloc[0].coords)) < len(list(line.coords))

    def test_simplify_vw_works(self):
        line = LineString([(i, (i % 2) * 0.1) for i in range(0, 101, 2)])
        gdf = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:2154")
        result = SimplifyCapability().execute(
            gdf, tolerance=5.0, algorithm="vw", crs_meters="EPSG:2154"
        )
        assert len(list(result.geometry.iloc[0].coords)) < len(list(line.coords))

    def test_simplify_coverage_on_polygons(self):
        p1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        p2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [p1, p2]}, crs="EPSG:2154"
        )
        # coverage_simplify with a small tolerance keeps the coverage intact
        result = SimplifyCapability().execute(
            gdf, tolerance=0.001, algorithm="coverage", crs_meters="EPSG:2154"
        )
        assert len(result) == 2

    def test_simplify_invalid_algorithm(self, metric_polygon):
        with pytest.raises(ValueError, match="algorithm"):
            SimplifyCapability().execute(
                metric_polygon, tolerance=1.0, algorithm="chaikin"
            )

    def test_simplify_schema_lists_algorithms(self):
        schema = SimplifyCapability().get_schema()
        assert "algorithm" in schema["properties"]
        assert set(schema["properties"]["algorithm"]["enum"]) == {"dp", "vw", "coverage"}


# ---------------------------------------------------------------------------
# Clip / Intersects strategies
# ---------------------------------------------------------------------------


class TestStrategyRegistration:

    def test_clip_has_three_strategies(self):
        modes = [s.mode.value for s in ClipCapability._strategies]
        assert "postgis" in modes and "duckdb" in modes and "python" in modes

    def test_intersects_has_three_strategies(self):
        modes = [s.mode.value for s in IntersectsCapability._strategies]
        assert "postgis" in modes and "duckdb" in modes and "python" in modes

    def test_clip_python_strategy_runs_without_engine(self, metric_polygon):
        """Python strategy stays the fallback when no special engine is active."""
        # execute() path bypasses strategies — it should still work.
        mask = gpd.GeoDataFrame(
            {"geometry": [Polygon([(0, 0), (50, 0), (50, 50), (0, 50)])]},
            crs="EPSG:2154",
        )
        result = ClipCapability().execute(metric_polygon, ref_gdf=mask)
        assert len(result) == 1
        assert pytest.approx(result.geometry.iloc[0].area) == 2500.0

    def test_intersects_python_strategy_runs_without_engine(self, metric_polygon):
        pts = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [Point(50, 50), Point(200, 200)]},
            crs="EPSG:2154",
        )
        result = IntersectsCapability().execute(pts, ref_gdf=metric_polygon)
        # Only Point(50, 50) is inside the polygon
        assert set(result["id"]) == {1}


# ---------------------------------------------------------------------------
# Temporal AttrPredicate operators
# ---------------------------------------------------------------------------


class TestTemporalPredicates:

    def test_age_gt_fires_after_threshold(self):
        past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
        pred = AttrPredicate(field="modified_at", op="age_gt", value=3600)  # 1h
        assert _eval_attr(pred, {"modified_at": past.isoformat()})

    def test_age_gt_does_not_fire_when_too_recent(self):
        recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=10)
        pred = AttrPredicate(field="modified_at", op="age_gt", value=3600)
        assert not _eval_attr(pred, {"modified_at": recent.isoformat()})

    def test_age_lt_inverse(self):
        recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=10)
        pred = AttrPredicate(field="modified_at", op="age_lt", value=3600)
        assert _eval_attr(pred, {"modified_at": recent.isoformat()})

    def test_before(self):
        pred = AttrPredicate(field="created_at", op="before", value="2026-01-01T00:00:00Z")
        assert _eval_attr(pred, {"created_at": "2025-06-01T00:00:00Z"})
        assert not _eval_attr(pred, {"created_at": "2026-06-01T00:00:00Z"})

    def test_after(self):
        pred = AttrPredicate(field="created_at", op="after", value="2026-01-01T00:00:00Z")
        assert _eval_attr(pred, {"created_at": "2026-06-01T00:00:00Z"})
        assert not _eval_attr(pred, {"created_at": "2025-06-01T00:00:00Z"})

    def test_between(self):
        pred = AttrPredicate(
            field="created_at",
            op="between",
            value=["2026-01-01T00:00:00Z", "2026-12-31T23:59:59Z"],
        )
        assert _eval_attr(pred, {"created_at": "2026-06-15T12:00:00Z"})
        assert not _eval_attr(pred, {"created_at": "2025-06-15T12:00:00Z"})

    def test_epoch_timestamp_accepted(self):
        ten_minutes_ago = time.time() - 600
        pred = AttrPredicate(field="ts", op="age_gt", value=300)
        assert _eval_attr(pred, {"ts": ten_minutes_ago})

    def test_unparsable_timestamp_returns_false(self):
        pred = AttrPredicate(field="ts", op="age_gt", value=10)
        assert _eval_attr(pred, {"ts": "not-a-date"}) is False

    def test_missing_field_returns_false(self):
        pred = AttrPredicate(field="ts", op="age_gt", value=10)
        assert _eval_attr(pred, {}) is False

    def test_between_requires_2_element_list(self):
        pred = AttrPredicate(field="ts", op="between", value="2026-01-01")
        errors = _validate_predicate(pred, index=0)
        assert any("2-element list" in e.message for e in errors)

    def test_age_gt_requires_value(self):
        pred = AttrPredicate(field="ts", op="age_gt", value=None)
        errors = _validate_predicate(pred, index=0)
        assert any("requires a numeric" in e.message for e in errors)

    def test_validation_accepts_temporal_ops(self):
        for op in ("age_gt", "age_lt", "before", "after"):
            pred = AttrPredicate(field="ts", op=op, value=60 if "age" in op else "2026-01-01T00:00:00Z")
            errors = _validate_predicate(pred, index=0)
            assert errors == [], f"{op} should validate, got {errors}"


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryExposesNewCapabilities:

    @pytest.mark.parametrize(
        "name",
        [
            "concave_hull",
            "offset_curve",
            "snap_to_grid",
            "line_merge",
            "polygonize",
            "voronoi_polygons",
            "delaunay_triangulation",
        ],
    )
    def test_capability_registered(self, name):
        cap = get_capability(name)
        assert cap is not None
        schema = cap.get_schema()
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"
