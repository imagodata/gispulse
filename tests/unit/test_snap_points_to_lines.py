"""Unit tests for snap_points_to_lines (capability B).

Covers the published contract consumed by downstream callers (e.g. MILOU's
``build_site_network_candidates``): stable ``edge_id``, along-line ``measure``,
perpendicular ``offset_distance`` and a ``snapped`` flag, with the nearest line
always resolved and input columns preserved.
"""

from __future__ import annotations

import time

import geopandas as gpd
import numpy as np
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


def _run(pts, lines, ref_id_col="eid", **params):
    return SnapPointsToLinesCapability().execute(
        pts, ref_gdf=lines, ref_id_col=ref_id_col, **params
    )


def test_requires_reference():
    pts = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:3857")
    with pytest.raises(ValueError):
        SnapPointsToLinesCapability().execute(pts, ref_id_col="eid")


# --- DoD #1 : nearby point projects with the expected fields ---------------
def test_basic_snap_fields(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 5)], crs="EPSG:3857")
    out = _run(pts, lines)
    row = out.iloc[0]
    assert bool(row["snapped"]) is True
    assert row["edge_id"] == "E1"          # nearest line is y=0
    assert row["measure"] == pytest.approx(30.0)
    assert 0.0 <= row["measure"] <= 100.0   # measure within [0, line length]
    assert row["offset_distance"] == pytest.approx(5.0)
    # geometry is the projected point on the line
    assert row.geometry.distance(Point(30, 0)) == pytest.approx(0.0, abs=1e-6)


# --- DoD #4 : nearest of several wins; ties broken deterministically -------
def test_picks_nearest_of_several(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 45)], crs="EPSG:3857")
    out = _run(pts, lines)
    assert out.iloc[0]["edge_id"] == "E2"  # closer to y=50
    assert out.iloc[0]["offset_distance"] == pytest.approx(5.0)


def test_equidistant_tie_breaks_on_smallest_edge_id():
    # Point sits exactly between two parallel, equidistant lines.
    lines = gpd.GeoDataFrame(
        {"eid": ["B", "A"]},  # deliberately not pre-sorted
        geometry=[
            LineString([(0, 10), (100, 10)]),    # 10 m above
            LineString([(0, -10), (100, -10)]),  # 10 m below
        ],
        crs="EPSG:3857",
    )
    pts = gpd.GeoDataFrame(geometry=[Point(50, 0)], crs="EPSG:3857")
    out = _run(pts, lines)
    assert out.iloc[0]["offset_distance"] == pytest.approx(10.0)
    # ex aequo -> smallest edge_id ("A") wins, regardless of row order
    assert out.iloc[0]["edge_id"] == "A"
    # ... and it is stable across runs
    assert _run(pts, lines).iloc[0]["edge_id"] == "A"


# --- DoD #2 : beyond max_distance_m -> snapped=False but still resolved ----
def test_max_distance_leaves_unsnapped(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 20)], crs="EPSG:3857")
    out = _run(pts, lines, max_distance_m=10.0)
    row = out.iloc[0]
    assert not row["snapped"]
    # nearest edge + projected point are STILL reported (consumer filters)
    assert row["edge_id"] == "E1"
    assert row["offset_distance"] == pytest.approx(20.0)  # true nearest distance
    assert row["measure"] == pytest.approx(30.0)
    assert row.geometry.distance(Point(30, 0)) == pytest.approx(0.0, abs=1e-6)


def test_within_max_distance_snaps(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 8)], crs="EPSG:3857")
    out = _run(pts, lines, max_distance_m=10.0)
    assert out.iloc[0]["snapped"]
    assert out.iloc[0]["edge_id"] == "E1"


# --- DoD #3 : max_distance_m=None snaps everything ------------------------
def test_none_max_distance_snaps_all(lines):
    pts = gpd.GeoDataFrame(
        geometry=[Point(30, 5), Point(60, 200), Point(10, -300)],
        crs="EPSG:3857",
    )
    out = _run(pts, lines, max_distance_m=None)
    assert out["snapped"].all()
    assert list(out["edge_id"]) == ["E1", "E2", "E1"]


# --- DoD #6 : missing ref_id_col -> structured error, not a silent index ---
def test_missing_ref_id_col_raises(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 5)], crs="EPSG:3857")
    with pytest.raises(ValueError, match="ref_id_col"):
        SnapPointsToLinesCapability().execute(pts, ref_gdf=lines)
    with pytest.raises(ValueError, match="ref_id_col"):
        SnapPointsToLinesCapability().execute(
            pts, ref_gdf=lines, ref_id_col="does_not_exist"
        )


# --- DoD #5 : input columns of gdf survive intact -------------------------
def test_input_columns_preserved(lines):
    pts = gpd.GeoDataFrame(
        {"site_id": [42], "label": ["alpha"]},
        geometry=[Point(30, 5)],
        crs="EPSG:3857",
    )
    out = _run(pts, lines)
    assert out.iloc[0]["site_id"] == 42
    assert out.iloc[0]["label"] == "alpha"
    # and the new columns coexist
    assert {"edge_id", "measure", "offset_distance", "snapped"} <= set(out.columns)


def test_empty_point_geometry(lines):
    pts = gpd.GeoDataFrame(geometry=[None, Point(30, 5)], crs="EPSG:3857")
    out = _run(pts, lines)
    assert not out.iloc[0]["snapped"]
    assert out.iloc[0]["edge_id"] is None  # no nearest line for a null geometry
    assert out.iloc[1]["snapped"]


# --- DoD #7 : non-projected input CRS is reprojected; measures in meters ---
def test_reprojects_angular(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(0.0003, 0.00004)], crs="EPSG:4326")
    lines_ll = lines.to_crs("EPSG:4326")
    out = _run(pts, lines_ll, crs_meters="EPSG:3857")
    assert str(out.crs).endswith("4326")        # back to the input CRS
    assert out.iloc[0]["measure"] > 0           # measured in meters, not degrees
    assert out.iloc[0]["offset_distance"] > 0


def test_registered_via_apply(lines):
    pts = gpd.GeoDataFrame(geometry=[Point(30, 5)], crs="EPSG:3857")
    out = gispulse.apply(
        "snap_points_to_lines", pts, ref_gdf=lines, ref_id_col="eid"
    )
    assert "edge_id" in out.columns
    assert out.iloc[0]["edge_id"] == "E1"


# --- DoD #8 : large input does not explode quadratically ------------------
@pytest.mark.parametrize("scale", [10_000])
def test_large_input_subquadratic(scale):
    rng = np.random.default_rng(0)
    # `scale` parallel horizontal lines stacked 10 m apart, ids as ints.
    edges = gpd.GeoDataFrame(
        {"edge_id": list(range(scale))},
        geometry=[LineString([(0, y * 10), (1000, y * 10)]) for y in range(scale)],
        crs="EPSG:3857",
    )
    xs = rng.uniform(0, 1000, scale)
    ys = rng.uniform(0, scale * 10, scale)
    pts = gpd.GeoDataFrame(
        {"site_id": list(range(scale))},
        geometry=[Point(x, y) for x, y in zip(xs, ys)],
        crs="EPSG:3857",
    )
    start = time.perf_counter()
    out = SnapPointsToLinesCapability().execute(
        pts, ref_gdf=edges, ref_id_col="edge_id", max_distance_m=None
    )
    elapsed = time.perf_counter() - start
    assert len(out) == scale
    assert out["snapped"].all()
    assert out["site_id"].tolist() == list(range(scale))  # order + columns kept
    # A quadratic 10^4 x 10^4 scan is ~10^8 distance ops (minutes). The STRtree
    # path is ~O(n log m); a generous 60 s ceiling only trips on regression.
    assert elapsed < 60.0, f"snap of {scale}x{scale} took {elapsed:.1f}s"
