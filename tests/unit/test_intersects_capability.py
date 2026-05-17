"""Tests for the IntersectsCapability."""

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from gispulse.capabilities.vector import IntersectsCapability


@pytest.fixture
def cap():
    return IntersectsCapability()


@pytest.fixture
def sample_gdf():
    return gpd.GeoDataFrame(
        {"name": ["inside", "outside", "edge"]},
        geometry=[Point(0.5, 0.5), Point(10, 10), Point(1, 1)],
        crs="EPSG:4326",
    )


@pytest.fixture
def ref_polygon():
    return Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])


class TestIntersectsCapability:
    def test_intersects_with_wkt(self, cap, sample_gdf, ref_polygon):
        result = cap.execute(sample_gdf, wkt=ref_polygon.wkt)
        assert len(result) == 2  # inside + edge
        assert "outside" not in result["name"].values

    def test_intersects_with_mask_gdf(self, cap, sample_gdf, ref_polygon):
        mask = gpd.GeoDataFrame(geometry=[ref_polygon], crs="EPSG:4326")
        result = cap.execute(sample_gdf, mask_gdf=mask)
        assert len(result) == 2

    def test_intersects_no_params_raises(self, cap, sample_gdf):
        with pytest.raises(ValueError, match="requires"):
            cap.execute(sample_gdf)

    def test_intersects_empty_result(self, cap):
        gdf = gpd.GeoDataFrame(
            {"name": ["far"]},
            geometry=[Point(100, 100)],
            crs="EPSG:4326",
        )
        result = cap.execute(gdf, wkt="POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))")
        assert len(result) == 0

    def test_schema(self, cap):
        schema = cap.get_schema()
        assert "wkt" in schema["properties"]
