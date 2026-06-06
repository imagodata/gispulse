"""Unit tests for snap_points_to_lines (capability B)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

import gispulse
from gispulse.capabilities.vector.snap_points import SnapPointsToLinesCapability


@pytest.fixture
def lines() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"eid": ["E1", "E2"]},
        geometry=[
            LineString([(0, 0), (100, 0)]),
            LineString([(0, 50), (100, 50)]),
        ],
        crs="EPSG:3857",
    )


def _run(pts, lines, **params):
    return SnapPointsToLinesCapability().execute(pts, ref_gdf=lines, **params)


def test_requires_reference():
    pts = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:3857")
    with pytest.raises(ValueError):
        SnapPointsToLinesCapability().execute(pts)


def test_basic_snap_fields(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 5)], crs="EPSG:3857")
    out = _run(pts, lines, ref_id_col="eid")
    row = out.iloc[0]
    assert row["snapped"] is True or row["snapped"] == True  # noqa: E712
    assert row["edge_id"] == "E1"          # nearest line is y=0
    assert row["measure"] == pytest.approx(30.0)
    assert row["offset_distance"] == pytest.approx(5.0)
    # geometry is the projected point on the line
    assert row.geometry.distance(Point(30, 0)) == pytest.approx(0.0, abs=1e-6)


def test_picks_nearest_of_several(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 45)], crs="EPSG:3857")
    out = _run(pts, lines, ref_id_col="eid")
    assert out.iloc[0]["edge_id"] == "E2"  # closer to y=50
    assert out.iloc[0]["offset_distance"] == pytest.approx(5.0)


def test_max_distance_leaves_unsnapped(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 20)], crs="EPSG:3857")
    out = _run(pts, lines, ref_id_col="eid", max_distance_m=10.0)
    row = out.iloc[0]
    assert not row["snapped"]
    assert row["edge_id"] is None
    # offset still reports the true nearest distance (20 m to y=0)
    assert row["offset_distance"] == pytest.approx(20.0)
    # original geometry preserved when unsnapped
    assert row.geometry.equals(Point(30, 20))


def test_within_max_distance_snaps(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 8)], crs="EPSG:3857")
    out = _run(pts, lines, ref_id_col="eid", max_distance_m=10.0)
    assert out.iloc[0]["snapped"]
    assert out.iloc[0]["edge_id"] == "E1"


def test_edge_id_defaults_to_position(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 5)], crs="EPSG:3857")
    out = _run(pts, lines)  # no ref_id_col
    assert out.iloc[0]["edge_id"] == 0


def test_empty_point_geometry(lines):
    pts = gpd.GeoDataFrame(geometry=[None, Point(30, 5)], crs="EPSG:3857")
    out = _run(pts, lines, ref_id_col="eid")
    assert not out.iloc[0]["snapped"]
    assert out.iloc[1]["snapped"]


def test_reprojects_angular(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(0.0003, 0.00004)], crs="EPSG:4326")
    lines_ll = lines.to_crs("EPSG:4326")
    out = _run(pts, lines_ll, ref_id_col="eid", crs_meters="EPSG:3857")
    assert str(out.crs).endswith("4326")
    assert out.iloc[0]["measure"] > 0  # measured in meters


def test_registered_via_apply(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 5)], crs="EPSG:3857")
    out = gispulse.apply(
        "snap_points_to_lines", pts, ref_gdf=lines, ref_id_col="eid"
    )
    assert "edge_id" in out.columns
    assert out.iloc[0]["edge_id"] == "E1"
