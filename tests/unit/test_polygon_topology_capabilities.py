"""Unit tests for polygon topology capabilities."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from capabilities.polygon_topology import (
    FixGapsCapability,
    FixOverlapsCapability,
    RemoveSliversCapability,
    SnapBordersCapability,
)


@pytest.fixture
def coverage_with_gap() -> gpd.GeoDataFrame:
    """Two adjacent squares with a small gap between them."""
    a = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    b = Polygon([(11, 0), (21, 0), (21, 10), (11, 10)])  # 1 m gap
    return gpd.GeoDataFrame({"id": [1, 2], "geometry": [a, b]}, crs="EPSG:2154")


@pytest.fixture
def coverage_with_overlap() -> gpd.GeoDataFrame:
    """Two squares overlapping by 2 m."""
    a = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    b = Polygon([(8, 0), (18, 0), (18, 10), (8, 10)])  # 2 m overlap
    return gpd.GeoDataFrame({"id": [1, 2], "geometry": [a, b]}, crs="EPSG:2154")


@pytest.fixture
def coverage_with_sliver() -> gpd.GeoDataFrame:
    """Normal polygon + a 0.1 m² sliver."""
    a = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    sliver = Polygon([(10, 0), (10.01, 0), (10.01, 10), (10, 10)])  # 0.1 m²
    return gpd.GeoDataFrame(
        {"id": [1, 2], "geometry": [a, sliver]}, crs="EPSG:2154"
    )


class TestFixGaps:

    def test_small_gap_allocated_to_neighbour(self):
        """A fully enclosed gap should be merged into its neighbour."""
        # Outer polygon with an internal hole (5x5 square hole in a 20x20)
        outer = Polygon(
            [(0, 0), (20, 0), (20, 20), (0, 20)],
            holes=[[(7, 7), (13, 7), (13, 13), (7, 13)]],
        )
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [outer]}, crs="EPSG:2154"
        )
        original_total = gdf.geometry.area.sum()
        result = FixGapsCapability().execute(
            gdf, max_gap_area=100.0, crs_meters="EPSG:2154"
        )
        new_total = result.geometry.area.sum()
        # Hole (36 m²) is filled back into the polygon
        assert new_total > original_total + 30.0

    def test_negative_area_raises(self, coverage_with_gap):
        with pytest.raises(ValueError, match="max_gap_area"):
            FixGapsCapability().execute(coverage_with_gap, max_gap_area=-1.0)

    def test_zero_area_is_noop(self, coverage_with_gap):
        """Beta P3 (2026-04-24): the schema declares ``minimum: 0`` for
        ``max_gap_area`` so 0 is a valid contract input meaning "do not
        fill any gap". The execute used to raise ``ValueError`` on
        ``max_gap_area <= 0`` — inconsistent with the schema and
        surprising to callers programmatically setting the threshold to 0
        to disable the fix step. It is now a clean no-op (input returned
        unchanged).
        """
        result = FixGapsCapability().execute(
            coverage_with_gap, max_gap_area=0, crs_meters="EPSG:2154"
        )
        # No gap filled — total area unchanged from input.
        assert result.geometry.area.sum() == coverage_with_gap.geometry.area.sum()


class TestFixOverlaps:

    def test_smallest_keeps_overlap(self, coverage_with_overlap):
        result = FixOverlapsCapability().execute(
            coverage_with_overlap, rule="smallest", crs_meters="EPSG:2154"
        )
        # Both polygons are the same area — one should shrink, one unchanged.
        # No overlap should remain.
        g1, g2 = result.geometry.iloc[0], result.geometry.iloc[1]
        assert g1.intersection(g2).area < 1e-6

    def test_first_rule_preserves_first(self, coverage_with_overlap):
        original_a = coverage_with_overlap.geometry.iloc[0].area
        result = FixOverlapsCapability().execute(
            coverage_with_overlap, rule="first", crs_meters="EPSG:2154"
        )
        # Polygon 1 should be preserved as-is
        assert pytest.approx(result.geometry.iloc[0].area) == original_a
        # Polygon 2 should be clipped
        assert result.geometry.iloc[1].area < coverage_with_overlap.geometry.iloc[1].area

    def test_invalid_rule(self, coverage_with_overlap):
        with pytest.raises(ValueError, match="rule"):
            FixOverlapsCapability().execute(coverage_with_overlap, rule="random")


class TestRemoveSlivers:

    def test_below_min_area_dropped(self, coverage_with_sliver):
        result = RemoveSliversCapability().execute(
            coverage_with_sliver, min_area=1.0, crs_meters="EPSG:2154"
        )
        # Only the 100 m² polygon remains
        assert len(result) == 1
        assert result["id"].iloc[0] == 1

    def test_shape_index_filter(self):
        # Normal square + very thin rectangle
        square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        thin = Polygon([(0, 100), (100, 100), (100, 101), (0, 101)])
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [square, thin]}, crs="EPSG:2154"
        )
        result = RemoveSliversCapability().execute(
            gdf, min_area=0.0, max_shape_index=2.0, crs_meters="EPSG:2154"
        )
        # Square is compact (SI ~1.27), thin is elongated (SI >> 2)
        assert set(result["id"]) == {1}


class TestSnapBorders:

    def test_snap_rounds_coords_to_grid(self):
        noisy = Polygon([(0.001, 0.002), (1.003, 0.001), (1.002, 1.001), (0.001, 1.0)])
        gdf = gpd.GeoDataFrame({"geometry": [noisy]}, crs="EPSG:2154")
        result = SnapBordersCapability().execute(
            gdf, grid_size=0.01, crs_meters="EPSG:2154"
        )
        # All vertices should be multiples of 0.01
        for coord in result.geometry.iloc[0].exterior.coords:
            x, y = coord[0], coord[1]
            assert abs((x * 100) - round(x * 100)) < 1e-6
            assert abs((y * 100) - round(y * 100)) < 1e-6

    def test_invalid_grid_size(self):
        gdf = gpd.GeoDataFrame(
            {"geometry": [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]},
            crs="EPSG:2154",
        )
        with pytest.raises(ValueError, match="grid_size"):
            SnapBordersCapability().execute(gdf, grid_size=0)
