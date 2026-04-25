"""Geospatial edge case tests — GeoDataFrame-based, no backend required.

Covers:
- NULL geometries: rows with None geometry in a GeoDataFrame
- Empty GeoDataFrame (0 features)
- Malformed WKT parsing (must raise, not silently return None)
- CRS-less GeoDataFrame handling
- Column names with spaces, accents and special characters in pandas.query()
- Mixed geometry types (Point, LineString, Polygon, MultiPolygon) in one layer
- Empty geometry collections
- Very large coordinate values (no overflow)
- CalculateCapability: arithmetic, division-by-zero, inject-unsafe expressions
- FilterCapability: dangerous expression rejection, spatial predicate with ref_wkt
"""

from __future__ import annotations

import pytest
import geopandas as gpd
import pandas as pd
from shapely import wkt as shapely_wkt
from shapely.errors import ShapelyError
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
)

from capabilities.vector import CalculateCapability, FilterCapability


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gdf_with_null_geoms() -> gpd.GeoDataFrame:
    """GeoDataFrame where some rows have None geometry."""
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4], "val": [10, 20, 30, 40]},
        geometry=[Point(1, 1), None, Point(3, 3), None],
        crs="EPSG:4326",
    )


@pytest.fixture
def empty_gdf() -> gpd.GeoDataFrame:
    """GeoDataFrame with schema but zero rows."""
    return gpd.GeoDataFrame(
        {"id": pd.Series([], dtype=int), "val": pd.Series([], dtype=float)},
        geometry=gpd.GeoSeries([], dtype="geometry"),
        crs="EPSG:4326",
    )


@pytest.fixture
def no_crs_gdf() -> gpd.GeoDataFrame:
    """GeoDataFrame with no CRS assigned."""
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3]},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
    )


@pytest.fixture
def mixed_geom_gdf() -> gpd.GeoDataFrame:
    """GeoDataFrame with Point, LineString, Polygon, MultiPolygon in the same layer."""
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4], "type": ["point", "line", "poly", "multipoly"]},
        geometry=[
            Point(0, 0),
            LineString([(0, 0), (1, 1)]),
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            MultiPolygon([
                Polygon([(2, 2), (3, 2), (3, 3), (2, 3)]),
                Polygon([(4, 4), (5, 4), (5, 5), (4, 5)]),
            ]),
        ],
        crs="EPSG:4326",
    )


@pytest.fixture
def numeric_gdf() -> gpd.GeoDataFrame:
    """GeoDataFrame with numeric columns suitable for CalculateCapability."""
    return gpd.GeoDataFrame(
        {"population": [1000, 2000, 500], "area_m2": [200.0, 400.0, 100.0]},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# NULL geometry rows
# ---------------------------------------------------------------------------


class TestNullGeometries:
    def test_filter_preserves_row_count_with_nulls(self, gdf_with_null_geoms):
        """Attribute filter on a non-geometry column must not lose rows due to null geoms."""
        result = FilterCapability().execute(gdf_with_null_geoms, expression="val > 15")
        assert len(result) == 3  # ids 2, 3, 4 (val 20, 30, 40)

    def test_filter_returns_correct_values_with_nulls(self, gdf_with_null_geoms):
        result = FilterCapability().execute(gdf_with_null_geoms, expression="val == 10")
        assert len(result) == 1
        assert result["val"].iloc[0] == 10

    def test_null_geom_rows_are_in_gdf(self, gdf_with_null_geoms):
        null_mask = gdf_with_null_geoms.geometry.isna()
        assert null_mask.sum() == 2

    def test_calculate_on_gdf_with_nulls(self, gdf_with_null_geoms):
        """CalculateCapability must compute numeric columns even when geometry is null."""
        result = CalculateCapability().execute(
            gdf_with_null_geoms,
            expressions={"val_doubled": "val * 2"},
        )
        assert "val_doubled" in result.columns
        assert list(result["val_doubled"]) == [20, 40, 60, 80]

    def test_null_geom_preserved_after_calculate(self, gdf_with_null_geoms):
        result = CalculateCapability().execute(
            gdf_with_null_geoms,
            expressions={"val_plus_one": "val + 1"},
        )
        null_mask = result.geometry.isna()
        assert null_mask.sum() == 2

    def test_all_null_geometries_gdf(self):
        """GeoDataFrame where every row has a null geometry is still a valid GDF."""
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2]},
            geometry=[None, None],
            crs="EPSG:4326",
        )
        result = FilterCapability().execute(gdf, expression="id > 0")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Empty GeoDataFrame
# ---------------------------------------------------------------------------


class TestEmptyGeoDataFrame:
    def test_filter_empty_returns_empty(self, empty_gdf):
        result = FilterCapability().execute(empty_gdf, expression="val > 0")
        assert len(result) == 0

    def test_calculate_empty_returns_empty(self, empty_gdf):
        result = CalculateCapability().execute(
            empty_gdf, expressions={"doubled": "val * 2"}
        )
        assert len(result) == 0

    def test_calculate_empty_adds_column(self, empty_gdf):
        result = CalculateCapability().execute(
            empty_gdf, expressions={"doubled": "val * 2"}
        )
        assert "doubled" in result.columns

    def test_filter_empty_no_expression_returns_empty(self, empty_gdf):
        result = FilterCapability().execute(empty_gdf, expression="")
        assert len(result) == 0

    def test_calculate_no_expressions_returns_unchanged(self, empty_gdf):
        result = CalculateCapability().execute(empty_gdf, expressions={})
        assert len(result) == 0

    def test_empty_gdf_crs_preserved_after_filter(self, empty_gdf):
        result = FilterCapability().execute(empty_gdf, expression="id > 0")
        assert result.crs is not None
        assert result.crs.to_epsg() == 4326


# ---------------------------------------------------------------------------
# WKT parsing — malformed inputs must raise, not return None silently
# ---------------------------------------------------------------------------


class TestMalformedWKT:
    def test_invalid_wkt_raises(self):
        with pytest.raises((ShapelyError, Exception)):
            shapely_wkt.loads("NOT A VALID WKT STRING")

    def test_truncated_point_wkt_raises(self):
        with pytest.raises((ShapelyError, Exception)):
            shapely_wkt.loads("POINT(")

    def test_wkt_with_letters_in_coords_raises(self):
        with pytest.raises((ShapelyError, Exception)):
            shapely_wkt.loads("POINT(abc def)")

    def test_empty_string_wkt_raises(self):
        with pytest.raises((ShapelyError, Exception)):
            shapely_wkt.loads("")

    def test_valid_wkt_does_not_raise(self):
        geom = shapely_wkt.loads("POINT(2.3 48.9)")
        assert geom is not None
        assert geom.geom_type == "Point"

    def test_valid_polygon_wkt_does_not_raise(self):
        geom = shapely_wkt.loads("POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        assert geom.geom_type == "Polygon"

    def test_wkt_missing_closing_paren(self):
        with pytest.raises((ShapelyError, Exception)):
            shapely_wkt.loads("POLYGON((0 0, 1 0, 1 1, 0 1, 0 0)")

    def test_wkt_wrong_geometry_type_keyword(self):
        with pytest.raises((ShapelyError, Exception)):
            shapely_wkt.loads("POIT(0 0)")

    def test_filter_capability_rejects_malformed_ref_wkt(self):
        """FilterCapability with a malformed ref_wkt must not crash silently."""
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        )
        with pytest.raises(Exception):
            FilterCapability().execute(
                gdf,
                spatial_predicate="intersects",
                ref_wkt="NOT A WKT",
            )


# ---------------------------------------------------------------------------
# CRS handling
# ---------------------------------------------------------------------------


class TestCrsHandling:
    def test_no_crs_gdf_has_none_crs(self, no_crs_gdf):
        assert no_crs_gdf.crs is None

    def test_filter_works_without_crs(self, no_crs_gdf):
        result = FilterCapability().execute(no_crs_gdf, expression="id > 1")
        assert len(result) == 2

    def test_calculate_works_without_crs(self, no_crs_gdf):
        result = CalculateCapability().execute(
            no_crs_gdf, expressions={"id_doubled": "id * 2"}
        )
        assert "id_doubled" in result.columns
        assert result["id_doubled"].tolist() == [2, 4, 6]

    def test_crs_is_none_after_filter_on_no_crs_gdf(self, no_crs_gdf):
        result = FilterCapability().execute(no_crs_gdf, expression="id > 0")
        assert result.crs is None

    def test_crs_reprojection_epsg4326_to_3857(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1]},
            geometry=[Point(2.3, 48.9)],
            crs="EPSG:4326",
        )
        reprojected = gdf.to_crs("EPSG:3857")
        assert reprojected.crs.to_epsg() == 3857
        # Coordinates should be in meters, not degrees
        x, y = reprojected.geometry.iloc[0].x, reprojected.geometry.iloc[0].y
        assert abs(x) > 1000  # meters, not degrees
        assert abs(y) > 1000

    def test_gdf_with_crs_crs_preserved_after_filter(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2, 3]},
            geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
            crs="EPSG:4326",
        )
        result = FilterCapability().execute(gdf, expression="id > 1")
        assert result.crs is not None
        assert result.crs.to_epsg() == 4326


# ---------------------------------------------------------------------------
# Column names with spaces, accents, special characters
# ---------------------------------------------------------------------------


class TestSpecialColumnNames:
    def test_column_with_space_using_backtick_in_query(self):
        """pandas.query() uses backtick escaping for column names with spaces."""
        gdf = gpd.GeoDataFrame(
            {"my col": [1, 2, 3]},
            geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
            crs="EPSG:4326",
        )
        # pandas.query supports backtick escaping for column names with spaces
        result = gdf.query("`my col` > 1")
        assert len(result) == 2

    def test_column_with_accent_accessible_via_bracket(self):
        """Columns with accented names are accessible via bracket notation."""
        gdf = gpd.GeoDataFrame(
            {"superficie": [100, 200], "département": ["75", "69"]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        )
        vals = gdf["département"].tolist()
        assert vals == ["75", "69"]

    def test_calculate_with_normal_column_name(self, numeric_gdf):
        """Columns with normal names work fine in CalculateCapability."""
        result = CalculateCapability().execute(
            numeric_gdf,
            expressions={"density": "population / area_m2"},
        )
        assert "density" in result.columns
        assert result["density"].iloc[0] == pytest.approx(5.0)

    def test_filter_with_quoted_string_value(self, mixed_geom_gdf):
        """String values in filter expressions with quotes work correctly."""
        result = FilterCapability().execute(mixed_geom_gdf, expression="type == 'point'")
        assert len(result) == 1

    def test_column_name_with_underscore_in_calculate(self, numeric_gdf):
        result = CalculateCapability().execute(
            numeric_gdf,
            expressions={"pop_per_ha": "population / area_m2 * 10000"},
        )
        assert "pop_per_ha" in result.columns

    def test_filter_capability_rejects_backtick_injection(self):
        """FilterCapability must block backtick character used for code injection."""
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        )
        with pytest.raises(ValueError, match="backtick"):
            FilterCapability().execute(gdf, expression="`id` > 0")


# ---------------------------------------------------------------------------
# Mixed geometry types
# ---------------------------------------------------------------------------


class TestMixedGeometryTypes:
    def test_mixed_gdf_has_four_rows(self, mixed_geom_gdf):
        assert len(mixed_geom_gdf) == 4

    def test_mixed_gdf_geometry_types(self, mixed_geom_gdf):
        types = set(mixed_geom_gdf.geometry.geom_type)
        assert "Point" in types
        assert "LineString" in types
        assert "Polygon" in types
        assert "MultiPolygon" in types

    def test_filter_on_mixed_gdf_by_attribute(self, mixed_geom_gdf):
        result = FilterCapability().execute(mixed_geom_gdf, expression="id > 2")
        assert len(result) == 2

    def test_calculate_on_mixed_gdf(self, mixed_geom_gdf):
        result = CalculateCapability().execute(
            mixed_geom_gdf, expressions={"id_squared": "id * id"}
        )
        assert "id_squared" in result.columns
        assert len(result) == 4

    def test_mixed_gdf_geom_type_column_accessible(self, mixed_geom_gdf):
        types = mixed_geom_gdf.geometry.geom_type.tolist()
        assert len(types) == 4

    def test_filter_by_string_type_on_mixed(self, mixed_geom_gdf):
        result = FilterCapability().execute(mixed_geom_gdf, expression="type == 'poly'")
        assert len(result) == 1
        assert result["type"].iloc[0] == "poly"

    def test_mixed_gdf_bounding_box_valid(self, mixed_geom_gdf):
        valid_geoms = mixed_geom_gdf.geometry.dropna()
        bbox = valid_geoms.total_bounds
        assert len(bbox) == 4
        assert bbox[0] <= bbox[2]  # minx <= maxx
        assert bbox[1] <= bbox[3]  # miny <= maxy


# ---------------------------------------------------------------------------
# Empty geometry collections
# ---------------------------------------------------------------------------


class TestEmptyGeometryCollections:
    def test_empty_geometry_collection_is_valid_shapely(self):
        geom = GeometryCollection()
        assert geom.is_empty
        assert geom.geom_type == "GeometryCollection"

    def test_gdf_with_empty_geometry_collection(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2]},
            geometry=[GeometryCollection(), Point(1, 1)],
            crs="EPSG:4326",
        )
        assert len(gdf) == 2

    def test_empty_geom_collection_area_is_zero(self):
        geom = GeometryCollection()
        assert geom.area == 0.0

    def test_empty_polygon_is_empty(self):
        geom = Polygon()
        assert geom.is_empty

    def test_filter_on_gdf_with_empty_geom_collection(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "val": [10, 20]},
            geometry=[GeometryCollection(), Point(1, 1)],
            crs="EPSG:4326",
        )
        result = FilterCapability().execute(gdf, expression="val > 15")
        assert len(result) == 1

    def test_empty_linestring_is_degenerate(self):
        geom = LineString()
        assert geom.is_empty

    def test_gdf_mix_of_valid_and_empty_geoms(self):
        """A GDF can hold both valid and empty geometries simultaneously."""
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2, 3]},
            geometry=[
                Point(0, 0),
                GeometryCollection(),
                Polygon([(0, 0), (1, 0), (1, 1)]),
            ],
            crs="EPSG:4326",
        )
        assert len(gdf) == 3
        empty_mask = gdf.geometry.is_empty
        assert empty_mask.sum() == 1


# ---------------------------------------------------------------------------
# Very large coordinate values
# ---------------------------------------------------------------------------


class TestLargeCoordinates:
    def test_very_large_coordinates_point(self):
        """Shapely can handle very large coordinate values without overflow."""
        geom = Point(1e15, 1e15)
        assert geom.x == 1e15
        assert geom.y == 1e15

    def test_very_large_coordinates_polygon(self):
        poly = Polygon(
            [(0, 0), (1e14, 0), (1e14, 1e14), (0, 1e14)]
        )
        assert not poly.is_empty
        assert poly.area == pytest.approx(1e28)

    def test_gdf_with_large_coords_filter(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2], "val": [100, 200]},
            geometry=[Point(1e12, 1e12), Point(2e12, 2e12)],
        )
        result = FilterCapability().execute(gdf, expression="val > 150")
        assert len(result) == 1

    def test_very_small_coordinates_do_not_underflow(self):
        geom = Point(1e-15, 1e-15)
        assert geom.x == pytest.approx(1e-15)

    def test_negative_large_coords(self):
        geom = Point(-1e15, -1e15)
        assert geom.x == -1e15

    def test_distance_between_large_coord_points(self):
        p1 = Point(0, 0)
        p2 = Point(1e10, 0)
        dist = p1.distance(p2)
        assert dist == pytest.approx(1e10)

    def test_gdf_calculate_with_large_val(self):
        gdf = gpd.GeoDataFrame(
            {"population": [1_000_000_000], "area_m2": [500_000_000.0]},
            geometry=[Point(0, 0)],
        )
        result = CalculateCapability().execute(
            gdf, expressions={"density": "population / area_m2"}
        )
        assert result["density"].iloc[0] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# CalculateCapability edge cases
# ---------------------------------------------------------------------------


class TestCalculateCapabilityEdgeCases:
    def test_no_expressions_returns_unchanged(self, numeric_gdf):
        result = CalculateCapability().execute(numeric_gdf, expressions=None)
        assert list(result.columns) == list(numeric_gdf.columns)

    def test_empty_expressions_dict_returns_unchanged(self, numeric_gdf):
        result = CalculateCapability().execute(numeric_gdf, expressions={})
        assert len(result) == len(numeric_gdf)

    def test_multiple_expressions_at_once(self, numeric_gdf):
        result = CalculateCapability().execute(
            numeric_gdf,
            expressions={
                "density": "population / area_m2",
                "area_ha": "area_m2 / 10000",
            },
        )
        assert "density" in result.columns
        assert "area_ha" in result.columns

    def test_overwrite_existing_column(self, numeric_gdf):
        result = CalculateCapability().execute(
            numeric_gdf,
            expressions={"population": "population * 2"},
        )
        assert result["population"].iloc[0] == 2000

    def test_chained_expressions_use_original_columns(self, numeric_gdf):
        """Expressions are evaluated sequentially — later ones can use prior results."""
        result = CalculateCapability().execute(
            numeric_gdf,
            expressions={"doubled": "population * 2"},
        )
        assert result["doubled"].tolist() == [2000, 4000, 1000]

    def test_reject_import_expression(self, numeric_gdf):
        with pytest.raises((ValueError, SyntaxError)):
            CalculateCapability().execute(
                numeric_gdf,
                expressions={"hack": "__import__('os').system('id')"},
            )

    def test_reject_dunder_access(self, numeric_gdf):
        with pytest.raises(ValueError):
            CalculateCapability().execute(
                numeric_gdf,
                expressions={"hack": "population.__class__"},
            )

    def test_reject_semicolon_in_expression(self, numeric_gdf):
        with pytest.raises(ValueError):
            CalculateCapability().execute(
                numeric_gdf,
                expressions={"hack": "population; import os"},
            )

    def test_numpy_function_in_expression(self, numeric_gdf):
        result = CalculateCapability().execute(
            numeric_gdf,
            expressions={"log_area": "np.log(area_m2)"},
        )
        import numpy as np
        assert "log_area" in result.columns
        assert result["log_area"].iloc[0] == pytest.approx(np.log(200.0))

    def test_abs_function_in_expression(self):
        gdf = gpd.GeoDataFrame(
            {"delta": [-10, 5, -3]},
            geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
        )
        result = CalculateCapability().execute(
            gdf,
            expressions={"abs_delta": "abs(delta)"},
        )
        assert result["abs_delta"].tolist() == [10, 5, 3]

    def test_result_is_geodataframe(self, numeric_gdf):
        result = CalculateCapability().execute(
            numeric_gdf, expressions={"x": "population + 1"}
        )
        assert isinstance(result, gpd.GeoDataFrame)
