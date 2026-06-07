"""Unit tests for split_lines_at_points (capability C)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString, Point

import gispulse
from gispulse.capabilities.vector.split_lines import SplitLinesAtPointsCapability


@pytest.fixture
def lines() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"eid": ["E1", "E2"], "kind": ["road", "rail"]},
        geometry=[
            LineString([(0, 0), (100, 0)]),
            LineString([(0, 50), (100, 50)]),
        ],
        crs="EPSG:3857",
    )


def _run(lines, points=None, **params):
    return SplitLinesAtPointsCapability().execute(lines, ref_gdf=points, **params)


def test_missing_reference_is_passthrough(lines):
    out = _run(lines, None)
    # Lines unchanged but tagged with a stable parent id.
    assert len(out) == 2
    assert "parent_edge_id" in out.columns
    assert list(out.geometry) == list(lines.geometry)


def test_empty_reference_is_passthrough(lines):
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:3857")
    out = _run(lines, empty)
    assert len(out) == 2
    assert "parent_edge_id" in out.columns


def test_splits_line_at_point(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(40, 0)], crs="EPSG:3857")
    out = _run(lines, pts)
    # E1 cut into two; E2 untouched -> 3 segments total.
    assert len(out) == 3
    e1 = out[out["eid"] == "E1"]
    assert len(e1) == 2
    lengths = sorted(g.length for g in e1.geometry)
    assert lengths == pytest.approx([40.0, 60.0])
    # Attributes copied onto every segment.
    assert set(e1["kind"]) == {"road"}


def test_multiple_points_multiple_cuts(lines):
    pts = gpd.GeoDataFrame(
        geometry=[Point(25, 0), Point(75, 0)], crs="EPSG:3857"
    )
    out = _run(lines, pts)
    e1 = out[out["eid"] == "E1"]
    assert len(e1) == 3
    assert sorted(g.length for g in e1.geometry) == pytest.approx([25.0, 25.0, 50.0])


def test_tolerance_filters_far_points(lines):
    # Point 8 m off the line: ignored at tol=0, splits at tol=10.
    pts = gpd.GeoDataFrame(geometry=[Point(40, 8)], crs="EPSG:3857")
    assert len(_run(lines, pts, tolerance_m=0.0)) == 2  # no cut
    out = _run(lines, pts, tolerance_m=10.0)
    e1 = out[out["eid"] == "E1"]
    assert len(e1) == 2  # cut at the projected location


def test_parent_edge_id_from_id_col(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(40, 0)], crs="EPSG:3857")
    out = _run(lines, pts, id_col="eid")
    e1 = out[out["eid"] == "E1"]
    assert set(e1["parent_edge_id"]) == {"E1"}


def test_parent_edge_id_defaults_to_position(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(40, 0)], crs="EPSG:3857")
    out = _run(lines, pts)
    # First line is row 0 -> parent_edge_id 0 on both its segments.
    seg = out[out["eid"] == "E1"]
    assert set(seg["parent_edge_id"]) == {0}


def test_custom_parent_id_col(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(40, 0)], crs="EPSG:3857")
    out = _run(lines, pts, id_col="eid", parent_id_col="src_edge")
    assert "src_edge" in out.columns
    assert "parent_edge_id" not in out.columns


def test_multipart_line_suffix():
    lines = gpd.GeoDataFrame(
        {"eid": ["M1"]},
        geometry=[
            MultiLineString(
                [[(0, 0), (100, 0)], [(0, 50), (100, 50)]]
            )
        ],
        crs="EPSG:3857",
    )
    pts = gpd.GeoDataFrame(geometry=[Point(40, 0), Point(60, 50)], crs="EPSG:3857")
    out = _run(lines, pts, id_col="eid")
    parents = set(out["parent_edge_id"])
    assert parents == {"M1#0", "M1#1"}


def test_reprojects_angular(lines):
    lines_ll = lines.to_crs("EPSG:4326")
    pts = gpd.GeoDataFrame(geometry=[Point(40, 0)], crs="EPSG:3857").to_crs(
        "EPSG:4326"
    )
    out = _run(lines_ll, pts, crs_meters="EPSG:3857")
    assert str(out.crs).endswith("4326")
    assert len(out[out["eid"] == "E1"]) == 2


def test_negative_tolerance_raises(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(40, 0)], crs="EPSG:3857")
    with pytest.raises(ValueError):
        _run(lines, pts, tolerance_m=-1.0)


def test_registered_via_apply(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(40, 0)], crs="EPSG:3857")
    out = gispulse.apply("split_lines_at_points", lines, ref_gdf=pts, id_col="eid")
    assert "parent_edge_id" in out.columns
    assert len(out[out["eid"] == "E1"]) == 2
