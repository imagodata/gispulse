"""Unit tests for network topology capabilities (consolidate_network family)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString

from capabilities.network_topology import (
    ExtendDanglesCapability,
    NodeLinesCapability,
    RemoveDuplicateEdgesCapability,
    RemovePseudoNodesCapability,
    SnapEndpointsCapability,
)


@pytest.fixture
def disconnected_pair() -> gpd.GeoDataFrame:
    """Two nearly-touching lines with a 0.3 m gap."""
    lines = [
        LineString([(0, 0), (100, 0)]),
        LineString([(100.3, 0), (200, 0)]),
    ]
    return gpd.GeoDataFrame({"id": [1, 2], "geometry": lines}, crs="EPSG:2154")


@pytest.fixture
def pseudo_node_chain() -> gpd.GeoDataFrame:
    """Three collinear segments sharing endpoints → dissolvable to one line."""
    lines = [
        LineString([(0, 0), (10, 0)]),
        LineString([(10, 0), (20, 0)]),
        LineString([(20, 0), (30, 0)]),
    ]
    return gpd.GeoDataFrame({"id": [1, 2, 3], "geometry": lines}, crs="EPSG:2154")


@pytest.fixture
def crossing_lines() -> gpd.GeoDataFrame:
    """Two lines crossing at (5, 5)."""
    lines = [
        LineString([(0, 5), (10, 5)]),
        LineString([(5, 0), (5, 10)]),
    ]
    return gpd.GeoDataFrame({"id": [1, 2], "geometry": lines}, crs="EPSG:2154")


@pytest.fixture
def duplicate_edges() -> gpd.GeoDataFrame:
    """3 lines — 2 are duplicates (same path, one reversed)."""
    lines = [
        LineString([(0, 0), (10, 0)]),
        LineString([(0, 0), (10, 0)]),          # exact duplicate
        LineString([(10, 0), (0, 0)]),          # reversed (same edge)
        LineString([(0, 0), (0, 10)]),          # unique
    ]
    return gpd.GeoDataFrame({"id": [1, 2, 3, 4], "geometry": lines}, crs="EPSG:2154")


class TestSnapEndpoints:

    def test_snap_closes_gap(self, disconnected_pair):
        result = SnapEndpointsCapability().execute(
            disconnected_pair, tolerance=1.0, crs_meters="EPSG:2154"
        )
        # After snap, the right end of line 1 should equal the left end of line 2
        l1_end = list(result.geometry.iloc[0].coords)[-1]
        l2_start = list(result.geometry.iloc[1].coords)[0]
        dist = ((l1_end[0] - l2_start[0]) ** 2 + (l1_end[1] - l2_start[1]) ** 2) ** 0.5
        assert dist < 1e-6

    def test_snap_preserves_count(self, disconnected_pair):
        result = SnapEndpointsCapability().execute(
            disconnected_pair, tolerance=1.0, crs_meters="EPSG:2154"
        )
        assert len(result) == len(disconnected_pair)

    def test_snap_invalid_tolerance(self, disconnected_pair):
        with pytest.raises(ValueError, match="tolerance"):
            SnapEndpointsCapability().execute(disconnected_pair, tolerance=0)


class TestRemovePseudoNodes:

    def test_three_segments_merge_to_one(self, pseudo_node_chain):
        result = RemovePseudoNodesCapability().execute(pseudo_node_chain)
        assert len(result) == 1
        assert pytest.approx(result.geometry.iloc[0].length) == 30.0

    def test_preserves_branched_network(self):
        # A Y-shaped branch — center node has degree 3, must not dissolve
        lines = [
            LineString([(0, 0), (10, 0)]),
            LineString([(10, 0), (20, 5)]),
            LineString([(10, 0), (20, -5)]),
        ]
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2, 3], "geometry": lines}, crs="EPSG:2154"
        )
        result = RemovePseudoNodesCapability().execute(gdf)
        # Y topology preserved → still 3 edges
        assert len(result) == 3


class TestNodeLines:

    def test_split_at_crossing(self, crossing_lines):
        result = NodeLinesCapability().execute(crossing_lines)
        # Two crossing lines → after noding, 4 segments
        assert len(result) == 4

    def test_empty_passthrough(self):
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:2154")
        result = NodeLinesCapability().execute(empty)
        assert len(result) == 0


class TestExtendDangles:

    def test_dangle_snaps_to_nearest_line(self):
        # Line A spans 0-10 along x. Dangle at (5, 2) ending near y=0.
        line_a = LineString([(0, 0), (10, 0)])
        dangle = LineString([(5, 2), (5, 0.3)])
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [line_a, dangle]}, crs="EPSG:2154"
        )
        result = ExtendDanglesCapability().execute(
            gdf, tolerance=1.0, crs_meters="EPSG:2154"
        )
        dangle_out = result.geometry.iloc[1]
        end = list(dangle_out.coords)[-1]
        assert abs(end[1]) < 1e-6  # snapped to y=0

    def test_tolerance_too_small(self):
        line_a = LineString([(0, 0), (10, 0)])
        dangle = LineString([(5, 2), (5, 1.5)])
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [line_a, dangle]}, crs="EPSG:2154"
        )
        # Gap is 1.5 m, tolerance 0.5 → dangle stays put
        result = ExtendDanglesCapability().execute(
            gdf, tolerance=0.5, crs_meters="EPSG:2154"
        )
        end = list(result.geometry.iloc[1].coords)[-1]
        assert abs(end[1] - 1.5) < 1e-6


class TestRemoveDuplicateEdges:

    def test_drops_exact_duplicate(self, duplicate_edges):
        result = RemoveDuplicateEdgesCapability().execute(
            duplicate_edges, tolerance=0.01, crs_meters="EPSG:2154"
        )
        # Starts with 4, expects 2 unique edges
        assert len(result) == 2

    def test_unique_preserved(self):
        lines = [
            LineString([(0, 0), (10, 0)]),
            LineString([(0, 0), (0, 10)]),
        ]
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": lines}, crs="EPSG:2154"
        )
        result = RemoveDuplicateEdgesCapability().execute(gdf)
        assert len(result) == 2
