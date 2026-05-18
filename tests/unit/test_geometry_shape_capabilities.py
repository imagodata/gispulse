"""Unit tests for extract_holes and force_geometry_type."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import (
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

from gispulse.capabilities.vector import (
    ExtractHolesCapability,
    ForceGeometryTypeCapability,
)


# ---------------------------------------------------------------------------
# ExtractHoles
# ---------------------------------------------------------------------------


@pytest.fixture
def donut() -> gpd.GeoDataFrame:
    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
    hole1 = [(2, 2), (4, 2), (4, 4), (2, 4)]
    hole2 = [(6, 6), (8, 6), (8, 8), (6, 8)]
    return gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "name": ["donut", "solid"],
            "geometry": [
                Polygon(outer, [hole1, hole2]),
                Polygon([(20, 0), (24, 0), (24, 4), (20, 4)]),
            ],
        },
        crs="EPSG:4326",
    )


class TestExtractHoles:
    def test_extracts_two_holes(self, donut):
        out = ExtractHolesCapability().execute(donut)
        # 2 holes from donut, 0 from solid
        assert len(out) == 2
        assert all(g.geom_type == "Polygon" for g in out.geometry)

    def test_attributes_inherited(self, donut):
        out = ExtractHolesCapability().execute(donut)
        # Both hole rows inherit donut's attributes
        assert (out["name"] == "donut").all()
        assert (out["id"] == 1).all()

    def test_hole_index_column(self, donut):
        out = ExtractHolesCapability().execute(donut)
        assert "hole_index" in out.columns
        assert sorted(out["hole_index"]) == [0, 1]

    def test_parent_id_col(self, donut):
        out = ExtractHolesCapability().execute(donut, parent_id_col="parent_idx")
        assert "parent_idx" in out.columns
        assert (out["parent_idx"] == 0).all()  # row index of donut

    def test_no_holes_yields_empty(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]},
            crs="EPSG:4326",
        )
        out = ExtractHolesCapability().execute(gdf)
        assert len(out) == 0


# ---------------------------------------------------------------------------
# ForceGeometryType
# ---------------------------------------------------------------------------


class TestForceGeometryTypeMultiPromotion:
    def test_point_to_multipoint(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [Point(0, 0), Point(1, 1)]},
            crs="EPSG:4326",
        )
        out = ForceGeometryTypeCapability().execute(gdf, target="MultiPoint")
        assert all(g.geom_type == "MultiPoint" for g in out.geometry)

    def test_polygon_to_multipolygon(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]},
            crs="EPSG:4326",
        )
        out = ForceGeometryTypeCapability().execute(gdf, target="MultiPolygon")
        assert out.geometry.iloc[0].geom_type == "MultiPolygon"


class TestForceGeometryTypeDemote:
    def test_multipolygon_explode(self):
        mp = MultiPolygon([
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(2, 2), (3, 2), (3, 3), (2, 3)]),
        ])
        gdf = gpd.GeoDataFrame({"id": [99], "geometry": [mp]}, crs="EPSG:4326")
        out = ForceGeometryTypeCapability().execute(
            gdf, target="Polygon", on_multi="explode",
        )
        assert len(out) == 2
        assert all(g.geom_type == "Polygon" for g in out.geometry)
        assert (out["id"] == 99).all()

    def test_multipoint_first_only(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [MultiPoint([Point(0, 0), Point(1, 1)])]},
            crs="EPSG:4326",
        )
        out = ForceGeometryTypeCapability().execute(
            gdf, target="Point", on_multi="first",
        )
        assert len(out) == 1
        assert out.geometry.iloc[0].geom_type == "Point"
        assert out.geometry.iloc[0].x == 0


class TestForceGeometryTypeInvalid:
    def test_invalid_raises_by_default(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326",
        )
        with pytest.raises(ValueError, match="Cannot coerce"):
            ForceGeometryTypeCapability().execute(gdf, target="Polygon")

    def test_invalid_drop(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2],
             "geometry": [Point(0, 0), Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]},
            crs="EPSG:4326",
        )
        out = ForceGeometryTypeCapability().execute(
            gdf, target="Polygon", on_invalid="drop",
        )
        assert len(out) == 1
        assert out.geometry.iloc[0].geom_type == "Polygon"

    def test_unknown_target_raises(self):
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326")
        with pytest.raises(ValueError, match="Unsupported target"):
            ForceGeometryTypeCapability().execute(gdf, target="Bogus")
