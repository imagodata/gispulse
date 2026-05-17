"""Tests for capabilities/network.py — S11 capabilities.

Covers: ShortestPathCapability, IsochroneCapability,
        NetworkAllocationCapability, ConnectivityCheckCapability.

Includes tier-gating tests (all network caps require Pro).
"""

from __future__ import annotations

import pytest

pytest.importorskip("networkx", reason="networkx not installed")

import geopandas as gpd
from shapely.geometry import LineString, Point

from gispulse.capabilities.network import (
    ConnectivityCheckCapability,
    IsochroneCapability,
    NetworkAllocationCapability,
    ShortestPathCapability,
)
from gispulse.persistence.tier import TierError


# ---------------------------------------------------------------------------
# Synthetic network fixtures
# ---------------------------------------------------------------------------


def _grid_network() -> gpd.GeoDataFrame:
    """Line network on a 3×2 grid.

    Nodes at (0,0), (1,0), (2,0), (0,1), (1,1), (2,1).
    Horizontal and vertical arcs of length 1.
    """
    lines = [
        LineString([(0, 0), (1, 0)]),
        LineString([(1, 0), (2, 0)]),
        LineString([(0, 1), (1, 1)]),
        LineString([(1, 1), (2, 1)]),
        LineString([(0, 0), (0, 1)]),
        LineString([(1, 0), (1, 1)]),
        LineString([(2, 0), (2, 1)]),
    ]
    return gpd.GeoDataFrame(geometry=lines, crs="EPSG:4326")


def _disconnected_network() -> gpd.GeoDataFrame:
    """Two disconnected line segments."""
    lines = [
        LineString([(0, 0), (1, 0)]),
        LineString([(5, 5), (6, 5)]),  # isolated from the first segment
    ]
    return gpd.GeoDataFrame(geometry=lines, crs="EPSG:4326")


def _pro_env(monkeypatch) -> None:
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv("GISPULSE_LICENSE_KEY", "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


def _community_env(monkeypatch) -> None:
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.delenv("GISPULSE_LICENSE_KEY", raising=False)


# ---------------------------------------------------------------------------
# Tier gating — all network capabilities require Pro
# ---------------------------------------------------------------------------


class TestNetworkTierGating:
    def test_shortest_path_blocked_in_community(self, monkeypatch):
        _community_env(monkeypatch)
        gdf = _grid_network()
        cap = ShortestPathCapability()
        with pytest.raises(TierError):
            cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=1)

    def test_isochrone_blocked_in_community(self, monkeypatch):
        _community_env(monkeypatch)
        gdf = _grid_network()
        cap = IsochroneCapability()
        with pytest.raises(TierError):
            cap.execute(gdf, start_x=0, start_y=0, cost_budget=1.5)

    def test_network_allocation_blocked_in_community(self, monkeypatch):
        _community_env(monkeypatch)
        gdf = _grid_network()
        hubs = gpd.GeoDataFrame(
            [{"id": "H1", "geometry": Point(0, 0)}], crs="EPSG:4326"
        )
        cap = NetworkAllocationCapability()
        with pytest.raises(TierError):
            cap.execute(
                gpd.GeoDataFrame(
                    [{"geometry": Point(2, 1)}], geometry="geometry", crs="EPSG:4326"
                ),
                network_gdf=gdf,
                hubs_gdf=hubs,
            )

    def test_connectivity_check_blocked_in_community(self, monkeypatch):
        _community_env(monkeypatch)
        gdf = _grid_network()
        cap = ConnectivityCheckCapability()
        with pytest.raises(TierError):
            cap.execute(gdf)


# ---------------------------------------------------------------------------
# ShortestPathCapability
# ---------------------------------------------------------------------------


class TestShortestPath:
    def test_returns_geodataframe(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=1)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_path_not_empty(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=1)
        assert len(result) > 0

    def test_path_order_column_present(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=1)
        assert "path_order" in result.columns

    def test_path_order_sequential(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=0)
        assert list(result["path_order"]) == list(range(len(result)))

    def test_geometry_column_is_linestring(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=0)
        assert "geometry" in result.columns
        assert all(isinstance(g, LineString) for g in result.geometry)

    def test_same_start_end_returns_geodataframe(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=0, end_y=0)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_crs_preserved(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=1)
        assert result.crs == gdf.crs

    def test_schema_required_params(self):
        cap = ShortestPathCapability()
        schema = cap.get_schema()
        for param in ("start_x", "start_y", "end_x", "end_y"):
            assert param in schema["required"]


# ---------------------------------------------------------------------------
# IsochroneCapability
# ---------------------------------------------------------------------------


class TestIsochrone:
    def test_returns_geodataframe(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = IsochroneCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=1.5)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_dissolve_returns_single_polygon(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = IsochroneCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=1.5, dissolve=True)
        assert len(result) == 1
        assert "cost_budget" in result.columns

    def test_no_dissolve_returns_edges(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = IsochroneCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=1.5, dissolve=False)
        assert len(result) > 0
        assert "cost" in result.columns

    def test_large_budget_covers_all_edges(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = IsochroneCapability()
        # IsochroneCapability now reprojects to crs_meters (default EPSG:3857)
        # so the grid (1° spacing ≈ 111km in meters) needs a budget ≫ 999.
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=1_000_000.0, dissolve=False)
        assert len(result) == len(gdf)

    def test_zero_budget_returns_empty_isochrone(self, monkeypatch):
        """Beta P2 (2026-04-24): a budget of 0 used to produce a degenerate
        ~30 m buffer ring around the start node because the start itself
        has ``d == 0`` so the edge-buffer step still ran. "Reach with zero
        budget" must yield an empty isochrone — anything else is a false
        coverage signal that downstream classification picks up.
        """
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = IsochroneCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=0.0, dissolve=False)
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 0
        assert "geometry" in result.columns

    def test_negative_budget_raises(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = IsochroneCapability()
        with pytest.raises(ValueError, match="cost_budget"):
            cap.execute(gdf, start_x=0, start_y=0, cost_budget=-1.0)

    def test_crs_preserved(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = IsochroneCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=1.5, dissolve=False)
        assert result.crs == gdf.crs

    def test_schema_required_params(self):
        cap = IsochroneCapability()
        schema = cap.get_schema()
        # Schema no longer enforces required params — point and batch modes
        # are both valid; the runtime picks based on provided arguments.
        for param in ("start_x", "start_y", "cost_budget", "crs_meters"):
            assert param in schema["properties"]


# ---------------------------------------------------------------------------
# NetworkAllocationCapability
# ---------------------------------------------------------------------------


class TestNetworkAllocation:
    def _subscribers(self) -> gpd.GeoDataFrame:
        """Three subscriber points on the grid."""
        return gpd.GeoDataFrame(
            [
                {"sub_id": "S1", "geometry": Point(2, 1)},
                {"sub_id": "S2", "geometry": Point(1, 0)},
                {"sub_id": "S3", "geometry": Point(0, 1)},
            ],
            crs="EPSG:4326",
        )

    def _hubs(self) -> gpd.GeoDataFrame:
        """Two NRO hubs."""
        return gpd.GeoDataFrame(
            [
                {"hub_id": "NRO_A", "geometry": Point(0, 0)},
                {"hub_id": "NRO_B", "geometry": Point(2, 0)},
            ],
            crs="EPSG:4326",
        )

    def test_returns_geodataframe(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = NetworkAllocationCapability()
        result = cap.execute(
            self._subscribers(),
            network_gdf=gdf,
            hubs_gdf=self._hubs(),
            hub_id_col="hub_id",
        )
        assert isinstance(result, gpd.GeoDataFrame)

    def test_same_length_as_input(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        subs = self._subscribers()
        cap = NetworkAllocationCapability()
        result = cap.execute(subs, network_gdf=gdf, hubs_gdf=self._hubs(), hub_id_col="hub_id")
        assert len(result) == len(subs)

    def test_allocated_column_present(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = NetworkAllocationCapability()
        result = cap.execute(
            self._subscribers(), network_gdf=gdf, hubs_gdf=self._hubs(), hub_id_col="hub_id"
        )
        assert "allocated" in result.columns
        assert "allocated_hub_id" in result.columns
        assert "allocation_cost" in result.columns

    def test_all_subscribers_allocated(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = NetworkAllocationCapability()
        result = cap.execute(
            self._subscribers(), network_gdf=gdf, hubs_gdf=self._hubs(), hub_id_col="hub_id"
        )
        assert result["allocated"].all()

    def test_max_cost_limits_allocation(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        # Very small budget: subscriber at (2,1) is distance 3+ from hub (0,0)
        far_sub = gpd.GeoDataFrame(
            [{"geometry": Point(2, 1)}], geometry="geometry", crs="EPSG:4326"
        )
        single_hub = gpd.GeoDataFrame(
            [{"hub_id": "H1", "geometry": Point(0, 0)}], crs="EPSG:4326"
        )
        cap = NetworkAllocationCapability()
        result = cap.execute(
            far_sub,
            network_gdf=gdf,
            hubs_gdf=single_hub,
            hub_id_col="hub_id",
            max_cost=0.01,  # impossibly small
        )
        # With max_cost=0.01, only the hub node itself is reachable
        assert isinstance(result, gpd.GeoDataFrame)

    def test_missing_network_raises(self, monkeypatch):
        _pro_env(monkeypatch)
        cap = NetworkAllocationCapability()
        with pytest.raises(ValueError, match="network_gdf"):
            cap.execute(self._subscribers(), hubs_gdf=self._hubs())

    def test_missing_hubs_raises(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = NetworkAllocationCapability()
        with pytest.raises(ValueError, match="hubs_gdf"):
            cap.execute(self._subscribers(), network_gdf=gdf)

    def test_empty_input_returns_empty_with_columns(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = NetworkAllocationCapability()
        empty = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
        result = cap.execute(empty, network_gdf=gdf, hubs_gdf=self._hubs())
        assert "allocated" in result.columns
        assert len(result) == 0

    def test_schema_required_params(self):
        cap = NetworkAllocationCapability()
        schema = cap.get_schema()
        assert "network_gdf" in schema["required"]
        assert "hubs_gdf" in schema["required"]


# ---------------------------------------------------------------------------
# ConnectivityCheckCapability
# ---------------------------------------------------------------------------


class TestConnectivityCheck:
    def test_connected_network_returns_true(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ConnectivityCheckCapability()
        result = cap.execute(gdf)
        assert bool(result["is_connected"].iloc[0]) is True

    def test_disconnected_network_returns_false(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _disconnected_network()
        cap = ConnectivityCheckCapability()
        result = cap.execute(gdf)
        assert bool(result["is_connected"].iloc[0]) is False

    def test_n_components_connected(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ConnectivityCheckCapability()
        result = cap.execute(gdf)
        assert result["n_components"].iloc[0] == 1

    def test_n_components_disconnected(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _disconnected_network()
        cap = ConnectivityCheckCapability()
        result = cap.execute(gdf)
        assert result["n_components"].iloc[0] == 2

    def test_n_edges_matches_input(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ConnectivityCheckCapability()
        result = cap.execute(gdf)
        assert result["n_edges"].iloc[0] == len(gdf)

    def test_returns_geodataframe(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ConnectivityCheckCapability()
        result = cap.execute(gdf)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_single_row_summary_by_default(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _grid_network()
        cap = ConnectivityCheckCapability()
        result = cap.execute(gdf)
        assert len(result) == 1

    def test_return_components_mode(self, monkeypatch):
        _pro_env(monkeypatch)
        gdf = _disconnected_network()
        cap = ConnectivityCheckCapability()
        result = cap.execute(gdf, return_components=True)
        # Two disconnected segments → two components
        assert len(result) == 2
        assert "component_id" in result.columns
        assert "is_largest" in result.columns

    def test_largest_component_is_marked(self, monkeypatch):
        _pro_env(monkeypatch)
        # Grid (7 arcs) + isolated arc (1 arc) → largest = grid
        from shapely.geometry import LineString
        combined = gpd.GeoDataFrame(
            geometry=list(_grid_network().geometry) + [LineString([(10, 10), (11, 10)])],
            crs="EPSG:4326",
        )
        cap = ConnectivityCheckCapability()
        result = cap.execute(combined, return_components=True)
        largest = result[result["is_largest"]]
        assert largest["n_edges"].iloc[0] == 7

    def test_empty_network_returns_zeros(self, monkeypatch):
        _pro_env(monkeypatch)
        cap = ConnectivityCheckCapability()
        empty = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
        result = cap.execute(empty)
        assert result["n_components"].iloc[0] == 0
        assert result["n_edges"].iloc[0] == 0

    def test_schema_has_return_components(self):
        cap = ConnectivityCheckCapability()
        schema = cap.get_schema()
        assert "return_components" in schema["properties"]
