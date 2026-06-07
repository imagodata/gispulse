"""Unit tests for community_detection (capability I)."""

from __future__ import annotations

import geopandas as gpd
import pytest

pytest.importorskip("networkx", reason="networkx not installed")

from shapely.geometry import LineString

import gispulse
from gispulse.capabilities.network import CommunityDetectionCapability


@pytest.fixture(autouse=True)
def pro_tier(monkeypatch):
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv(
        "GISPULSE_LICENSE_KEY",
        "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ."
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )


def _run(gdf, **params):
    return CommunityDetectionCapability().execute(gdf, **params)


@pytest.fixture
def two_triangles_bridge() -> gpd.GeoDataFrame:
    # Two dense triangles joined by a single bridge edge (index 6).
    # Uniform weights so community structure is driven by connectivity, not
    # by the (long) bridge's geometric length.
    return gpd.GeoDataFrame(
        {"w": [1.0] * 7},
        geometry=[
            LineString([(0, 0), (1, 0)]),       # 0 tri1 A-B
            LineString([(1, 0), (0.5, 1)]),     # 1 tri1 B-C
            LineString([(0.5, 1), (0, 0)]),     # 2 tri1 C-A
            LineString([(10, 0), (11, 0)]),     # 3 tri2 D-E
            LineString([(11, 0), (10.5, 1)]),   # 4 tri2 E-F
            LineString([(10.5, 1), (10, 0)]),   # 5 tri2 F-D
            LineString([(1, 0), (10, 0)]),      # 6 bridge B-D
        ],
        crs="EPSG:3857",
    )


def test_louvain_finds_two_communities_and_bridge_is_boundary(two_triangles_bridge):
    out = _run(two_triangles_bridge, method="louvain", weight_col="w")
    labels = out["community_id"].tolist()
    assert labels[6] == -1                       # bridge spans two communities
    tri1, tri2 = set(labels[0:3]), set(labels[3:6])
    assert len(tri1) == 1 and len(tri2) == 1     # each triangle is coherent
    assert tri1 != tri2                          # and distinct
    assert -1 not in tri1 and -1 not in tri2
    assert len({lbl for lbl in labels if lbl != -1}) == 2


def test_single_component_one_community():
    triangle = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (1, 0)]),
            LineString([(1, 0), (0.5, 1)]),
            LineString([(0.5, 1), (0, 0)]),
        ],
        crs="EPSG:3857",
    )
    out = _run(triangle, method="louvain")
    labels = out["community_id"].tolist()
    assert set(labels) == {0}                    # one community, no boundary


@pytest.mark.parametrize("method", ["greedy_modularity", "label_propagation"])
def test_other_methods_run(two_triangles_bridge, method):
    out = _run(two_triangles_bridge, method=method)
    assert "community_id" in out.columns
    assert len(out) == len(two_triangles_bridge)
    assert all(isinstance(v, int) for v in out["community_id"])


def test_invalid_method_raises(two_triangles_bridge):
    with pytest.raises(ValueError, match="unknown method"):
        _run(two_triangles_bridge, method="spectral")


def test_empty_gdf():
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:3857")
    out = _run(empty)
    assert "community_id" in out.columns
    assert len(out) == 0


def test_reprojection_from_4326():
    net = gpd.GeoDataFrame(
        geometry=[
            LineString([(2.0, 48.0), (2.001, 48.0)]),
            LineString([(2.001, 48.0), (2.002, 48.0)]),
        ],
        crs="EPSG:4326",
    )
    out = _run(net, method="louvain")
    assert out.crs == net.crs
    assert "community_id" in out.columns


def test_via_apply(two_triangles_bridge):
    out = gispulse.apply(
        "community_detection", two_triangles_bridge, method="louvain", weight_col="w"
    )
    assert out["community_id"].tolist()[6] == -1
