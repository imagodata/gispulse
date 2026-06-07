"""Unit tests for steiner_tree (capability G)."""

from __future__ import annotations

import geopandas as gpd
import pytest

pytest.importorskip("networkx", reason="networkx not installed")

from shapely.geometry import LineString, Point

import gispulse
from gispulse.capabilities.network import SteinerTreeCapability


@pytest.fixture(autouse=True)
def pro_tier(monkeypatch):
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv(
        "GISPULSE_LICENSE_KEY",
        "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ."
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )


def _run(gdf, ref_gdf, **params):
    return SteinerTreeCapability().execute(gdf, ref_gdf=ref_gdf, **params)


@pytest.fixture
def network() -> gpd.GeoDataFrame:
    # A(0,0)-B(10,0)-C(20,0) horizontal, B-D(10,10) up, C-E(20,10) up.
    return gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (10, 0)]),    # A-B
            LineString([(10, 0), (20, 0)]),   # B-C
            LineString([(10, 0), (10, 10)]),  # B-D
            LineString([(20, 0), (20, 10)]),  # C-E
        ],
        crs="EPSG:3857",
    )


def _terminals(coords) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[Point(*c) for c in coords], crs="EPSG:3857")


def test_spans_only_terminals_not_whole_network(network):
    # Connect A and D: tree must be {A-B, B-D} (2 edges), excluding B-C and C-E.
    tree = _run(network, _terminals([(0, 0), (10, 10)]))
    assert len(tree) == 2
    assert tree["weight"].sum() == pytest.approx(20.0)


def test_three_terminals(network):
    # A, C, E → A-B, B-C, C-E (3 edges, total 30); excludes B-D.
    tree = _run(network, _terminals([(0, 0), (20, 0), (20, 10)]))
    assert len(tree) == 3
    assert tree["weight"].sum() == pytest.approx(30.0)


def test_disconnected_terminals_raise(network):
    isolated = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(100, 100), (110, 100)]),  # separate component
        ],
        crs="EPSG:3857",
    )
    with pytest.raises(ValueError, match="not all connected"):
        _run(isolated, _terminals([(0, 0), (100, 100)]))


def test_fewer_than_two_terminals_returns_empty(network):
    tree = _run(network, _terminals([(0, 0)]))
    assert len(tree) == 0


def test_missing_terminals_raises(network):
    with pytest.raises(ValueError, match="terminals layer"):
        SteinerTreeCapability().execute(network, ref_gdf=None)


def test_empty_network_returns_empty():
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:3857")
    tree = _run(empty, _terminals([(0, 0), (1, 1)]))
    assert len(tree) == 0


def test_reprojection_from_4326():
    net = gpd.GeoDataFrame(
        geometry=[
            LineString([(2.0, 48.0), (2.01, 48.0)]),
            LineString([(2.01, 48.0), (2.02, 48.0)]),
        ],
        crs="EPSG:4326",
    )
    terms = gpd.GeoDataFrame(
        geometry=[Point(2.0, 48.0), Point(2.02, 48.0)], crs="EPSG:4326"
    )
    tree = SteinerTreeCapability().execute(net, ref_gdf=terms)
    assert tree.crs == net.crs
    assert len(tree) == 2  # both segments needed to connect the two ends


def test_via_apply(network):
    tree = gispulse.apply(
        "steiner_tree", network, ref_gdf=_terminals([(0, 0), (10, 10)])
    )
    assert len(tree) == 2
