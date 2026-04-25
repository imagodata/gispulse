"""Edge case tests for GISPulse capabilities and I/O.

Tests critical spatial edge cases:
- NULL geometries in GeoDataFrame
- Empty GeoDataFrame (0 features)
- Missing CRS
- Single-feature GeoDataFrame
"""

from __future__ import annotations

import pytest
import geopandas as gpd
from shapely.geometry import Point, Polygon

from capabilities.vector import (
    AreaLengthCapability,
    BufferCapability,
    CentroidCapability,
    ClipCapability,
    DissolveCapability,
    FilterCapability,
    IntersectsCapability,
    SpatialJoinCapability,
    UnionCapability,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gdf_with_nulls() -> gpd.GeoDataFrame:
    """GeoDataFrame with one NULL geometry."""
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3], "val": [10, 20, 30]},
        geometry=[Point(2, 48), None, Point(3, 49)],
        crs="EPSG:4326",
    )


@pytest.fixture
def empty_gdf() -> gpd.GeoDataFrame:
    """Empty GeoDataFrame with schema but no rows."""
    return gpd.GeoDataFrame(
        {"id": []},
        geometry=[],
        crs="EPSG:4326",
    )


@pytest.fixture
def no_crs_gdf() -> gpd.GeoDataFrame:
    """GeoDataFrame with no CRS defined."""
    return gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(2, 48), Point(3, 49)],
    )


@pytest.fixture
def single_polygon() -> gpd.GeoDataFrame:
    """Single polygon GeoDataFrame."""
    return gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
        crs="EPSG:4326",
    )


@pytest.fixture
def ref_polygon() -> gpd.GeoDataFrame:
    """Reference polygon for cross-layer tests."""
    return gpd.GeoDataFrame(
        {"zone": ["A"]},
        geometry=[Polygon([(0.5, -0.5), (1.5, -0.5), (1.5, 1.5), (0.5, 1.5)])],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# NULL geometries
# ---------------------------------------------------------------------------


class TestNullGeometries:
    def test_buffer_with_nulls(self, gdf_with_nulls):
        result = BufferCapability().execute(gdf_with_nulls, distance=100)
        assert len(result) == 3

    def test_centroid_with_nulls(self, gdf_with_nulls):
        result = CentroidCapability().execute(gdf_with_nulls)
        assert len(result) == 3

    def test_filter_with_nulls(self, gdf_with_nulls):
        result = FilterCapability().execute(gdf_with_nulls, expression="val > 15")
        assert len(result) == 2

    def test_area_length_with_nulls(self, gdf_with_nulls):
        result = AreaLengthCapability().execute(gdf_with_nulls)
        assert "area_m2" in result.columns
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Empty GeoDataFrame
# ---------------------------------------------------------------------------


class TestEmptyGeoDataFrame:
    def test_buffer_empty(self, empty_gdf):
        result = BufferCapability().execute(empty_gdf, distance=100)
        assert len(result) == 0

    def test_filter_empty(self, empty_gdf):
        result = FilterCapability().execute(empty_gdf, expression="id > 0")
        assert len(result) == 0

    def test_dissolve_empty(self, empty_gdf):
        result = DissolveCapability().execute(empty_gdf)
        assert len(result) == 0

    def test_union_empty(self, empty_gdf):
        result = UnionCapability().execute(empty_gdf)
        assert len(result) == 1  # union_all returns a single empty geom

    def test_centroid_empty(self, empty_gdf):
        result = CentroidCapability().execute(empty_gdf)
        assert len(result) == 0

    def test_area_length_empty(self, empty_gdf):
        result = AreaLengthCapability().execute(empty_gdf)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# No CRS
# ---------------------------------------------------------------------------


class TestNoCRS:
    def test_buffer_no_crs(self, no_crs_gdf):
        """Buffer should work in native units when no CRS."""
        result = BufferCapability().execute(no_crs_gdf, distance=1)
        assert len(result) == 2
        assert result.crs is None
        # Geometries should be polygons after buffer
        for geom in result.geometry:
            assert geom.geom_type in ("Polygon", "MultiPolygon")

    def test_filter_no_crs(self, no_crs_gdf):
        result = FilterCapability().execute(no_crs_gdf, expression="id == 1")
        assert len(result) == 1

    def test_centroid_no_crs(self, no_crs_gdf):
        result = CentroidCapability().execute(no_crs_gdf)
        assert len(result) == 2

    def test_area_length_no_crs(self, no_crs_gdf):
        """Area/length should compute in native units when no CRS."""
        poly_gdf = gpd.GeoDataFrame(
            {"id": [1]},
            geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
        )
        result = AreaLengthCapability().execute(poly_gdf)
        assert result["area_m2"].iloc[0] == pytest.approx(1.0)
        assert result["length_m"].iloc[0] == pytest.approx(4.0)

    def test_dissolve_no_crs(self, no_crs_gdf):
        result = DissolveCapability().execute(no_crs_gdf)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Cross-layer with edge cases
# ---------------------------------------------------------------------------


class TestCrossLayerEdgeCases:
    def test_intersects_empty_input(self, empty_gdf, ref_polygon):
        result = IntersectsCapability().execute(empty_gdf, ref_gdf=ref_polygon)
        assert len(result) == 0

    def test_clip_single_feature(self, single_polygon, ref_polygon):
        result = ClipCapability().execute(single_polygon, ref_gdf=ref_polygon)
        assert len(result) == 1

    def test_spatial_join_empty_ref(self, single_polygon):
        empty_ref = gpd.GeoDataFrame(
            {"zone": []}, geometry=[], crs="EPSG:4326"
        )
        result = SpatialJoinCapability().execute(
            single_polygon, ref_gdf=empty_ref, how="left"
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# JobStatus str enum
# ---------------------------------------------------------------------------


class TestJobStatusEnum:
    def test_jobstatus_is_str(self):
        from core.models import JobStatus
        assert isinstance(JobStatus.PENDING, str)
        assert JobStatus.PENDING == "pending"
        assert JobStatus.COMPLETED.value == "completed"
        # str(Enum) changed in Python 3.11+ — use .value for serialization
        assert JobStatus.FAILED.value == "failed"
