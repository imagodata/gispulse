"""Unit tests for cluster_network_dbscan (capability H)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

pytest.importorskip("networkx", reason="networkx not installed")
pytest.importorskip("sklearn", reason="scikit-learn not installed")

import gispulse
from gispulse.capabilities.clustering import NetworkDBSCANClusterCapability


def _run(gdf, ref_gdf, **params):
    return NetworkDBSCANClusterCapability().execute(gdf, ref_gdf=ref_gdf, **params)


def _points(coords) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"pid": list(range(len(coords)))},
        geometry=[Point(*c) for c in coords],
        crs="EPSG:3857",
    )


@pytest.fixture
def two_branches() -> gpd.GeoDataFrame:
    # Two parallel, DISCONNECTED branches 10 m apart.
    return gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 10), (100, 10)]),
            LineString([(0, 0), (100, 0)]),
        ],
        crs="EPSG:3857",
    )


def test_disconnected_branches_split_what_euclidean_would_merge(two_branches):
    # Points on the upper and lower branch are only 10 m apart in straight
    # line (euclidean DBSCAN with eps=30 would merge them), but there is no
    # path between the branches → they must land in different clusters.
    pts = _points([(10, 10), (20, 10), (10, 0), (20, 0)])
    out = _run(pts, two_branches, eps_m=30.0, min_samples=2)
    labels = out["cluster"].tolist()
    upper = set(labels[:2])
    lower = set(labels[2:])
    assert len(upper) == 1 and len(lower) == 1  # each branch is one cluster
    assert upper.isdisjoint(lower)              # and they are different
    assert -1 not in labels                     # nobody is noise here


def test_network_distance_along_chain():
    # Chain of 3 segments: nodes at x = 0, 50, 100, 150.
    net = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (50, 0)]),
            LineString([(50, 0), (100, 0)]),
            LineString([(100, 0), (150, 0)]),
        ],
        crs="EPSG:3857",
    )
    # A→node0, B→node50 (50 m apart), C→node150 (100 m from B, 150 from A).
    pts = _points([(10, 0), (60, 0), (140, 0)])
    out = _run(pts, net, eps_m=60.0, min_samples=2)
    labels = out["cluster"].tolist()
    assert labels[0] == labels[1]   # A & B within 50 m network distance
    assert labels[0] != -1
    assert labels[2] == -1          # C is 100 m away → isolated → noise


def test_connected_network_merges_branches():
    # Same two branches, now joined by a short rung at x=0.
    net = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 10), (100, 10)]),
            LineString([(0, 0), (100, 0)]),
            LineString([(0, 0), (0, 10)]),
        ],
        crs="EPSG:3857",
    )
    pts = _points([(5, 10), (5, 0)])
    out = _run(pts, net, eps_m=50.0, min_samples=2)
    labels = out["cluster"].tolist()
    # both snap near (0,*); network distance across the rung is 10 m ≤ 50
    assert labels[0] == labels[1] != -1


def test_empty_gdf(two_branches):
    empty = gpd.GeoDataFrame({"pid": []}, geometry=[], crs="EPSG:3857")
    out = _run(empty, two_branches, eps_m=30.0)
    assert "cluster" in out.columns
    assert len(out) == 0


def test_missing_network_raises():
    pts = _points([(0, 0)])
    with pytest.raises(ValueError, match="line-network"):
        NetworkDBSCANClusterCapability().execute(pts, ref_gdf=None)


def test_invalid_eps_raises(two_branches):
    pts = _points([(0, 0)])
    with pytest.raises(ValueError, match="eps_m"):
        _run(pts, two_branches, eps_m=0.0)


def test_reprojection_from_4326():
    # Network + points in lat/lon; eps_m in meters via crs_meters reproject.
    net = gpd.GeoDataFrame(
        geometry=[LineString([(2.0, 48.0), (2.01, 48.0)])],
        crs="EPSG:4326",
    )
    pts = gpd.GeoDataFrame(
        geometry=[Point(2.0001, 48.0), Point(2.0002, 48.0)],
        crs="EPSG:4326",
    )
    out = NetworkDBSCANClusterCapability().execute(
        pts, ref_gdf=net, eps_m=2000.0, min_samples=2
    )
    assert out.crs == pts.crs                 # original CRS preserved
    assert out["cluster"].tolist() == [0, 0]  # both within 2 km on the line


def test_via_apply(two_branches):
    pts = _points([(10, 10), (20, 10)])
    out = gispulse.apply(
        "cluster_network_dbscan", pts, ref_gdf=two_branches, eps_m=30.0, min_samples=2
    )
    assert out["cluster"].tolist() == [0, 0]
