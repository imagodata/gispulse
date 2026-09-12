"""Tests for capabilities/network_expansion.py — NetworkGreedyExpansionCapability."""

from __future__ import annotations

import pytest

pytest.importorskip("networkx", reason="networkx not installed")

import geopandas as gpd
from shapely.geometry import LineString, Point

from gispulse.capabilities.network_expansion import NetworkGreedyExpansionCapability


@pytest.fixture(autouse=True)
def pro_tier(monkeypatch):
    """The expansion capability requires Pro tier — activate it for every test."""
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv("GISPULSE_LICENSE_KEY", "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


# A projected CRS (is_angular False) keeps node coordinates exact so frontier
# points can be matched by identity without reprojection rounding.
_CRS = "EPSG:3857"


def _path_network() -> gpd.GeoDataFrame:
    """Path graph (0,0)-(1,0)-(2,0)-(3,0), unit-length horizontal edges."""
    lines = [
        LineString([(0, 0), (1, 0)]),
        LineString([(1, 0), (2, 0)]),
        LineString([(2, 0), (3, 0)]),
    ]
    return gpd.GeoDataFrame(geometry=lines, crs=_CRS)


def _points(coords) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[Point(*c) for c in coords], crs=_CRS)


def _cost_points(coords, costs, col="activation_cost") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {col: list(costs)}, geometry=[Point(*c) for c in coords], crs=_CRS
    )


def _node_of(result, x, y):
    """Return (u, v) of the row whose child v sits at (x, y) — for assertions."""
    for _, row in result.iterrows():
        pt = row.geometry
        # child endpoint is whichever end matches (x, y)
        for cx, cy in (pt.coords[0], pt.coords[-1]):
            if abs(cx - x) < 1e-9 and abs(cy - y) < 1e-9:
                return row
    return None


class TestGreedyExpansion:
    def test_path_absorption_order_increasing(self):
        """Frontier at one end → nodes absorbed in increasing distance order."""
        cap = NetworkGreedyExpansionCapability()
        result = cap.execute(_path_network(), ref_gdf=_points([(0, 0)]))
        assert isinstance(result, gpd.GeoDataFrame)
        assert list(result["step_order"]) == [1, 2, 3]
        # Each step absorbs the next node along the path: (1,0), (2,0), (3,0).
        absorbed_x = [row.geometry.coords[-1][0] for _, row in result.iterrows()]
        # child endpoint x, in step order, is strictly increasing (1, 2, 3).
        assert absorbed_x == sorted(absorbed_x)
        assert absorbed_x == [1.0, 2.0, 3.0]
        # marginal cost == edge weight (unit) since no activation cost.
        assert all(abs(m - 1.0) < 1e-9 for m in result["marginal_cost"])
        assert all(abs(a) < 1e-9 for a in result["activation_cost"])

    def test_multi_node_frontier(self):
        """Two roots at both ends → the middle nodes get absorbed once each."""
        cap = NetworkGreedyExpansionCapability()
        result = cap.execute(
            _path_network(), ref_gdf=_points([(0, 0), (3, 0)])
        )
        # 4 nodes total, 2 are frontier roots → exactly 2 absorbed (the middle).
        assert len(result) == 2
        absorbed = {
            (round(row.geometry.coords[-1][0], 6), round(row.geometry.coords[-1][1], 6))
            for _, row in result.iterrows()
        }
        # No node absorbed twice.
        assert len(absorbed) == 2

    def test_activation_cost_changes_order(self):
        """Activation cost flips which neighbour is absorbed first."""
        # Star: R--A weight 1, R--B weight 2.
        lines = [
            LineString([(0, 0), (1, 0)]),   # R -> A (len 1)
            LineString([(0, 0), (0, 2)]),   # R -> B (len 2)
        ]
        net = gpd.GeoDataFrame(geometry=lines, crs=_CRS)
        frontier = _points([(0, 0)])

        cap = NetworkGreedyExpansionCapability()

        # Without activation cost: A (marginal 1) before B (marginal 2).
        base = cap.execute(net, ref_gdf=frontier)
        base_order = [
            (row["step_order"], tuple(row.geometry.coords[-1]))
            for _, row in base.iterrows()
        ]
        assert base_order[0][1] == (1.0, 0.0)   # A first
        assert base_order[1][1] == (0.0, 2.0)   # B second

        # With activation cost 5 on A, 0 on B: B (marginal 2) before A (marginal 6).
        cost = _cost_points([(1, 0), (0, 2)], [5.0, 0.0])
        flipped = cap.execute(net, ref_gdf=frontier, ref_gdfs=[frontier, cost])
        flip_order = [
            (row["step_order"], tuple(row.geometry.coords[-1]))
            for _, row in flipped.iterrows()
        ]
        assert flip_order[0][1] == (0.0, 2.0)   # B first now
        assert flip_order[1][1] == (1.0, 0.0)   # A second
        # A's row carries the activation cost and marginal = 1 + 5 = 6.
        a_row = _node_of(flipped, 1.0, 0.0)
        assert abs(a_row["activation_cost"] - 5.0) < 1e-9
        assert abs(a_row["marginal_cost"] - 6.0) < 1e-9

    def test_cost_via_ref_gdfs_only(self):
        """ref_gdfs[0] frontier + ref_gdfs[1] cost, without a separate ref_gdf."""
        net = _path_network()
        frontier = _points([(0, 0)])
        cost = _cost_points([(2, 0)], [10.0])
        cap = NetworkGreedyExpansionCapability()
        result = cap.execute(net, ref_gdfs=[frontier, cost])
        assert list(result["step_order"]) == [1, 2, 3]
        mid = _node_of(result, 2.0, 0.0)
        assert abs(mid["activation_cost"] - 10.0) < 1e-9

    def test_empty_frontier_raises(self):
        cap = NetworkGreedyExpansionCapability()
        empty = gpd.GeoDataFrame({"geometry": []}, crs=_CRS)
        with pytest.raises(ValueError, match="frontier"):
            cap.execute(_path_network(), ref_gdf=empty)

    def test_no_frontier_layer_raises(self):
        cap = NetworkGreedyExpansionCapability()
        with pytest.raises(ValueError, match="frontier"):
            cap.execute(_path_network())

    def test_absent_frontier_node_raises(self):
        """A frontier point that is not a network node fast-fails."""
        cap = NetworkGreedyExpansionCapability()
        off_network = _points([(99, 99)])
        with pytest.raises(ValueError, match="not network"):
            cap.execute(_path_network(), ref_gdf=off_network)

    def test_nan_activation_cost_raises(self):
        cap = NetworkGreedyExpansionCapability()
        frontier = _points([(0, 0)])
        cost = _cost_points([(2, 0)], [float("nan")])
        with pytest.raises(ValueError, match="NaN"):
            cap.execute(_path_network(), ref_gdfs=[frontier, cost])

    def test_nan_edge_weight_raises(self):
        cap = NetworkGreedyExpansionCapability()
        lines = [LineString([(0, 0), (1, 0)]), LineString([(1, 0), (2, 0)])]
        net = gpd.GeoDataFrame({"w": [1.0, float("nan")], "geometry": lines}, crs=_CRS)
        with pytest.raises(ValueError, match="NaN"):
            cap.execute(net, ref_gdf=_points([(0, 0)]), weight_col="w")

    def test_empty_graph_returns_empty(self):
        cap = NetworkGreedyExpansionCapability()
        empty_net = gpd.GeoDataFrame({"geometry": []}, crs=_CRS)
        result = cap.execute(empty_net, ref_gdf=_points([(0, 0)]))
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0
        assert "step_order" in result.columns

    def test_disconnected_component_unreached(self):
        """Nodes in a component not reachable from the frontier never appear."""
        # Component A: (0,0)-(1,0)-(2,0). Component B: (100,0)-(101,0).
        lines = [
            LineString([(0, 0), (1, 0)]),
            LineString([(1, 0), (2, 0)]),
            LineString([(100, 0), (101, 0)]),
        ]
        net = gpd.GeoDataFrame(geometry=lines, crs=_CRS)
        cap = NetworkGreedyExpansionCapability()
        result = cap.execute(net, ref_gdf=_points([(0, 0)]))
        # Only component A's two non-root nodes are absorbed.
        assert len(result) == 2
        absorbed_x = {round(row.geometry.coords[-1][0], 6) for _, row in result.iterrows()}
        # Component B coordinates (100, 101) must be absent.
        assert absorbed_x == {1.0, 2.0}
        assert 100.0 not in absorbed_x
        assert 101.0 not in absorbed_x

    def test_frontier_in_other_crs_is_reprojected(self):
        """Projected network + frontier in a different CRS (same physical points).

        The network is already metric (EPSG:2154, reproject=False), so the
        frontier reprojection must be driven by the frontier's OWN CRS gap, not
        by the network's angularity — otherwise the 4326 frontier is left
        unaligned and the exact node-identity match fails on legitimate input.
        """
        pts_ll = gpd.GeoSeries(
            [Point(2.0, 46.0), Point(2.01, 46.0), Point(2.02, 46.0), Point(2.03, 46.0)],
            crs="EPSG:4326",
        )
        pts_m = pts_ll.to_crs("EPSG:2154")
        lines = [LineString([pts_m.iloc[i], pts_m.iloc[i + 1]]) for i in range(3)]
        net = gpd.GeoDataFrame(geometry=lines, crs="EPSG:2154")
        frontier_ll = gpd.GeoDataFrame(geometry=[Point(2.0, 46.0)], crs="EPSG:4326")

        cap = NetworkGreedyExpansionCapability()
        result = cap.execute(net, ref_gdf=frontier_ll)
        # The 4326 frontier matched node 0 after reprojection → 3 nodes absorbed.
        assert list(result["step_order"]) == [1, 2, 3]

    def test_cost_layer_in_other_crs_is_reprojected(self):
        """Cost point in a different CRS must snap to the right node, not vanish.

        With the network projected and the cost layer in EPSG:4326, the cost is
        silently lost (assigned 0) unless the cost layer is reprojected on its
        own CRS gap. Here the activation cost 10 sits at node 2's physical
        location expressed in 4326 and must land on node 2.
        """
        pts_ll = gpd.GeoSeries(
            [Point(2.0, 46.0), Point(2.01, 46.0), Point(2.02, 46.0), Point(2.03, 46.0)],
            crs="EPSG:4326",
        )
        pts_m = pts_ll.to_crs("EPSG:2154")
        lines = [LineString([pts_m.iloc[i], pts_m.iloc[i + 1]]) for i in range(3)]
        net = gpd.GeoDataFrame(geometry=lines, crs="EPSG:2154")
        # Frontier as the exact node-0 coordinate in the network CRS (isolates
        # the cost-layer fix from the frontier fix).
        frontier = gpd.GeoDataFrame(
            geometry=[Point(pts_m.iloc[0].x, pts_m.iloc[0].y)], crs="EPSG:2154"
        )
        cost = gpd.GeoDataFrame(
            {"activation_cost": [10.0]},
            geometry=[Point(2.02, 46.0)],
            crs="EPSG:4326",
        )
        cap = NetworkGreedyExpansionCapability()
        result = cap.execute(net, ref_gdf=frontier, cost_gdf=cost)
        node2 = pts_m.iloc[2]
        row = _node_of(result, node2.x, node2.y)
        assert row is not None
        assert abs(row["activation_cost"] - 10.0) < 1e-9
        # Exactly one node carries the cost — it was not mis-assigned elsewhere.
        assert sum(1 for c in result["activation_cost"] if abs(c - 10.0) < 1e-9) == 1

    def test_schema(self):
        cap = NetworkGreedyExpansionCapability()
        schema = cap.get_schema()
        assert "ref_layers" in schema["properties"]
        assert "cost_col" in schema["properties"]
        assert "weight_col" in schema["properties"]
        assert "crs_meters" in schema["properties"]
