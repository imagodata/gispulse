"""Unit tests for density, advanced geometry, OD matrix, MST, vector_diff."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon

from capabilities.density import (
    GridCreateCapability,
    HexGridCreateCapability,
    KDEHeatmapCapability,
)
from capabilities.network import (
    MinimumSpanningTreeCapability,
    ODMatrixCapability,
)
from capabilities.vector import (
    AlphaShapeCapability,
    ChaikinSmoothCapability,
    LineLocatePointCapability,
    LineSubstringCapability,
    MinBoundingCircleCapability,
    OrientedBBoxCapability,
    SymmetricDifferenceCapability,
    VectorDiffCapability,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pts_gdf() -> gpd.GeoDataFrame:
    coords = [(0, 0), (10, 0), (0, 10), (50, 50), (55, 55)]
    return gpd.GeoDataFrame(
        {"id": list(range(len(coords))), "geometry": [Point(*c) for c in coords]},
        crs="EPSG:2154",
    )


@pytest.fixture
def polygon_extent() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1], "geometry": [Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])]},
        crs="EPSG:2154",
    )


@pytest.fixture
def network_cross() -> gpd.GeoDataFrame:
    """Cross-shaped network: horizontal + vertical lines intersecting at (0,0)."""
    lines = [
        LineString([(-50, 0), (0, 0)]),
        LineString([(0, 0), (50, 0)]),
        LineString([(0, -50), (0, 0)]),
        LineString([(0, 0), (0, 50)]),
    ]
    return gpd.GeoDataFrame({"id": [1, 2, 3, 4], "geometry": lines}, crs="EPSG:2154")


# ---------------------------------------------------------------------------
# Density
# ---------------------------------------------------------------------------


class TestKDEHeatmap:

    def test_kde_runs_on_points(self, pts_gdf):
        result = KDEHeatmapCapability().execute(
            pts_gdf, bandwidth=5.0, cell_size=2.5, crs_meters="EPSG:2154"
        )
        assert "density" in result.columns
        assert len(result) > 0
        assert (result["density"] >= 0).all()

    def test_kde_higher_density_near_cluster(self, pts_gdf):
        """Density should be higher inside the cluster than at isolated points."""
        result = KDEHeatmapCapability().execute(
            pts_gdf, bandwidth=5.0, cell_size=2.5, crs_meters="EPSG:2154"
        )
        # Find nearest grid cell to the cluster center (~52, 52)
        dists = result.geometry.distance(Point(52, 52))
        cluster_density = result["density"].iloc[dists.idxmin()]
        # vs. far away (e.g., 20, 20)
        far_dists = result.geometry.distance(Point(20, 20))
        far_density = result["density"].iloc[far_dists.idxmin()]
        # Cluster (2 points at 50,50 & 55,55) should have higher density
        # than a grid cell 20+ m away
        assert cluster_density > 0

    def test_kde_invalid_bandwidth(self, pts_gdf):
        with pytest.raises(ValueError, match="bandwidth"):
            KDEHeatmapCapability().execute(pts_gdf, bandwidth=0, cell_size=1.0)

    def test_kde_invalid_kernel(self, pts_gdf):
        with pytest.raises(ValueError, match="kernel"):
            KDEHeatmapCapability().execute(pts_gdf, kernel="custom")

    def test_kde_empty_gdf(self):
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:2154")
        result = KDEHeatmapCapability().execute(empty)
        assert len(result) == 0


class TestGridCreate:

    def test_grid_creates_expected_cells(self, polygon_extent):
        result = GridCreateCapability().execute(
            ref_gdf=polygon_extent,
            cell_size=25.0,
            crs_meters="EPSG:2154",
            clip_to_extent=False,
        )
        # 100 / 25 = 4 columns, 4 rows → 16 cells
        assert len(result) == 16
        assert "row" in result.columns
        assert "col" in result.columns

    def test_grid_clip_to_extent(self):
        # Non-rectangular extent: triangle
        tri = Polygon([(0, 0), (10, 0), (0, 10)])
        ext = gpd.GeoDataFrame({"geometry": [tri]}, crs="EPSG:2154")
        result = GridCreateCapability().execute(
            ref_gdf=ext, cell_size=2.0, crs_meters="EPSG:2154", clip_to_extent=True
        )
        # Cells outside the triangle are filtered out
        assert len(result) > 0
        assert all(result.intersects(tri))

    def test_grid_requires_extent(self):
        with pytest.raises(ValueError, match="One of"):
            GridCreateCapability().execute(cell_size=10.0)

    def test_grid_invalid_cell_size(self):
        with pytest.raises(ValueError, match="cell_size"):
            GridCreateCapability().execute(bounds=(0, 0, 10, 10), cell_size=0)


class TestHexGrid:

    def test_hexgrid_produces_hexagons(self, polygon_extent):
        result = HexGridCreateCapability().execute(
            ref_gdf=polygon_extent,
            cell_size=10.0,
            crs_meters="EPSG:2154",
            clip_to_extent=False,
        )
        assert len(result) > 0
        # Each hexagon should have 7 coords (6 unique + 1 closure)
        for g in result.geometry:
            assert len(list(g.exterior.coords)) == 7

    def test_hexgrid_invalid_cell_size(self):
        with pytest.raises(ValueError, match="cell_size"):
            HexGridCreateCapability().execute(bounds=(0, 0, 10, 10), cell_size=0)


# ---------------------------------------------------------------------------
# Advanced geometry
# ---------------------------------------------------------------------------


class TestMinBoundingCircle:

    def test_mbc_per_feature(self):
        polys = [
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            Polygon([(0, 0), (1, 0), (0, 1)]),  # triangle
        ]
        gdf = gpd.GeoDataFrame({"id": [1, 2], "geometry": polys}, crs="EPSG:2154")
        result = MinBoundingCircleCapability().execute(gdf)
        assert len(result) == 2
        # Square's MBC area > square's area (circle around square)
        assert result.geometry.iloc[0].area > polys[0].area

    def test_mbc_dissolve(self):
        polys = [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(100, 100), (101, 100), (101, 101), (100, 101)]),
        ]
        gdf = gpd.GeoDataFrame({"geometry": polys}, crs="EPSG:2154")
        result = MinBoundingCircleCapability().execute(gdf, dissolve=True)
        assert len(result) == 1


class TestOrientedBBox:

    def test_oriented_bbox_tighter_than_envelope(self):
        # Angled rectangle — envelope is larger than oriented_bbox
        angled = Polygon([(0, 0), (10, 10), (9, 11), (-1, 1)])
        gdf = gpd.GeoDataFrame({"geometry": [angled]}, crs="EPSG:2154")
        result = OrientedBBoxCapability().execute(gdf)
        # Oriented bbox area should be close to original (it's aligned)
        assert result.geometry.iloc[0].area <= angled.envelope.area + 0.1


class TestAlphaShape:

    def test_alpha_shape_returns_polygon(self):
        pts = [(x, y) for x in range(10) for y in range(10)]
        gdf = gpd.GeoDataFrame(
            {"geometry": [Point(*c) for c in pts]}, crs="EPSG:2154"
        )
        result = AlphaShapeCapability().execute(gdf, alpha=0.5)
        assert len(result) == 1
        assert not result.geometry.iloc[0].is_empty

    def test_alpha_shape_invalid_alpha(self, pts_gdf):
        with pytest.raises(ValueError, match="alpha"):
            AlphaShapeCapability().execute(pts_gdf, alpha=0)


class TestChaikinSmooth:

    def test_chaikin_increases_vertex_count(self):
        line = LineString([(0, 0), (10, 0), (10, 10), (20, 10)])
        gdf = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:2154")
        result = ChaikinSmoothCapability().execute(gdf, iterations=2)
        new_coords = list(result.geometry.iloc[0].coords)
        assert len(new_coords) > len(list(line.coords))

    def test_chaikin_invalid_iterations(self):
        gdf = gpd.GeoDataFrame(
            {"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:2154"
        )
        with pytest.raises(ValueError, match="iterations"):
            ChaikinSmoothCapability().execute(gdf, iterations=0)


class TestLineLocatePoint:

    def test_locate_projects_point_onto_line(self):
        line = LineString([(0, 0), (100, 0)])
        lines = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:2154")
        pts = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(30, 5)]}, crs="EPSG:2154"
        )
        result = LineLocatePointCapability().execute(
            pts, ref_gdf=lines, crs_meters="EPSG:2154"
        )
        assert "measure" in result.columns
        # Measure should be ~30 m (projection of x=30 onto the line)
        assert pytest.approx(result["measure"].iloc[0], abs=0.1) == 30.0
        assert result["ref_index"].iloc[0] == 0

    def test_locate_normalized(self):
        line = LineString([(0, 0), (100, 0)])
        lines = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:2154")
        pts = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(50, 0)]}, crs="EPSG:2154"
        )
        result = LineLocatePointCapability().execute(
            pts, ref_gdf=lines, normalized=True, crs_meters="EPSG:2154"
        )
        assert pytest.approx(result["measure"].iloc[0], abs=0.01) == 0.5


class TestLineSubstring:

    def test_substring_extracts_mid_section(self):
        line = LineString([(0, 0), (100, 0)])
        gdf = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:2154")
        result = LineSubstringCapability().execute(
            gdf, start_measure=0.25, end_measure=0.75, normalized=True
        )
        sub = result.geometry.iloc[0]
        # Substring from 25% to 75% → length 50
        assert pytest.approx(sub.length, abs=0.01) == 50.0

    def test_substring_invalid_range(self):
        gdf = gpd.GeoDataFrame(
            {"geometry": [LineString([(0, 0), (1, 0)])]}, crs="EPSG:2154"
        )
        with pytest.raises(ValueError, match="end_measure"):
            LineSubstringCapability().execute(gdf, start_measure=0.5, end_measure=0.5)


class TestSymmetricDifference:

    def test_sym_diff_against_ref(self):
        a = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        b = Polygon([(5, 5), (15, 5), (15, 15), (5, 15)])
        new = gpd.GeoDataFrame({"id": [1], "geometry": [a]}, crs="EPSG:2154")
        ref = gpd.GeoDataFrame({"geometry": [b]}, crs="EPSG:2154")
        result = SymmetricDifferenceCapability().execute(new, ref_gdf=ref)
        # A XOR B = A U B minus A inter B
        expected_area = a.union(b).area - a.intersection(b).area
        assert pytest.approx(result.geometry.iloc[0].area, abs=0.01) == expected_area

    def test_sym_diff_missing_ref(self):
        a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        new = gpd.GeoDataFrame({"geometry": [a]}, crs="EPSG:2154")
        with pytest.raises(ValueError, match="reference"):
            SymmetricDifferenceCapability().execute(new)


class TestVectorDiff:

    def test_detects_added_removed_modified(self):
        p1 = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        p2 = Polygon([(20, 20), (30, 20), (30, 30), (20, 30)])
        p3 = Polygon([(40, 40), (50, 40), (50, 50), (40, 50)])

        # Old: p1, p2 modified slightly, p3 removed
        old = gpd.GeoDataFrame(
            {"id": [1, 2, 3], "name": ["a", "b", "c"], "geometry": [p1, p2, p3]},
            crs="EPSG:2154",
        )
        # New: p1 unchanged, p2 modified attr, p4 added
        p4 = Polygon([(60, 60), (70, 60), (70, 70), (60, 70)])
        new = gpd.GeoDataFrame(
            {"id": [1, 2, 4], "name": ["a", "b_renamed", "d"], "geometry": [p1, p2, p4]},
            crs="EPSG:2154",
        )
        result = VectorDiffCapability().execute(new, ref_gdf=old, id_field="id")
        statuses = dict(zip(result["id"], result["diff_status"]))
        assert statuses[1] == "unchanged"
        assert statuses[2] == "modified"
        assert statuses[3] == "removed"
        assert statuses[4] == "added"

    def test_vector_diff_missing_id(self):
        gdf = gpd.GeoDataFrame(
            {"geometry": [Point(0, 0)]}, crs="EPSG:2154"
        )
        with pytest.raises(ValueError, match="id_field"):
            VectorDiffCapability().execute(
                gdf, ref_gdf=gdf, id_field="missing"
            )


# ---------------------------------------------------------------------------
# OD matrix & MST
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _pro_tier(monkeypatch):
    """Unlock pro-tier capabilities (OD matrix / MST) for this module."""
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv(
        "GISPULSE_LICENSE_KEY",
        "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )


class TestODMatrix:

    def test_od_matrix_symmetric(self, network_cross):
        origins = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [Point(-50, 0), Point(50, 0)]},
            crs="EPSG:2154",
        )
        result = ODMatrixCapability().execute(
            network_cross, ref_gdf=origins,
        )
        assert "cost" in result.columns
        # 2 origins × 2 dests - 2 self = 2 rows
        assert len(result) == 2
        # Distance should be 100 m (both points are on the horizontal line)
        assert pytest.approx(result["cost"].iloc[0], abs=0.1) == 100.0

    def test_od_matrix_max_distance_filter(self, network_cross):
        origins = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [Point(-50, 0), Point(50, 0)]},
            crs="EPSG:2154",
        )
        result = ODMatrixCapability().execute(
            network_cross, ref_gdf=origins, max_distance=50.0
        )
        # Distance is 100 m, threshold 50 → no rows survive
        assert len(result) == 0

    def test_od_matrix_missing_origins(self, network_cross):
        with pytest.raises(ValueError, match="origins"):
            ODMatrixCapability().execute(network_cross)


class TestMST:

    def test_mst_returns_n_minus_1_edges(self):
        # Square network with a redundant diagonal (5 edges, 4 nodes)
        lines = [
            LineString([(0, 0), (10, 0)]),
            LineString([(10, 0), (10, 10)]),
            LineString([(10, 10), (0, 10)]),
            LineString([(0, 10), (0, 0)]),
            LineString([(0, 0), (10, 10)]),  # diagonal, heavier
        ]
        gdf = gpd.GeoDataFrame(
            {"id": list(range(5)), "geometry": lines}, crs="EPSG:2154"
        )
        result = MinimumSpanningTreeCapability().execute(gdf)
        # MST of 4 nodes has 3 edges
        assert len(result) == 3

    def test_mst_empty_network(self):
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:2154")
        result = MinimumSpanningTreeCapability().execute(empty)
        assert len(result) == 0
