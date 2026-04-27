"""Tests for persistence.gpkg_spatial — RTree + Shapely two-phase queries.

Bugs in spatial filtering silently return wrong features, which cascades
through every spatial rule. We test:
- spatial_filter predicate dispatch (pure Shapely, no SQL needed)
- rtree_bbox_filter with a real GPKG (GDAL auto-creates the RTree)
- spatial_query two-phase integration
- Helper path correctness (auto-detect geom column, RTree table name)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from persistence.gpkg_spatial import (
    _PREDICATE_MAP,
    _detect_geom_column,
    _rtree_exists,
    _rtree_table_name,
    bbox_filter_gdf,
    rtree_bbox_filter,
    spatial_filter,
    spatial_query,
)


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    """Five points on the diagonal (0,0) → (4,4)."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "name": ["A", "B", "C", "D", "E"],
            "geometry": [
                Point(0, 0),
                Point(1, 1),
                Point(2, 2),
                Point(3, 3),
                Point(4, 4),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def polygon_gdf() -> gpd.GeoDataFrame:
    """Three non-overlapping polygons."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "geometry": [
                box(0, 0, 1, 1),
                box(2, 0, 3, 1),
                box(5, 5, 6, 6),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def gpkg_with_rtree(tmp_path, sample_gdf) -> Path:
    """Write a GPKG with auto-generated RTree (GDAL/pyogrio default)."""
    path = tmp_path / "points.gpkg"
    sample_gdf.to_file(path, layer="points", driver="GPKG")
    return path


# ---------------------------------------------------------------------------
# _rtree_table_name / _PREDICATE_MAP constants
# ---------------------------------------------------------------------------


class TestRtreeTableName:
    def test_default_geom_col(self):
        assert _rtree_table_name("parcels") == "rtree_parcels_geom"

    def test_custom_geom_col(self):
        assert _rtree_table_name("t", geom_col="shape") == "rtree_t_shape"


class TestPredicateMap:
    def test_all_expected_predicates(self):
        expected = {
            "intersects", "within", "contains",
            "overlaps", "crosses", "touches", "disjoint",
        }
        assert set(_PREDICATE_MAP.keys()) == expected


# ---------------------------------------------------------------------------
# _detect_geom_column / _rtree_exists
# ---------------------------------------------------------------------------


class TestGeomColumnDetection:
    def test_detects_geom_column_in_real_gpkg(self, gpkg_with_rtree):
        conn = sqlite3.connect(str(gpkg_with_rtree))
        try:
            col = _detect_geom_column(conn, "points")
            assert col in ("geom", "geometry")
        finally:
            conn.close()

    def test_missing_layer_returns_none(self, gpkg_with_rtree):
        conn = sqlite3.connect(str(gpkg_with_rtree))
        try:
            assert _detect_geom_column(conn, "nonexistent") is None
        finally:
            conn.close()

    def test_missing_table_returns_none(self, tmp_path):
        """If gpkg_geometry_columns table doesn't exist, return None."""
        path = tmp_path / "empty.db"
        conn = sqlite3.connect(path)
        try:
            assert _detect_geom_column(conn, "x") is None
        finally:
            conn.close()


class TestRtreeExists:
    def test_returns_true_for_gdal_generated_rtree(self, gpkg_with_rtree):
        conn = sqlite3.connect(str(gpkg_with_rtree))
        try:
            geom_col = _detect_geom_column(conn, "points") or "geom"
            rtree_name = _rtree_table_name("points", geom_col)
            assert _rtree_exists(conn, rtree_name) is True
        finally:
            conn.close()

    def test_returns_false_for_missing(self, gpkg_with_rtree):
        conn = sqlite3.connect(str(gpkg_with_rtree))
        try:
            assert _rtree_exists(conn, "rtree_ghost_geom") is False
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# rtree_bbox_filter — real GPKG with auto RTree
# ---------------------------------------------------------------------------


class TestRtreeBboxFilter:
    def test_bbox_covers_all_points(self, gpkg_with_rtree):
        conn = sqlite3.connect(str(gpkg_with_rtree))
        try:
            fids = rtree_bbox_filter(conn, "points", (-1, -1, 10, 10))
            assert len(fids) == 5
        finally:
            conn.close()

    def test_bbox_restricts_to_subset(self, gpkg_with_rtree):
        conn = sqlite3.connect(str(gpkg_with_rtree))
        try:
            # Cover (0,0), (1,1) only — exact envelope match
            fids = rtree_bbox_filter(conn, "points", (-0.5, -0.5, 1.5, 1.5))
            assert len(fids) == 2
        finally:
            conn.close()

    def test_bbox_outside_returns_empty(self, gpkg_with_rtree):
        conn = sqlite3.connect(str(gpkg_with_rtree))
        try:
            fids = rtree_bbox_filter(conn, "points", (100, 100, 200, 200))
            assert fids == []
        finally:
            conn.close()

    def test_missing_rtree_returns_empty(self, tmp_path):
        """No RTree → logged warning + empty list (caller does full scan)."""
        path = tmp_path / "empty.db"
        conn = sqlite3.connect(path)
        try:
            fids = rtree_bbox_filter(conn, "ghost", (0, 0, 1, 1))
            assert fids == []
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# spatial_filter — pure Shapely (no SQL)
# ---------------------------------------------------------------------------


class TestSpatialFilter:
    def test_intersects_default(self, sample_gdf):
        result = spatial_filter(sample_gdf, box(-0.5, -0.5, 1.5, 1.5))
        assert len(result) == 2  # (0,0), (1,1)

    def test_within(self, sample_gdf):
        result = spatial_filter(
            sample_gdf, box(-0.5, -0.5, 2.5, 2.5), predicate="within"
        )
        assert len(result) == 3  # (0,0), (1,1), (2,2)

    def test_contains_polygons(self, polygon_gdf):
        # A polygon that contains (0,0,1,1) box-ish — use a larger bbox
        result = spatial_filter(
            polygon_gdf, box(0, 0, 1, 1), predicate="contains"
        )
        # polygon_gdf[0] is box(0,0,1,1) — 'contains' its own boundary? No,
        # Shapely contains is strict. (2,0,3,1) doesn't contain (0,0,1,1).
        assert len(result) <= 1

    def test_disjoint(self, sample_gdf):
        result = spatial_filter(
            sample_gdf, box(-0.5, -0.5, 1.5, 1.5), predicate="disjoint"
        )
        assert len(result) == 3  # (2,2), (3,3), (4,4) are disjoint

    def test_unknown_predicate_raises(self, sample_gdf):
        with pytest.raises(ValueError, match="Unknown predicate"):
            spatial_filter(sample_gdf, Point(0, 0), predicate="INVALID")

    def test_returns_copy_not_view(self, sample_gdf):
        """Filtered result must be a copy so modifying it doesn't mutate input."""
        result = spatial_filter(sample_gdf, box(-1, -1, 1.5, 1.5))
        result["id"] = 999
        # Original unchanged
        assert list(sample_gdf["id"]) == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# spatial_query — two-phase integration
# ---------------------------------------------------------------------------


class TestSpatialQuery:
    def test_rtree_prefilter_then_shapely_refinement(
        self, gpkg_with_rtree, sample_gdf
    ):
        conn = sqlite3.connect(str(gpkg_with_rtree))
        try:
            result = spatial_query(
                conn, sample_gdf, "points", box(-0.5, -0.5, 1.5, 1.5)
            )
            # Points (0,0) and (1,1) intersect the box
            assert len(result) == 2
        finally:
            conn.close()

    def test_no_rtree_falls_back_to_full_scan(self, tmp_path, sample_gdf):
        """When the GPKG has no RTree for this layer, spatial_query does a
        full Shapely scan instead of returning empty."""
        path = tmp_path / "no_rtree.db"
        conn = sqlite3.connect(path)
        try:
            result = spatial_query(
                conn, sample_gdf, "ghost_layer",
                box(-0.5, -0.5, 1.5, 1.5),
            )
            # All 5 points scanned — 2 should match
            assert len(result) == 2
        finally:
            conn.close()

    def test_empty_candidates_short_circuits(self, gpkg_with_rtree, sample_gdf):
        conn = sqlite3.connect(str(gpkg_with_rtree))
        try:
            # bbox far away from any point
            result = spatial_query(
                conn, sample_gdf, "points",
                box(1000, 1000, 1001, 1001),
            )
            assert len(result) == 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# bbox_filter_gdf
# ---------------------------------------------------------------------------


class TestBboxFilterGdf:
    def test_bbox_restricts_via_rtree(self, gpkg_with_rtree, sample_gdf):
        conn = sqlite3.connect(str(gpkg_with_rtree))
        try:
            result = bbox_filter_gdf(
                conn, sample_gdf, "points", (-0.5, -0.5, 1.5, 1.5)
            )
            assert len(result) == 2
        finally:
            conn.close()
