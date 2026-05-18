"""Unit tests for spatial statistics capabilities (weights, Moran, Getis-Ord)."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point, Polygon

from gispulse.capabilities.spatial_stats import (
    GetisOrdGStarCapability,
    MoransICapability,
    SpatialWeightsCapability,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def grid_polygons() -> gpd.GeoDataFrame:
    """3x3 coverage of unit squares (9 polygons)."""
    polys = []
    for i in range(3):
        for j in range(3):
            polys.append(Polygon([(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]))
    return gpd.GeoDataFrame(
        {"id": list(range(9)), "value": [1, 1, 1, 1, 9, 1, 1, 1, 1], "geometry": polys},
        crs="EPSG:2154",
    )


@pytest.fixture
def grid_hotspot() -> gpd.GeoDataFrame:
    """3x3 coverage with one clustered hot zone at (0,0)-(1,1)-(2,2)."""
    polys = []
    values = []
    for i in range(3):
        for j in range(3):
            polys.append(Polygon([(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]))
            # Diagonal hot cluster (top-left to bottom-right)
            values.append(10.0 if i == j else 1.0)
    return gpd.GeoDataFrame(
        {"id": list(range(9)), "value": values, "geometry": polys},
        crs="EPSG:2154",
    )


@pytest.fixture
def scatter_points() -> gpd.GeoDataFrame:
    coords = [(0, 0), (1, 0), (0, 1), (5, 5), (6, 5), (5, 6)]
    return gpd.GeoDataFrame(
        {"id": list(range(len(coords))), "geometry": [Point(*c) for c in coords]},
        crs="EPSG:2154",
    )


# ---------------------------------------------------------------------------
# SpatialWeightsCapability
# ---------------------------------------------------------------------------


class TestSpatialWeights:

    def test_queen_weights_on_grid(self, grid_polygons):
        result = SpatialWeightsCapability().execute(grid_polygons, method="queen")
        assert "w_n_neighbours" in result.columns
        # Center cell (id=4) has 8 queen neighbours
        center = result[result["id"] == 4].iloc[0]
        assert center["w_n_neighbours"] == 8
        # Corner cells have 3 queen neighbours
        corner = result[result["id"] == 0].iloc[0]
        assert corner["w_n_neighbours"] == 3

    def test_rook_weights_on_grid(self, grid_polygons):
        result = SpatialWeightsCapability().execute(grid_polygons, method="rook")
        # Center has 4 rook neighbours (no diagonals)
        center = result[result["id"] == 4].iloc[0]
        assert center["w_n_neighbours"] == 4

    def test_knn_weights(self, scatter_points):
        result = SpatialWeightsCapability().execute(
            scatter_points, method="knn", k=2, crs_meters="EPSG:2154"
        )
        assert all(result["w_n_neighbours"] == 2)

    def test_distance_band_weights(self, scatter_points):
        result = SpatialWeightsCapability().execute(
            scatter_points,
            method="distance_band",
            threshold=2.0,
            crs_meters="EPSG:2154",
        )
        # Each cluster has 3 points; each point has 2 neighbours within 2m
        assert all(result["w_n_neighbours"] == 2)

    def test_invalid_method(self, grid_polygons):
        with pytest.raises(ValueError, match="method"):
            SpatialWeightsCapability().execute(grid_polygons, method="triangular")

    def test_distance_band_missing_threshold(self, scatter_points):
        with pytest.raises(ValueError, match="threshold"):
            SpatialWeightsCapability().execute(
                scatter_points, method="distance_band", crs_meters="EPSG:2154"
            )


# ---------------------------------------------------------------------------
# MoransICapability
# ---------------------------------------------------------------------------


class TestMoransI:

    def test_morans_i_positive_clustering(self, grid_hotspot):
        """Diagonal cluster → positive Moran's I (spatial autocorrelation)."""
        result = MoransICapability().execute(
            grid_hotspot, field="value", method="queen", permutations=99
        )
        assert len(result) == 1
        i_stat = result["morans_i"].iloc[0]
        # Diagonal cluster → positive, above expected (~-0.125)
        assert i_stat > -0.12

    def test_morans_i_random_no_pattern(self):
        """Shuffled values on grid should give Moran's I near expectation."""
        np.random.seed(42)
        polys = [
            Polygon([(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)])
            for i in range(4)
            for j in range(4)
        ]
        values = np.random.normal(size=len(polys))
        gdf = gpd.GeoDataFrame(
            {"id": list(range(len(polys))), "value": values, "geometry": polys},
            crs="EPSG:2154",
        )
        result = MoransICapability().execute(
            gdf, field="value", method="queen", permutations=99
        )
        # Near the expected -1/(n-1) = -0.067 for n=16
        i_stat = result["morans_i"].iloc[0]
        assert abs(i_stat) < 0.5

    def test_morans_i_requires_field(self, grid_polygons):
        with pytest.raises(ValueError, match="'field' is required"):
            MoransICapability().execute(grid_polygons)

    def test_morans_i_missing_field(self, grid_polygons):
        with pytest.raises(ValueError, match="not in GeoDataFrame"):
            MoransICapability().execute(grid_polygons, field="inexistent")

    def test_morans_i_nan_in_field(self, grid_polygons):
        grid_polygons.loc[0, "value"] = None
        with pytest.raises(ValueError, match="non-numeric or NaN"):
            MoransICapability().execute(grid_polygons, field="value")

    def test_morans_i_constant_field_returns_nan_pvalue(self):
        """Beta P1 (2026-04-24): a constant field used to produce a false
        ``p_value`` near 0.01 (looks significant) because the NaN-vs-NaN
        comparison inside the permutation loop evaluated to False for every
        simulation — so ``more_extreme = 0`` and ``p ≈ 1/permutations``.

        Constant fields have zero variance, Moran's I is mathematically
        undefined, and the p_value must be NaN — not a tiny number masking
        the absence of signal.
        """
        polys = [
            Polygon([(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)])
            for i in range(4)
            for j in range(4)
        ]
        gdf = gpd.GeoDataFrame(
            {"value": [5.0] * len(polys), "geometry": polys},
            crs="EPSG:2154",
        )
        result = MoransICapability().execute(
            gdf, field="value", method="queen", permutations=99
        )
        assert len(result) == 1
        assert np.isnan(result["morans_i"].iloc[0])
        assert np.isnan(result["z_score"].iloc[0])
        assert np.isnan(result["p_value"].iloc[0]), (
            "constant field must yield p_value=NaN, not a false-significant value"
        )
        assert int(result["n"].iloc[0]) == len(polys)


# ---------------------------------------------------------------------------
# GetisOrdGStarCapability
# ---------------------------------------------------------------------------


class TestGetisOrd:

    def test_hotspot_detected(self):
        """Cluster of high values → positive z-scores at the cluster edges."""
        # 4x4 grid with a 2x2 hotspot at the bottom-left
        polys = []
        values = []
        for i in range(4):
            for j in range(4):
                polys.append(Polygon([(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]))
                values.append(10.0 if (i < 2 and j < 2) else 1.0)
        gdf = gpd.GeoDataFrame(
            {"id": list(range(16)), "value": values, "geometry": polys},
            crs="EPSG:2154",
        )
        result = GetisOrdGStarCapability().execute(
            gdf, field="value", method="queen"
        )
        assert "gi_star" in result.columns
        assert "z_score" in result.columns
        assert "p_value" in result.columns
        assert "hotspot_label" in result.columns
        # z-scores inside the 2x2 hotspot should be positive (hot)
        # id 0, 1, 4, 5 = the 2x2 hotspot
        hot_ids = {0, 1, 4, 5}
        hot_z = result[result["id"].isin(hot_ids)]["z_score"].to_numpy()
        # At least one of the four hot cells should have positive z-score
        assert (hot_z > 0).any()

    def test_no_autocorrelation_field(self):
        polys = [
            Polygon([(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)])
            for i in range(4)
            for j in range(4)
        ]
        # constant values — no variation → all cells same z-score
        gdf = gpd.GeoDataFrame(
            {"id": list(range(len(polys))), "value": [5.0] * len(polys), "geometry": polys},
            crs="EPSG:2154",
        )
        result = GetisOrdGStarCapability().execute(
            gdf, field="value", method="queen"
        )
        # Standard deviation is 0 → z-scores should be NaN or 0
        # hotspot_label should say "not_significant" for all
        assert all(result["hotspot_label"] == "not_significant")

    def test_getis_requires_field(self, grid_polygons):
        with pytest.raises(ValueError, match="'field' is required"):
            GetisOrdGStarCapability().execute(grid_polygons)

    def test_getis_empty_gdf(self):
        empty = gpd.GeoDataFrame({"value": [], "geometry": []}, crs="EPSG:2154")
        result = GetisOrdGStarCapability().execute(empty, field="value")
        assert len(result) == 0
        assert "gi_star" in result.columns
