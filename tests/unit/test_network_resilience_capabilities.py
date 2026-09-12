"""Tests for capabilities/network_resilience.py — disjoint_paths + network_bridges."""

from __future__ import annotations

import pytest

pytest.importorskip("networkx", reason="networkx not installed")

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString

from gispulse.capabilities.network_resilience import (
    DisjointPathsCapability,
    NetworkBridgesCapability,
    NetworkRedundancyCapability,
)


@pytest.fixture(autouse=True)
def pro_tier(monkeypatch):
    """Both resilience capabilities require Pro tier — activate it everywhere."""
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv("GISPULSE_LICENSE_KEY", "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


_CRS = "EPSG:3857"  # projected: no reprojection, node coordinates stay exact


def _network(segments, weights=None, weight_col="w") -> gpd.GeoDataFrame:
    """Line network from [(x0, y0), (x1, y1)] pairs, optional explicit weights."""
    data = {}
    if weights is not None:
        data[weight_col] = list(weights)
    return gpd.GeoDataFrame(
        data, geometry=[LineString([a, b]) for a, b in segments], crs=_CRS
    )


def _path_endpoints(result, path_id):
    """Ordered interior + terminal coordinates visited by one path's arcs."""
    part = result[result["path_id"] == path_id].sort_values("path_order")
    pts = []
    for geom in part.geometry:
        pts.append(geom.coords[0])
        pts.append(geom.coords[-1])
    return pts


class TestDisjointPaths:
    def test_diamond_two_node_disjoint_paths(self):
        """S-A-T / S-B-T diamond → two node-disjoint paths, no shared interior."""
        S, A, B, T = (0, 0), (1, 1), (1, -1), (2, 0)
        net = _network([(S, A), (A, T), (S, B), (B, T)])
        result = DisjointPathsCapability().execute(
            net, start_x=0, start_y=0, end_x=2, end_y=0, k=2
        )
        assert set(result["path_id"]) == {0, 1}
        assert set(result["paths_found"]) == {2}
        # Interior nodes of the two paths are disjoint: one goes through A,
        # the other through B.
        interiors = []
        for pid in (0, 1):
            pts = set(_path_endpoints(result, pid)) - {S, T}
            interiors.append(pts)
        assert interiors[0].isdisjoint(interiors[1])
        assert interiors[0] | interiors[1] == {A, B}
        # Both paths cost 2 edges of equal length → equal path_cost.
        costs = sorted(set(result["path_cost"]))
        assert len(costs) == 1

    def test_suurballe_beats_greedy_removal(self):
        """The classic trap: the shortest path uses BOTH interior nodes.

        Weights: S-A=1, A-B=1, B-T=1 (shortest path S-A-B-T, cost 3) and
        S-B=5, A-T=5. Removing the first path's interior nodes leaves no
        second route, so a naive scan reports a SPOF. Suurballe reroutes and
        returns the optimal pair {S-A-T, S-B-T}, total cost 12.
        """
        S, A, B, T = (0, 0), (1, 1), (2, -1), (3, 0)
        net = _network(
            [(S, A), (A, B), (B, T), (S, B), (A, T)],
            weights=[1.0, 1.0, 1.0, 5.0, 5.0],
        )
        result = DisjointPathsCapability().execute(
            net, start_x=0, start_y=0, end_x=3, end_y=0, k=2, weight_col="w"
        )
        assert set(result["paths_found"]) == {2}
        assert sorted(set(result["path_cost"])) == [6.0, 6.0] or set(
            result["path_cost"]
        ) == {6.0}
        # Total cost of the pair is 12 — the A-B edge is used by neither path.
        total = sum(
            result[result["path_id"] == pid]["edge_weight"].sum() for pid in (0, 1)
        )
        assert total == pytest.approx(12.0)

    def test_shared_node_edge_vs_node_mode(self):
        """Two routes forced through one middle node M.

        mode='node' can only deliver one path; mode='edge' delivers two
        edge-disjoint paths sharing M.
        """
        S, a, b, M, c, d, T = (
            (0, 0),
            (1, 1),
            (1, -1),
            (2, 0),
            (3, 1),
            (3, -1),
            (4, 0),
        )
        net = _network(
            [(S, a), (a, M), (S, b), (b, M), (M, c), (c, T), (M, d), (d, T)]
        )
        node_result = DisjointPathsCapability().execute(
            net, start_x=0, start_y=0, end_x=4, end_y=0, k=2, mode="node"
        )
        assert set(node_result["paths_found"]) == {1}
        edge_result = DisjointPathsCapability().execute(
            net, start_x=0, start_y=0, end_x=4, end_y=0, k=2, mode="edge"
        )
        assert set(edge_result["paths_found"]) == {2}

    def test_single_route_reports_spof_not_error(self):
        """A bare path graph has one route: paths_found == 1, no exception."""
        net = _network([((0, 0), (1, 0)), ((1, 0), (2, 0))])
        result = DisjointPathsCapability().execute(
            net, start_x=0, start_y=0, end_x=2, end_y=0, k=2
        )
        assert set(result["paths_found"]) == {1}
        assert set(result["path_id"]) == {0}
        assert list(result.sort_values("path_order")["path_order"]) == [0, 1]

    def test_unreachable_returns_empty(self):
        """Disconnected components → empty result with the column contract."""
        net = _network([((0, 0), (1, 0)), ((5, 5), (6, 5))])
        result = DisjointPathsCapability().execute(
            net, start_x=0, start_y=0, end_x=6, end_y=5, k=2
        )
        assert result.empty
        assert list(result.columns) == [
            "path_id",
            "path_order",
            "edge_weight",
            "path_cost",
            "paths_found",
            "geometry",
        ]

    def test_same_snap_node_raises(self):
        net = _network([((0, 0), (1, 0))])
        with pytest.raises(ValueError, match="same network node"):
            DisjointPathsCapability().execute(
                net, start_x=0, start_y=0, end_x=0.1, end_y=0.1, k=2
            )

    def test_nan_weight_raises(self):
        net = _network(
            [((0, 0), (1, 0)), ((1, 0), (2, 0))], weights=[1.0, float("nan")]
        )
        with pytest.raises(ValueError, match="NaN edge weight"):
            DisjointPathsCapability().execute(
                net, start_x=0, start_y=0, end_x=2, end_y=0, weight_col="w"
            )

    def test_negative_weight_raises(self):
        net = _network([((0, 0), (1, 0)), ((1, 0), (2, 0))], weights=[1.0, -1.0])
        with pytest.raises(ValueError, match="negative edge weight"):
            DisjointPathsCapability().execute(
                net, start_x=0, start_y=0, end_x=2, end_y=0, weight_col="w"
            )

    def test_invalid_k_and_mode_raise(self):
        net = _network([((0, 0), (1, 0))])
        with pytest.raises(ValueError, match="k must be >= 1"):
            DisjointPathsCapability().execute(
                net, start_x=0, start_y=0, end_x=1, end_y=0, k=0
            )
        with pytest.raises(ValueError, match="mode must be"):
            DisjointPathsCapability().execute(
                net, start_x=0, start_y=0, end_x=1, end_y=0, mode="both"
            )

    def test_k1_is_plain_shortest_path(self):
        """k=1 degenerates to the shortest path (by weight)."""
        S, A, B, T = (0, 0), (1, 1), (2, -1), (3, 0)
        net = _network(
            [(S, A), (A, B), (B, T), (S, B), (A, T)],
            weights=[1.0, 1.0, 1.0, 5.0, 5.0],
        )
        result = DisjointPathsCapability().execute(
            net, start_x=0, start_y=0, end_x=3, end_y=0, k=1, weight_col="w"
        )
        assert set(result["paths_found"]) == {1}
        assert result["path_cost"].iloc[0] == pytest.approx(3.0)

    def test_deterministic_output(self):
        """Same input twice → byte-identical frame (AX #6)."""
        S, A, B, T = (0, 0), (1, 1), (1, -1), (2, 0)
        net = _network([(S, A), (A, T), (S, B), (B, T)])
        cap = DisjointPathsCapability()
        r1 = cap.execute(net, start_x=0, start_y=0, end_x=2, end_y=0, k=2)
        r2 = cap.execute(net, start_x=0, start_y=0, end_x=2, end_y=0, k=2)
        assert r1.equals(r2)

    def test_empty_network(self):
        empty = gpd.GeoDataFrame(geometry=[], crs=_CRS)
        result = DisjointPathsCapability().execute(
            empty, start_x=0, start_y=0, end_x=1, end_y=0
        )
        assert result.empty


class TestNetworkBridges:
    def test_path_graph_all_bridges(self):
        net = _network([((0, 0), (1, 0)), ((1, 0), (2, 0))])
        result = NetworkBridgesCapability().execute(net)
        assert list(result["is_bridge"]) == [True, True]

    def test_triangle_no_bridges(self):
        A, B, C = (0, 0), (1, 0), (0.5, 1)
        net = _network([(A, B), (B, C), (C, A)])
        result = NetworkBridgesCapability().execute(net)
        assert list(result["is_bridge"]) == [False, False, False]

    def test_triangle_with_spur(self):
        """Only the spur hanging off the cycle is a bridge."""
        A, B, C, D = (0, 0), (1, 0), (0.5, 1), (2, 0)
        net = _network([(A, B), (B, C), (C, A), (B, D)])
        result = NetworkBridgesCapability().execute(net)
        assert list(result["is_bridge"]) == [False, False, False, True]

    def test_parallel_lines_are_never_bridges(self):
        """Two distinct rows joining the same nodes back each other up."""
        A, B = (0, 0), (2, 0)
        parallel_1 = LineString([A, (1, 1), B])
        parallel_2 = LineString([A, (1, -1), B])
        net = gpd.GeoDataFrame(geometry=[parallel_1, parallel_2], crs=_CRS)
        result = NetworkBridgesCapability().execute(net)
        assert list(result["is_bridge"]) == [False, False]

    def test_multilinestring_any_part_bridge(self):
        """A multi-part row is flagged when any of its parts is a bridge."""
        A, B, C, D = (0, 0), (1, 0), (0.5, 1), (2, 0)
        triangle = [LineString([A, B]), LineString([B, C]), LineString([C, A])]
        multi = MultiLineString([[B, D]])  # spur packed in a MultiLineString
        net = gpd.GeoDataFrame(geometry=triangle + [multi], crs=_CRS)
        result = NetworkBridgesCapability().execute(net)
        assert list(result["is_bridge"]) == [False, False, False, True]

    def test_empty_and_null_geometry_false(self):
        A, B = (0, 0), (1, 0)
        net = gpd.GeoDataFrame(geometry=[LineString([A, B]), None], crs=_CRS)
        result = NetworkBridgesCapability().execute(net)
        assert list(result["is_bridge"]) == [True, False]

    def test_custom_column_name_and_input_preserved(self):
        net = _network([((0, 0), (1, 0))], weights=[7.0])
        result = NetworkBridgesCapability().execute(net, bridge_col="spof")
        assert list(result["spof"]) == [True]
        assert list(result["w"]) == [7.0]  # existing attributes untouched

    def test_two_components_each_audited(self):
        """Bridges are per-component: a cycle plus a distant lone segment."""
        A, B, C = (0, 0), (1, 0), (0.5, 1)
        far = ((10, 10), (11, 10))
        net = _network([(A, B), (B, C), (C, A), far])
        result = NetworkBridgesCapability().execute(net)
        assert list(result["is_bridge"]) == [False, False, False, True]


def _pts(coords) -> gpd.GeoDataFrame:
    from shapely.geometry import Point

    return gpd.GeoDataFrame(geometry=[Point(*c) for c in coords], crs=_CRS)


class TestNetworkRedundancy:
    def _ring_with_spur(self):
        """Ring S-A-T-B-S, spur T-D, plus a far disconnected segment."""
        S, A, T, B, D = (0, 0), (1, 1), (2, 0), (1, -1), (3, 0)
        far = ((50, 50), (51, 50))
        net = _network([(S, A), (A, T), (T, B), (B, S), (T, D), far])
        return net, {"S": S, "A": A, "T": T, "B": B, "D": D, "far": far[0]}

    def test_protected_spof_and_unreachable(self):
        """T on the ring = protected (2), spur end D = SPOF (1), far = 0."""
        net, p = self._ring_with_spur()
        sites = _pts([p["T"], p["D"], p["far"]])
        result = NetworkRedundancyCapability().execute(
            sites, ref_gdfs=[net, _pts([p["S"]])]
        )
        assert list(result["redundancy"]) == [2, 1, 0]

    def test_site_on_facility_counts_k(self):
        net, p = self._ring_with_spur()
        sites = _pts([p["S"]])
        result = NetworkRedundancyCapability().execute(
            sites, ref_gdfs=[net, _pts([p["S"]])], k=2
        )
        assert list(result["redundancy"]) == [2]

    def test_second_facility_rescues_spof(self):
        """A facility on the spur end makes D reachable twice? No — D-T is
        still the only arm to the ring facility, but a facility AT D makes
        D itself protected (site==facility) and T gains nothing new."""
        net, p = self._ring_with_spur()
        sites = _pts([p["D"]])
        result = NetworkRedundancyCapability().execute(
            sites, ref_gdfs=[net, _pts([p["S"], p["D"]])]
        )
        assert list(result["redundancy"]) == [2]  # sits on a facility

    def test_edge_mode_shared_node(self):
        """Two routes forced through middle node M: node mode 1, edge mode 2."""
        S, a, b, M, c, d, T = (
            (0, 0), (1, 1), (1, -1), (2, 0), (3, 1), (3, -1), (4, 0),
        )
        net = _network(
            [(S, a), (a, M), (S, b), (b, M), (M, c), (c, T), (M, d), (d, T)]
        )
        sites = _pts([S])
        node_result = NetworkRedundancyCapability().execute(
            sites, ref_gdfs=[net, _pts([T])], mode="node"
        )
        edge_result = NetworkRedundancyCapability().execute(
            sites, ref_gdfs=[net, _pts([T])], mode="edge"
        )
        assert list(node_result["redundancy"]) == [1]
        assert list(edge_result["redundancy"]) == [2]

    def test_annotated_copy_preserves_input(self):
        net, p = self._ring_with_spur()
        sites = _pts([p["T"], p["D"]])
        sites["name"] = ["t-site", "d-site"]
        result = NetworkRedundancyCapability().execute(
            sites, ref_gdfs=[net, _pts([p["S"]])], redundancy_col="protection"
        )
        assert list(result["name"]) == ["t-site", "d-site"]
        assert list(result["protection"]) == [2, 1]
        assert "protection" not in sites.columns  # input untouched

    def test_missing_layers_raise(self):
        net, p = self._ring_with_spur()
        sites = _pts([p["T"]])
        with pytest.raises(ValueError, match="ref_layers"):
            NetworkRedundancyCapability().execute(sites)
        with pytest.raises(ValueError, match="facilities"):
            NetworkRedundancyCapability().execute(
                sites, ref_gdfs=[net, _pts([])]
            )

    def test_empty_network_all_zero(self):
        empty_net = gpd.GeoDataFrame(geometry=[], crs=_CRS)
        sites = _pts([(0, 0)])
        result = NetworkRedundancyCapability().execute(
            sites, ref_gdfs=[empty_net, _pts([(1, 1)])]
        )
        assert list(result["redundancy"]) == [0]
