"""Unit tests for build_network_graph (capability A)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString

import gispulse
from gispulse.capabilities.network_graph import (
    BuildNetworkGraphCapability,
    split_network_graph,
)


def _run(gdf, **params):
    return BuildNetworkGraphCapability().execute(gdf, **params)


@pytest.fixture
def y_network() -> gpd.GeoDataFrame:
    # A Y: two arms meeting at (10, 0), third arm going right.
    return gpd.GeoDataFrame(
        {"rid": ["a", "b", "c"]},
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(10, 10), (10, 0)]),
            LineString([(10, 0), (20, 0)]),
        ],
        crs="EPSG:3857",
    )


def test_nodes_and_edges_counts(y_network):
    out = _run(y_network)
    nodes, edges = split_network_graph(out)
    assert len(edges) == 3
    # 4 distinct endpoints: (0,0),(20,0),(10,10) and the shared (10,0)
    assert len(nodes) == 4


def test_shared_node_degree(y_network):
    out = _run(y_network)
    nodes, _ = split_network_graph(out)
    # the junction (10,0) is incident to all 3 edges → degree 3
    assert nodes["degree"].max() == 3
    assert sorted(nodes["degree"]) == [1, 1, 1, 3]


def test_edge_fields_present(y_network):
    out = _run(y_network)
    _, edges = split_network_graph(out)
    for col in ("edge_id", "from_node", "to_node", "length_m"):
        assert col in edges.columns
    # length_m is the metric length (input already metric).
    assert edges["length_m"].tolist() == pytest.approx([10.0, 10.0, 10.0])


def test_edge_id_from_id_col(y_network):
    out = _run(y_network, id_col="rid")
    _, edges = split_network_graph(out)
    assert set(edges["edge_id"]) == {"a", "b", "c"}


def test_attributes_preserved_on_edges(y_network):
    out = _run(y_network, id_col="rid")
    _, edges = split_network_graph(out)
    assert "rid" in edges.columns


def test_snap_tolerance_fuses_near_endpoints():
    # Two lines whose endpoints are 0.5 m apart — fused at tol=1.
    gdf = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(10.5, 0), (20, 0)]),
        ],
        crs="EPSG:3857",
    )
    loose = split_network_graph(_run(gdf, snap_tolerance_m=0.0))[0]
    assert len(loose) == 4  # nothing fused
    tight = split_network_graph(_run(gdf, snap_tolerance_m=1.0))[0]
    assert len(tight) == 3  # the two near endpoints merged


def test_multilinestring_parts_become_edges():
    gdf = gpd.GeoDataFrame(
        {"rid": ["m"]},
        geometry=[
            MultiLineString(
                [[(0, 0), (10, 0)], [(0, 5), (10, 5)]]
            )
        ],
        crs="EPSG:3857",
    )
    _, edges = split_network_graph(_run(gdf, id_col="rid"))
    assert len(edges) == 2
    assert set(edges["edge_id"]) == {"m#0", "m#1"}


def test_reprojects_angular_lengths_in_meters():
    # A ~1 degree segment near the equator in EPSG:4326.
    gdf = gpd.GeoDataFrame(
        geometry=[LineString([(0, 0), (0.01, 0)])],
        crs="EPSG:4326",
    )
    out = _run(gdf, crs_meters="EPSG:3857")
    nodes, edges = split_network_graph(out)
    # result back in source CRS
    assert str(out.crs).endswith("4326")
    # length computed in meters (~1.1 km for 0.01° at equator in 3857)
    assert edges["length_m"].iloc[0] > 1000


def test_empty_input():
    gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:3857")
    out = _run(gdf)
    assert len(out) == 0
    assert "feature_type" in out.columns


def test_registered_and_callable_via_apply(y_network):
    out = gispulse.apply("build_network_graph", y_network, id_col="rid")
    assert "feature_type" in out.columns
    assert set(out["feature_type"]) == {"node", "edge"}
