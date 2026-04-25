"""P0 reproducer: SinglepartsToMultipartCapability with by=None + mixed geom types.

BETA-TEST finding 2026-04-24 v3 (CF3). The ``by=<col>`` path got a
pre-validation fix in 6ca381c (issue P0-4), but the ``by=None`` branch still
crashes with ``TypeError: 'LineString' object is not iterable`` or silently
drops features depending on the first geometry type.

Expected behaviour after fix: either (a) raise the same pre-validation
``ValueError`` the ``by=<col>`` path raises, or (b) partition per geom type
and return one Multi* per type. Current code does neither.

Fix location: ``capabilities/vector.py:3864-3898`` (by=None branch).
"""
from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from capabilities.registry import get as cap_get
import capabilities  # noqa: F401


@pytest.fixture
def mixed_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "geometry": [
                Point(0, 0),
                LineString([(0, 0), (1, 1)]),
                Polygon([(0, 0), (1, 0), (1, 1)]),
            ],
        },
        crs="EPSG:3857",
    )


@pytest.fixture
def homogeneous_points() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3], "geometry": [Point(i, 0) for i in range(3)]},
        crs="EPSG:3857",
    )


def test_singleparts_by_none_mixed_raises_clear_error(mixed_gdf):
    """Mixed-type input with by=None must raise a clear ValueError, not TypeError."""
    cap = cap_get("singleparts_to_multipart")
    with pytest.raises(ValueError, match="(?i)mixed geometry types|force_geometry_type"):
        cap.execute(mixed_gdf, by=None)


def test_singleparts_by_none_homogeneous_works(homogeneous_points):
    """Regression guard — homogeneous input must still collapse into one MultiPoint."""
    cap = cap_get("singleparts_to_multipart")
    out = cap.execute(homogeneous_points, by=None)
    assert len(out) == 1
    assert out.geometry.iloc[0].geom_type == "MultiPoint"
    assert len(out.geometry.iloc[0].geoms) == 3
