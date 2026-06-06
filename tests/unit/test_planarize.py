"""Unit tests for the planarize capability (D-enhance)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString

import gispulse
from gispulse.capabilities.network_topology import (
    PlanarizeCapability,
    split_planar_graph,
)


def _run(gdf, **params):
    return PlanarizeCapability().execute(gdf, **params)


@pytest.fixture
def crossing() -> gpd.GeoDataFrame:
    # An X: horizontal A and vertical B crossing at (5, 0).
    return gpd.GeoDataFrame(
        {"rid": ["A", "B"]},
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(5, -5), (5, 5)]),
        ],
        crs="EPSG:3857",
    )


def test_crossing_splits_into_four_segments(crossing):
    out = _run(crossing, id_col="rid")
    nodes, segments = split_planar_graph(out)
    assert len(segments) == 4
    # 4 outer endpoints + 1 crossing node
    assert len(nodes) == 5


def test_crossing_center_degree_four(crossing):
    out = _run(crossing, id_col="rid")
    nodes, _ = split_planar_graph(out)
    assert nodes["degree"].max() == 4


def test_parent_edge_id_and_attributes(crossing):
    out = _run(crossing, id_col="rid")
    _, segments = split_planar_graph(out)
    assert set(segments["parent_edge_id"]) == {"A", "B"}
    assert "rid" in segments.columns  # source attribute preserved
    # two segments per parent
    assert sorted(segments["parent_edge_id"].value_counts().tolist()) == [2, 2]


def test_t_junction_only_through_line_splits():
    gdf = gpd.GeoDataFrame(
        {"rid": ["A", "C"]},
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(5, 0), (5, 5)]),  # touches A at its own endpoint
        ],
        crs="EPSG:3857",
    )
    _, segments = split_planar_graph(_run(gdf, id_col="rid"))
    # A splits at (5,0) → 2 ; C unchanged → 1
    assert len(segments) == 3
    assert sorted(segments["parent_edge_id"].value_counts().tolist()) == [1, 2]


def test_quasi_intersection_snaps_with_tolerance():
    gdf = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(5, 0.5), (5, 5)]),  # 0.5 m gap from A
        ],
        crs="EPSG:3857",
    )
    none = split_planar_graph(_run(gdf, snap_tolerance_m=0.0))[1]
    assert len(none) == 2  # nothing split
    snapped = split_planar_graph(_run(gdf, snap_tolerance_m=1.0))[1]
    assert len(snapped) == 3  # A split at the near point


def test_no_intersection_passthrough():
    gdf = gpd.GeoDataFrame(
        {"rid": ["A", "B"]},
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(0, 100), (10, 100)]),
        ],
        crs="EPSG:3857",
    )
    _, segments = split_planar_graph(_run(gdf, id_col="rid"))
    assert len(segments) == 2  # untouched


def test_multilinestring_parent_suffix():
    gdf = gpd.GeoDataFrame(
        {"rid": ["m"]},
        geometry=[MultiLineString([[(0, 0), (10, 0)], [(0, 5), (10, 5)]])],
        crs="EPSG:3857",
    )
    _, segments = split_planar_graph(_run(gdf, id_col="rid"))
    assert set(segments["parent_edge_id"]) == {"m#0", "m#1"}


def test_empty_input():
    gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:3857")
    out = _run(gdf)
    assert len(out) == 0
    assert "feature_type" in out.columns


def test_reprojects_back_to_source_crs(crossing):
    ll = crossing.to_crs("EPSG:4326")
    out = _run(ll, id_col="rid", crs_meters="EPSG:3857")
    assert str(out.crs).endswith("4326")


def test_registered_via_apply(crossing):
    out = gispulse.apply("planarize", crossing, id_col="rid")
    assert set(out["feature_type"]) == {"node", "segment"}
