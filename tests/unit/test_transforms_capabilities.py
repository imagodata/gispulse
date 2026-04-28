"""Unit tests for geometry transform capabilities (affine, swap_xy, reverse, Z/M)."""

from __future__ import annotations


import geopandas as gpd
import pytest
import shapely
from shapely.geometry import (
    LineString,
    MultiLineString,
    Point,
    Polygon,
)

from capabilities.transforms import (
    AddMCapability,
    AddZCapability,
    AffineTransformCapability,
    DropMCapability,
    DropZCapability,
    ReverseLinesCapability,
    SwapXYCapability,
)


# ---------------------------------------------------------------------------
# AffineTransform
# ---------------------------------------------------------------------------


class TestAffineTransform:
    def test_translate(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:2154",
        )
        out = AffineTransformCapability().execute(gdf, translate=[10, 20])
        assert out.geometry.iloc[0].x == 10
        assert out.geometry.iloc[0].y == 20

    def test_scale_around_centroid(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1],
             "geometry": [Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])]},
            crs="EPSG:2154",
        )
        out = AffineTransformCapability().execute(
            gdf, scale=[2.0, 2.0], origin="centroid",
        )
        # Original area = 4; scaled by 2x2 = 16
        assert out.geometry.iloc[0].area == pytest.approx(16.0)

    def test_rotate_90(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(1, 0)]}, crs="EPSG:2154",
        )
        out = AffineTransformCapability().execute(
            gdf, rotate=90, origin=[0, 0],
        )
        # Rotating (1,0) by 90° around origin → (0,1)
        assert out.geometry.iloc[0].x == pytest.approx(0, abs=1e-9)
        assert out.geometry.iloc[0].y == pytest.approx(1, abs=1e-9)

    def test_no_op_raises(self):
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:2154")
        with pytest.raises(ValueError, match="at least one"):
            AffineTransformCapability().execute(gdf)


# ---------------------------------------------------------------------------
# SwapXY
# ---------------------------------------------------------------------------


class TestSwapXY:
    def test_point(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(2.35, 48.85)]}, crs="EPSG:4326",
        )
        out = SwapXYCapability().execute(gdf)
        assert out.geometry.iloc[0].x == 48.85
        assert out.geometry.iloc[0].y == 2.35

    def test_polygon(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1],
             "geometry": [Polygon([(0, 0), (2, 0), (2, 1), (0, 1)])]},
            crs="EPSG:2154",
        )
        out = SwapXYCapability().execute(gdf)
        coords = list(out.geometry.iloc[0].exterior.coords)
        assert (0, 0) in coords
        assert (0, 2) in coords  # was (2, 0)
        assert (1, 2) in coords  # was (2, 1)


# ---------------------------------------------------------------------------
# ReverseLines
# ---------------------------------------------------------------------------


class TestReverseLines:
    def test_linestring(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1],
             "geometry": [LineString([(0, 0), (1, 0), (2, 0)])]},
            crs="EPSG:2154",
        )
        out = ReverseLinesCapability().execute(gdf)
        coords = list(out.geometry.iloc[0].coords)
        assert coords == [(2, 0), (1, 0), (0, 0)]

    def test_multilinestring(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1],
             "geometry": [MultiLineString([[(0, 0), (1, 1)], [(5, 5), (6, 6)]])]},
            crs="EPSG:2154",
        )
        out = ReverseLinesCapability().execute(gdf)
        first = list(out.geometry.iloc[0].geoms)[0]
        assert list(first.coords) == [(1, 1), (0, 0)]

    def test_passes_through_points_silently(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2],
             "geometry": [Point(0, 0), LineString([(0, 0), (1, 1)])]},
            crs="EPSG:2154",
        )
        out = ReverseLinesCapability().execute(gdf)
        assert out.geometry.iloc[0].geom_type == "Point"
        assert list(out.geometry.iloc[1].coords) == [(1, 1), (0, 0)]

    def test_strict_mode_raises_on_point(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:2154",
        )
        with pytest.raises(TypeError, match="does not support"):
            ReverseLinesCapability().execute(gdf, ignore_non_lines=False)


# ---------------------------------------------------------------------------
# Z dimension ops
# ---------------------------------------------------------------------------


class TestAddZ:
    def test_constant(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [Point(0, 0), Point(1, 1)]},
            crs="EPSG:2154",
        )
        out = AddZCapability().execute(gdf, z=42.0)
        assert all(shapely.has_z(g) for g in out.geometry)
        coords = shapely.get_coordinates(out.geometry.to_numpy(), include_z=True)
        assert (coords[:, 2] == 42.0).all()

    def test_from_column(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "altitude": [100.0, 200.0],
             "geometry": [Point(0, 0), Point(1, 1)]},
            crs="EPSG:2154",
        )
        out = AddZCapability().execute(gdf, from_column="altitude")
        coords = shapely.get_coordinates(out.geometry.to_numpy(), include_z=True)
        assert coords[0, 2] == 100.0
        assert coords[1, 2] == 200.0

    def test_neither_raises(self):
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:2154")
        with pytest.raises(ValueError, match="requires 'z' or 'from_column'"):
            AddZCapability().execute(gdf)

    def test_both_raises(self):
        gdf = gpd.GeoDataFrame({"id": [1], "altitude": [10.0],
                                "geometry": [Point(0, 0)]}, crs="EPSG:2154")
        with pytest.raises(ValueError, match="not both"):
            AddZCapability().execute(gdf, z=5, from_column="altitude")


class TestDropZ:
    def test_strips_z(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:2154",
        )
        with_z = AddZCapability().execute(gdf, z=42.0)
        out = DropZCapability().execute(with_z)
        assert not any(shapely.has_z(g) for g in out.geometry)


# ---------------------------------------------------------------------------
# M dimension ops (basic — shapely M support is limited)
# ---------------------------------------------------------------------------


class TestAddDropM:
    def test_add_m_constant(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "geometry": [Point(0, 0), Point(1, 1)]},
            crs="EPSG:2154",
        )
        out = AddMCapability().execute(gdf, m=5.0)
        # Geometries should now report has_m
        assert all(shapely.has_m(g) for g in out.geometry)

    def test_drop_m(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:2154",
        )
        with_m = AddMCapability().execute(gdf, m=5.0)
        out = DropMCapability().execute(with_m)
        assert not any(shapely.has_m(g) for g in out.geometry)

    def test_neither_raises(self):
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:2154")
        with pytest.raises(ValueError, match="requires 'm' or 'from_column'"):
            AddMCapability().execute(gdf)
