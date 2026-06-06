"""Unit tests for connected_components (capability E)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString

import gispulse
from gispulse.capabilities.network_components import ConnectedComponentsCapability


def _run(gdf, **params):
    return ConnectedComponentsCapability().execute(gdf, **params)


@pytest.fixture
def two_islands() -> gpd.GeoDataFrame:
    # Component A (3 connected edges) + component B (1 isolated edge).
    return gpd.GeoDataFrame(
        {"rid": ["a1", "a2", "a3", "b1"]},
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(10, 0), (10, 10)]),
            LineString([(10, 10), (0, 10)]),
            LineString([(100, 100), (110, 100)]),
        ],
        crs="EPSG:3857",
    )


def test_labels_two_components(two_islands):
    out = _run(two_islands)
    assert out[out["rid"].isin(["a1", "a2", "a3"])]["component_id"].nunique() == 1
    # the isolated edge is a different component
    a_id = out.loc[out["rid"] == "a1", "component_id"].iloc[0]
    b_id = out.loc[out["rid"] == "b1", "component_id"].iloc[0]
    assert a_id != b_id


def test_largest_component_is_zero(two_islands):
    out = _run(two_islands)
    # the 3-edge component must be id 0 (largest first)
    assert out.loc[out["rid"] == "a1", "component_id"].iloc[0] == 0
    assert out.loc[out["rid"] == "b1", "component_id"].iloc[0] == 1


def test_component_size(two_islands):
    out = _run(two_islands)
    assert out.loc[out["rid"] == "a1", "component_size"].iloc[0] == 3
    assert out.loc[out["rid"] == "b1", "component_size"].iloc[0] == 1


def test_fully_connected_single_component():
    gdf = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(10, 0), (20, 0)]),
        ],
        crs="EPSG:3857",
    )
    out = _run(gdf)
    assert set(out["component_id"]) == {0}
    assert set(out["component_size"]) == {2}


def test_snap_tolerance_joins_components():
    # Two edges with a 0.5 m gap → separate at tol=0, joined at tol=1.
    gdf = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(10.5, 0), (20, 0)]),
        ],
        crs="EPSG:3857",
    )
    assert _run(gdf)["component_id"].nunique() == 2
    joined = _run(gdf, snap_tolerance_m=1.0)
    assert joined["component_id"].nunique() == 1
    assert set(joined["component_size"]) == {2}


def test_empty_geometry_is_minus_one():
    gdf = gpd.GeoDataFrame(
        geometry=[None, LineString([(0, 0), (1, 0)])],
        crs="EPSG:3857",
    )
    out = _run(gdf)
    assert out["component_id"].iloc[0] == -1
    assert out["component_size"].iloc[0] == 0
    assert out["component_id"].iloc[1] == 0


def test_multilinestring_row_is_one_feature():
    gdf = gpd.GeoDataFrame(
        {"rid": ["m", "x"]},
        geometry=[
            MultiLineString([[(0, 0), (10, 0)], [(10, 0), (10, 10)]]),
            LineString([(50, 50), (60, 50)]),
        ],
        crs="EPSG:3857",
    )
    out = _run(gdf)
    # the multipart connected feature is its own component, the isolated one another
    assert out["component_id"].nunique() == 2


def test_empty_frame():
    gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:3857")
    out = _run(gdf)
    assert "component_id" in out.columns
    assert len(out) == 0


def test_registered_via_apply(two_islands):
    out = gispulse.apply("connected_components", two_islands)
    assert "component_id" in out.columns
    assert "component_size" in out.columns
