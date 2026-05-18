"""Unit tests for layer-shape transforms: multipart, boundary, assign_projection."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import (
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
)

from gispulse.capabilities.vector import (
    AssignProjectionCapability,
    BoundaryCapability,
    MultipartToSinglepartsCapability,
    SinglepartsToMultipartCapability,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def multipolys() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "name": ["a", "b"],
            "geometry": [
                MultiPolygon([
                    Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                    Polygon([(2, 2), (3, 2), (3, 3), (2, 3)]),
                ]),
                Polygon([(5, 5), (6, 5), (6, 6), (5, 6)]),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def singlepolys() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "commune": ["X", "X", "Y"],
            "pop": [10, 20, 30],
            "geometry": [
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
                Polygon([(5, 5), (6, 5), (6, 6), (5, 6)]),
            ],
        },
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# MultipartToSingleparts (explode)
# ---------------------------------------------------------------------------


class TestMultipartToSingleparts:
    def test_explode_count(self, multipolys):
        out = MultipartToSinglepartsCapability().execute(multipolys)
        # 2 polys from multi + 1 single = 3
        assert len(out) == 3
        assert all(g.geom_type == "Polygon" for g in out.geometry)

    def test_attributes_duplicated(self, multipolys):
        out = MultipartToSinglepartsCapability().execute(multipolys)
        assert (out["name"] == ["a", "a", "b"]).all()
        assert (out["id"] == [1, 1, 2]).all()

    def test_lines(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [
                MultiLineString([[(0, 0), (1, 1)], [(2, 2), (3, 3)]]),
            ]}, crs="EPSG:4326",
        )
        out = MultipartToSinglepartsCapability().execute(gdf)
        assert len(out) == 2
        assert all(g.geom_type == "LineString" for g in out.geometry)

    def test_empty_input(self):
        gdf = gpd.GeoDataFrame({"id": [], "geometry": []}, crs="EPSG:4326")
        out = MultipartToSinglepartsCapability().execute(gdf)
        assert len(out) == 0


# ---------------------------------------------------------------------------
# SinglepartsToMultipart (collect)
# ---------------------------------------------------------------------------


class TestSinglepartsToMultipart:
    def test_group_by_attribute(self, singlepolys):
        out = SinglepartsToMultipartCapability().execute(singlepolys, by=["commune"])
        # Two communes → two rows
        assert len(out) == 2
        x_row = out[out["commune"] == "X"].iloc[0]
        y_row = out[out["commune"] == "Y"].iloc[0]
        assert x_row.geometry.geom_type == "MultiPolygon"
        # X has 2 polys, Y has 1 (still becomes MultiPolygon via dissolve depending on version)
        assert len(list(x_row.geometry.geoms)) == 2
        # Y is a single polygon → dissolve may keep it as Polygon
        assert y_row.geometry.geom_type in {"Polygon", "MultiPolygon"}

    def test_no_groups_collapses_all(self, singlepolys):
        out = SinglepartsToMultipartCapability().execute(singlepolys, by=None)
        assert len(out) == 1
        assert out.geometry.iloc[0].geom_type == "MultiPolygon"

    def test_unknown_group_raises(self, singlepolys):
        with pytest.raises(KeyError):
            SinglepartsToMultipartCapability().execute(singlepolys, by=["ghost"])

    def test_collect_points(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2, 3], "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)]},
            crs="EPSG:4326",
        )
        out = SinglepartsToMultipartCapability().execute(gdf, by=None)
        assert len(out) == 1
        assert out.geometry.iloc[0].geom_type == "MultiPoint"


# ---------------------------------------------------------------------------
# Boundary
# ---------------------------------------------------------------------------


class TestBoundary:
    def test_polygon_to_line(self, singlepolys):
        out = BoundaryCapability().execute(singlepolys)
        assert len(out) == 3
        assert all(g.geom_type in {"LineString", "MultiLineString"} for g in out.geometry)

    def test_line_to_endpoints(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [LineString([(0, 0), (1, 1), (2, 2)])]},
            crs="EPSG:4326",
        )
        out = BoundaryCapability().execute(gdf)
        # Boundary of a line is its two endpoints (MultiPoint)
        assert out.geometry.iloc[0].geom_type == "MultiPoint"

    def test_drop_empty_for_points(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326",
        )
        out = BoundaryCapability().execute(gdf, drop_empty=True)
        # Boundary of a point is empty → dropped
        assert len(out) == 0


# ---------------------------------------------------------------------------
# AssignProjection
# ---------------------------------------------------------------------------


class TestAssignProjection:
    def test_set_when_missing(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(0, 0)]},
        )
        # No CRS initially
        assert gdf.crs is None
        out = AssignProjectionCapability().execute(gdf, crs="EPSG:2154")
        assert str(out.crs) == "EPSG:2154"
        # Coordinates unchanged
        assert out.geometry.iloc[0].x == 0
        assert out.geometry.iloc[0].y == 0

    def test_override_existing(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(700000, 6600000)]},
            crs="EPSG:4326",  # wrongly declared
        )
        out = AssignProjectionCapability().execute(
            gdf, crs="EPSG:2154", allow_override=True,
        )
        assert str(out.crs) == "EPSG:2154"
        # Coordinates unchanged (no reproject)
        assert out.geometry.iloc[0].x == 700000

    def test_missing_crs_raises(self):
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(0, 0)]})
        with pytest.raises(ValueError, match="requires 'crs'"):
            AssignProjectionCapability().execute(gdf)
