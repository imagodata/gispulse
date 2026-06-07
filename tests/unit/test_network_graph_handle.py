"""Unit tests for NetworkGraph — the reusable routing handle (capability F)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString, Point

pytest.importorskip("networkx", reason="networkx not installed")

from gispulse.core.network_graph_handle import NetworkGraph


@pytest.fixture
def y_network() -> gpd.GeoDataFrame:
    # A Y: two arms meeting at (10, 0), third arm going right.
    return gpd.GeoDataFrame(
        {"rid": ["a", "b", "c"], "w": [1.0, 2.0, 3.0]},
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(10, 10), (10, 0)]),
            LineString([(10, 0), (20, 0)]),
        ],
        crs="EPSG:3857",
    )


def _brute_nearest(points, pt) -> int:
    """Reference O(n) nearest used to pin the handle's snapping."""
    best_id, best = -1, float("inf")
    for nid, p in enumerate(points):
        d = (p.x - pt.x) ** 2 + (p.y - pt.y) ** 2
        if d < best:
            best, best_id = d, nid
    return best_id


def test_node_and_edge_counts(y_network):
    g = NetworkGraph.from_lines(y_network)
    # 4 distinct endpoints: (0,0),(20,0),(10,10) and the shared (10,0)
    assert len(g) == 4
    assert g.graph.number_of_edges() == 3


def test_shared_node_fused(y_network):
    g = NetworkGraph.from_lines(y_network)
    # the (10,0) junction is incident to all three edges → degree 3
    assert max(dict(g.graph.degree()).values()) == 3


def test_node_index_is_rounded_mapping(y_network):
    g = NetworkGraph.from_lines(y_network)
    assert (10.0, 0.0) in g.node_index
    junction = g.node_index[(10.0, 0.0)]
    assert g.node_point(junction).equals(Point(10.0, 0.0))


def test_weight_col_used(y_network):
    g = NetworkGraph.from_lines(y_network, weight_col="w")
    weights = sorted(data["weight"] for _, _, data in g.graph.edges(data=True))
    assert weights == [1.0, 2.0, 3.0]


def test_default_weight_is_length(y_network):
    g = NetworkGraph.from_lines(y_network)  # no weight_col
    weights = sorted(data["weight"] for _, _, data in g.graph.edges(data=True))
    # every arm is 10 m long
    assert weights == pytest.approx([10.0, 10.0, 10.0])


def test_nearest_node_matches_brute_force(y_network):
    g = NetworkGraph.from_lines(y_network)
    points = [g.node_point(i) for i in range(len(g))]
    for probe in (Point(0.4, 0.1), Point(9.6, 0.2), Point(19.0, -1.0), Point(10.1, 9.0)):
        assert g.nearest_node(probe) == _brute_nearest(points, probe)


def test_nearest_nodes_batch(y_network):
    g = NetworkGraph.from_lines(y_network)
    probes = [Point(0, 0), Point(20, 0), Point(10, 10)]
    got = g.nearest_nodes(probes)
    assert got == [g.nearest_node(p) for p in probes]


def test_empty_graph_returns_minus_one():
    empty = gpd.GeoDataFrame({"x": []}, geometry=[], crs="EPSG:3857")
    g = NetworkGraph.from_lines(empty)
    assert len(g) == 0
    assert g.nearest_node(Point(0, 0)) == -1
    assert g.nearest_nodes([Point(0, 0), Point(1, 1)]) == [-1, -1]


def test_multilinestring_splits_into_edges():
    gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[
            MultiLineString(
                [
                    LineString([(0, 0), (1, 0)]),
                    LineString([(1, 0), (2, 0)]),
                ]
            )
        ],
        crs="EPSG:3857",
    )
    g = NetworkGraph.from_lines(gdf)
    assert g.graph.number_of_edges() == 2
    assert len(g) == 3  # (0,0),(1,0),(2,0)


def test_empty_and_none_geometry_skipped():
    gdf = gpd.GeoDataFrame(
        {"id": [1, 2, 3]},
        geometry=[
            LineString([(0, 0), (1, 0)]),
            None,
            LineString(),
        ],
        crs="EPSG:3857",
    )
    g = NetworkGraph.from_lines(gdf)
    assert g.graph.number_of_edges() == 1


def test_index_built_once_and_reused(y_network):
    g = NetworkGraph.from_lines(y_network)
    g.nearest_node(Point(0, 0))
    first = g._index
    g.nearest_node(Point(20, 0))
    assert g._index is first  # lazily built, then cached
