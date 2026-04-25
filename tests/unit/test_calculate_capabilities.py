"""Unit tests for CalculateCapability and SpatialAggregateCapability."""

from __future__ import annotations

import pytest
import geopandas as gpd
from shapely.geometry import Point, Polygon

from capabilities.vector import CalculateCapability, SpatialAggregateCapability


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def parcels_gdf() -> gpd.GeoDataFrame:
    """Parcels — large polygons that contain points."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "name": ["parcel_a", "parcel_b"],
            "population": [100, 200],
            "area_m2": [500.0, 1000.0],
            "geometry": [
                Polygon([(0, 0), (5, 0), (5, 5), (0, 5)]),
                Polygon([(5, 0), (10, 0), (10, 5), (5, 5)]),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def buildings_gdf() -> gpd.GeoDataFrame:
    """Buildings — small points inside the parcels."""
    return gpd.GeoDataFrame(
        {
            "id": [10, 11, 12, 13, 14],
            "type": ["house", "shop", "house", "office", "house"],
            "height": [5.0, 8.0, 6.0, 15.0, 4.0],
            "geometry": [
                Point(1, 1),   # inside parcel_a
                Point(2, 3),   # inside parcel_a
                Point(3, 2),   # inside parcel_a
                Point(7, 2),   # inside parcel_b
                Point(8, 4),   # inside parcel_b
            ],
        },
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# CalculateCapability
# ---------------------------------------------------------------------------


class TestCalculateCapability:
    def test_simple_arithmetic(self, parcels_gdf: gpd.GeoDataFrame):
        cap = CalculateCapability()
        result = cap.execute(
            parcels_gdf,
            expressions={"density": "population / area_m2"},
        )
        assert "density" in result.columns
        assert result["density"].iloc[0] == pytest.approx(100.0 / 500.0)
        assert result["density"].iloc[1] == pytest.approx(200.0 / 1000.0)

    def test_multiple_expressions(self, parcels_gdf: gpd.GeoDataFrame):
        cap = CalculateCapability()
        result = cap.execute(
            parcels_gdf,
            expressions={
                "density": "population / area_m2",
                "double_pop": "population * 2",
            },
        )
        assert "density" in result.columns
        assert "double_pop" in result.columns
        assert result["double_pop"].iloc[0] == 200
        assert result["double_pop"].iloc[1] == 400

    def test_no_expressions_passthrough(self, parcels_gdf: gpd.GeoDataFrame):
        cap = CalculateCapability()
        result = cap.execute(parcels_gdf, expressions=None)
        assert result.equals(parcels_gdf)

    def test_expression_with_numpy(self, parcels_gdf: gpd.GeoDataFrame):
        cap = CalculateCapability()
        result = cap.execute(
            parcels_gdf,
            expressions={"log_pop": "np.log(population)"},
        )
        assert "log_pop" in result.columns
        import numpy as np
        assert result["log_pop"].iloc[0] == pytest.approx(np.log(100))

    def test_does_not_mutate_input(self, parcels_gdf: gpd.GeoDataFrame):
        cap = CalculateCapability()
        original_cols = list(parcels_gdf.columns)
        cap.execute(parcels_gdf, expressions={"new_col": "population + 1"})
        assert list(parcels_gdf.columns) == original_cols

    def test_schema(self):
        cap = CalculateCapability()
        schema = cap.get_schema()
        assert "expressions" in schema["properties"]
        assert "expressions" in schema["required"]


# ---------------------------------------------------------------------------
# SpatialAggregateCapability
# ---------------------------------------------------------------------------


class TestSpatialAggregateCapability:
    def test_count_buildings_per_parcel(
        self,
        parcels_gdf: gpd.GeoDataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ):
        cap = SpatialAggregateCapability()
        result = cap.execute(
            parcels_gdf,
            ref_gdf=buildings_gdf,
            predicate="contains",
            agg={"building_count": ["id", "count"]},
        )
        assert "building_count" in result.columns
        assert result["building_count"].iloc[0] == 3  # parcel_a has 3 buildings
        assert result["building_count"].iloc[1] == 2  # parcel_b has 2 buildings

    def test_sum_and_mean(
        self,
        parcels_gdf: gpd.GeoDataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ):
        cap = SpatialAggregateCapability()
        result = cap.execute(
            parcels_gdf,
            ref_gdf=buildings_gdf,
            predicate="contains",
            agg={
                "total_height": ["height", "sum"],
                "avg_height": ["height", "mean"],
            },
        )
        # parcel_a buildings: 5.0, 8.0, 6.0
        assert result["total_height"].iloc[0] == pytest.approx(19.0)
        assert result["avg_height"].iloc[0] == pytest.approx(19.0 / 3)
        # parcel_b buildings: 15.0, 4.0
        assert result["total_height"].iloc[1] == pytest.approx(19.0)
        assert result["avg_height"].iloc[1] == pytest.approx(19.0 / 2)

    def test_min_max(
        self,
        parcels_gdf: gpd.GeoDataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ):
        cap = SpatialAggregateCapability()
        result = cap.execute(
            parcels_gdf,
            ref_gdf=buildings_gdf,
            predicate="contains",
            agg={
                "min_h": ["height", "min"],
                "max_h": ["height", "max"],
            },
        )
        # parcel_a: heights 5, 8, 6
        assert result["min_h"].iloc[0] == pytest.approx(5.0)
        assert result["max_h"].iloc[0] == pytest.approx(8.0)
        # parcel_b: heights 15, 4
        assert result["min_h"].iloc[1] == pytest.approx(4.0)
        assert result["max_h"].iloc[1] == pytest.approx(15.0)

    def test_intersects_predicate(
        self,
        parcels_gdf: gpd.GeoDataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ):
        cap = SpatialAggregateCapability()
        result = cap.execute(
            parcels_gdf,
            ref_gdf=buildings_gdf,
            predicate="intersects",
            agg={"n": ["id", "count"]},
        )
        assert result["n"].sum() == 5  # all 5 buildings accounted for

    def test_no_ref_layer_raises(self, parcels_gdf: gpd.GeoDataFrame):
        cap = SpatialAggregateCapability()
        with pytest.raises(ValueError, match="reference layer"):
            cap.execute(parcels_gdf, agg={"x": ["id", "count"]})

    def test_no_agg_raises(
        self,
        parcels_gdf: gpd.GeoDataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ):
        cap = SpatialAggregateCapability()
        with pytest.raises(ValueError, match="agg"):
            cap.execute(parcels_gdf, ref_gdf=buildings_gdf, predicate="intersects")

    def test_invalid_predicate_raises(
        self,
        parcels_gdf: gpd.GeoDataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ):
        cap = SpatialAggregateCapability()
        with pytest.raises(ValueError, match="Unknown spatial predicate"):
            cap.execute(
                parcels_gdf,
                ref_gdf=buildings_gdf,
                predicate="banana",
                agg={"x": ["id", "count"]},
            )

    def test_invalid_agg_func_raises(
        self,
        parcels_gdf: gpd.GeoDataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ):
        cap = SpatialAggregateCapability()
        with pytest.raises(ValueError, match="Unknown agg function"):
            cap.execute(
                parcels_gdf,
                ref_gdf=buildings_gdf,
                predicate="intersects",
                agg={"x": ["id", "variance"]},
            )

    def test_does_not_mutate_input(
        self,
        parcels_gdf: gpd.GeoDataFrame,
        buildings_gdf: gpd.GeoDataFrame,
    ):
        cap = SpatialAggregateCapability()
        original_cols = list(parcels_gdf.columns)
        cap.execute(
            parcels_gdf,
            ref_gdf=buildings_gdf,
            predicate="contains",
            agg={"n": ["id", "count"]},
        )
        assert list(parcels_gdf.columns) == original_cols

    def test_schema(self):
        cap = SpatialAggregateCapability()
        schema = cap.get_schema()
        assert "ref_layer" in schema["properties"]
        assert "predicate" in schema["properties"]
        assert "agg" in schema["properties"]
        assert "ref_layer" not in schema.get("required", [])
        assert "agg" in schema["required"]
