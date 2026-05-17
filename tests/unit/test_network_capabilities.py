"""Tests for capabilities/network.py — ShortestPathCapability, IsochroneCapability."""

from __future__ import annotations

import pytest

pytest.importorskip("networkx", reason="networkx not installed")

import geopandas as gpd
from shapely.geometry import LineString

from gispulse.capabilities.network import IsochroneCapability, ShortestPathCapability


@pytest.fixture(autouse=True)
def pro_tier(monkeypatch):
    """All network capabilities require Pro tier — activate it for every test in this module."""
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv("GISPULSE_LICENSE_KEY", "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


def _grid_network() -> gpd.GeoDataFrame:
    """Réseau en grille 3×3 pour les tests.

    Nœuds aux coordonnées (0,0), (1,0), (2,0), (0,1), (1,1), (2,1).
    Arcs horizontaux et verticaux de longueur 1.
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


class TestShortestPath:
    def test_returns_geodataframe(self):
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=1)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_path_not_empty(self):
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=1)
        assert len(result) > 0

    def test_path_order_column_present(self):
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=1)
        assert "path_order" in result.columns

    def test_same_start_end_empty_or_trivial(self):
        gdf = _grid_network()
        cap = ShortestPathCapability()
        # Start == end: NetworkX path is a single node, no edges
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=0, end_y=0)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_geometry_column_present(self):
        gdf = _grid_network()
        cap = ShortestPathCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, end_x=2, end_y=0)
        assert "geometry" in result.columns
        assert all(isinstance(g, LineString) for g in result.geometry)


class TestIsochrone:
    def test_returns_geodataframe(self):
        gdf = _grid_network()
        cap = IsochroneCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=1.5)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_dissolve_returns_polygon(self):
        gdf = _grid_network()
        cap = IsochroneCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=1.5, dissolve=True)
        assert len(result) == 1
        assert "cost_budget" in result.columns

    def test_no_dissolve_returns_edges(self):
        gdf = _grid_network()
        cap = IsochroneCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=1.5, dissolve=False)
        assert len(result) > 0
        assert "cost" in result.columns

    def test_zero_budget_returns_start_only(self):
        gdf = _grid_network()
        cap = IsochroneCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=0.0, dissolve=False)
        # Seul le nœud de départ est atteignable (coût 0) — des arcs peuvent y être rattachés
        assert isinstance(result, gpd.GeoDataFrame)

    def test_large_budget_covers_all(self):
        gdf = _grid_network()
        cap = IsochroneCapability()
        # IsochroneCapability now reprojects to crs_meters (default EPSG:3857)
        # so the grid (1° spacing ≈ 111km in meters) needs a budget ≫ 999.
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=1_000_000.0, dissolve=False)
        assert len(result) == len(gdf)

    def test_crs_preserved(self):
        gdf = _grid_network()
        cap = IsochroneCapability()
        result = cap.execute(gdf, start_x=0, start_y=0, cost_budget=1.5, dissolve=False)
        assert result.crs == gdf.crs

    def test_schema(self):
        cap = IsochroneCapability()
        schema = cap.get_schema()
        # IsochroneCapability supports two modes (point via start_x/y,
        # batch via ref_gdf) so no param is strictly required in the schema —
        # the runtime picks the mode based on what's provided.
        assert "cost_budget" in schema["properties"]
        assert "cost_budgets" in schema["properties"]
        assert "start_x" in schema["properties"]
        assert "start_y" in schema["properties"]
        assert "crs_meters" in schema["properties"]


class TestIsochroneMultiBudget:
    """Batch mode + cost_budgets list: one Dijkstra pass → N concentric rings."""

    @staticmethod
    def _square_network(extent: int = 6, step: int = 100) -> gpd.GeoDataFrame:
        segs = []
        for x in range(extent + 1):
            segs.append(LineString([(x * step, 0), (x * step, extent * step)]))
        for y in range(extent + 1):
            segs.append(LineString([(0, y * step), (extent * step, y * step)]))
        return gpd.GeoDataFrame({"id": range(len(segs))}, geometry=segs, crs="EPSG:2154")

    @staticmethod
    def _sources(x: float, y: float) -> gpd.GeoDataFrame:
        from shapely.geometry import Point
        return gpd.GeoDataFrame({"name": ["src"]}, geometry=[Point(x, y)], crs="EPSG:2154")

    def test_emits_one_feature_per_budget(self):
        cap = IsochroneCapability()
        out = cap.execute(
            self._sources(300, 300),
            ref_gdf=self._square_network(),
            cost_budgets=[100, 300, 600],
            crs_meters="EPSG:2154",
            edge_buffer_m=5,
            dissolve=True,
        )
        assert len(out) == 3
        assert out["cost_budget"].tolist() == [100.0, 300.0, 600.0]

    def test_budget_order_independent(self):
        """Unsorted input budgets come back sorted ascending."""
        cap = IsochroneCapability()
        out = cap.execute(
            self._sources(300, 300),
            ref_gdf=self._square_network(),
            cost_budgets=[600, 100, 300],
            crs_meters="EPSG:2154",
            edge_buffer_m=5,
            dissolve=True,
        )
        assert out["cost_budget"].tolist() == [100.0, 300.0, 600.0]

    def test_rings_are_monotonic_in_area(self):
        cap = IsochroneCapability()
        out = cap.execute(
            self._sources(300, 300),
            ref_gdf=self._square_network(),
            cost_budgets=[100, 300, 600],
            crs_meters="EPSG:2154",
            edge_buffer_m=5,
            dissolve=True,
        )
        areas = out.geometry.area.tolist()
        assert all(areas[i] <= areas[i + 1] for i in range(len(areas) - 1))

    def test_dedups_duplicate_budgets(self):
        cap = IsochroneCapability()
        out = cap.execute(
            self._sources(300, 300),
            ref_gdf=self._square_network(),
            cost_budgets=[300, 300, 600],
            crs_meters="EPSG:2154",
            edge_buffer_m=5,
            dissolve=True,
        )
        assert out["cost_budget"].tolist() == [300.0, 600.0]

    def test_cost_budgets_requires_dissolve(self):
        cap = IsochroneCapability()
        with pytest.raises(ValueError, match="requires dissolve=True"):
            cap.execute(
                self._sources(300, 300),
                ref_gdf=self._square_network(),
                cost_budgets=[100, 300],
                crs_meters="EPSG:2154",
                dissolve=False,
            )

    def test_single_budget_path_unchanged(self):
        """Regression: passing only cost_budget (legacy) still works identically."""
        cap = IsochroneCapability()
        out = cap.execute(
            self._sources(300, 300),
            ref_gdf=self._square_network(),
            cost_budget=300,
            crs_meters="EPSG:2154",
            edge_buffer_m=5,
            dissolve=True,
        )
        assert len(out) == 1
        assert out["cost_budget"].iloc[0] == 300.0
