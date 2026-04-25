"""P0 reproducer: PointcloudGridSummaryCapability silently produces NaN on 2D input.

BETA-TEST finding 2026-04-24 v3 (CF2). The capability reads ``gdf.geometry.z``
without first checking ``has_z``; shapely returns ``NaN`` for 2D points. The
output grid cells carry ``z_mean=NaN`` and ``z_count=0`` with no warning.

Expected behaviour after fix: raise ``ValueError("requires 3D points")`` (or
similar) up front. Fix location: ``capabilities/pointcloud.py:420``.
"""
from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from capabilities.registry import get as cap_get
import capabilities  # noqa: F401


@pytest.fixture
def points_2d() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"geometry": [Point(0, 0), Point(1, 1), Point(2, 0)]},
        crs="EPSG:3857",
    )


@pytest.fixture
def points_3d() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"geometry": [Point(0, 0, 10), Point(1, 1, 20), Point(2, 0, 15)]},
        crs="EPSG:3857",
    )


def test_grid_summary_refuses_2d_points(points_2d):
    cap = cap_get("pointcloud_grid_summary")
    with pytest.raises(ValueError, match="(?i)3D|has_z|z coord"):
        cap.execute(points_2d, cell_size=1.0)


def test_grid_summary_accepts_3d_points(points_3d):
    """Regression guard — 3D path must keep working after the guard is added."""
    cap = cap_get("pointcloud_grid_summary")
    out = cap.execute(points_3d, cell_size=1.0)
    assert len(out) == 3
    # z_count must be 1 per cell (one input point per cell) — never 0.
    assert all(c == 1 for c in out["z_count"])
    # z_mean must be finite.
    assert out["z_mean"].notna().all()
