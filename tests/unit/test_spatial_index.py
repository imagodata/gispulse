"""Unit tests for the reusable SpatialIndex (capability K)."""

from __future__ import annotations

import pytest
from shapely.geometry import LineString, Point

from gispulse.core.spatial_index import SpatialIndex


@pytest.fixture
def grid_points() -> list[Point]:
    # A 3x3 grid at spacing 10.
    return [Point(x, y) for x in (0, 10, 20) for y in (0, 10, 20)]


def test_len_and_build(grid_points):
    idx = SpatialIndex.build(grid_points)
    assert len(idx) == 9
    assert idx.geometries[0] == grid_points[0]


def test_empty_index():
    idx = SpatialIndex([])
    assert len(idx) == 0
    assert idx.nearest(Point(0, 0)) == []
    assert idx.query_bbox((0, 0, 1, 1)) == []
    assert idx.query_radius(Point(0, 0), 5.0) == []


def test_query_bbox(grid_points):
    idx = SpatialIndex(grid_points)
    hits = set(idx.query_bbox((-1, -1, 11, 11)))
    # points (0,0),(0,10),(10,0),(10,10) → positions 0,1,3,4
    assert hits == {0, 1, 3, 4}


def test_query_radius_exact_not_bbox(grid_points):
    idx = SpatialIndex(grid_points)
    # radius 10 from origin matches (0,0),(10,0),(0,10) but NOT (10,10) at ~14.1
    hits = set(idx.query_radius(Point(0, 0), 10.0))
    assert hits == {0, 1, 3}


def test_query_radius_zero(grid_points):
    idx = SpatialIndex(grid_points)
    assert idx.query_radius(Point(10, 10), 0.0) == [4]


def test_query_radius_negative_raises(grid_points):
    idx = SpatialIndex(grid_points)
    with pytest.raises(ValueError):
        idx.query_radius(Point(0, 0), -1.0)


def test_nearest_single(grid_points):
    idx = SpatialIndex(grid_points)
    assert idx.nearest(Point(11, 9)) == [4]  # closest to (10,10)


def test_nearest_k(grid_points):
    idx = SpatialIndex(grid_points)
    near = idx.nearest(Point(0, 0), k=3)
    assert near[0] == 0  # (0,0) itself
    assert set(near) == {0, 1, 3}  # the three closest
    # ordered nearest-first: (0,0) then the two at distance 10
    assert near[0] == 0


def test_nearest_k_exceeds_size(grid_points):
    idx = SpatialIndex(grid_points)
    near = idx.nearest(Point(0, 0), k=100)
    assert len(near) == 9


def test_works_on_lines():
    lines = [LineString([(0, 0), (10, 0)]), LineString([(0, 100), (10, 100)])]
    idx = SpatialIndex(lines)
    assert idx.nearest(Point(5, 1)) == [0]
    assert set(idx.query_radius(Point(5, 1), 2.0)) == {0}
