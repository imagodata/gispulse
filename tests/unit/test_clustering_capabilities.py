"""Unit tests for clustering capabilities (DBSCAN, KMeans, HDBSCAN)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from gispulse.capabilities.clustering import (
    DBSCANClusterCapability,
    HDBSCANClusterCapability,
    KMeansClusterCapability,
)


@pytest.fixture
def two_clusters() -> gpd.GeoDataFrame:
    """Two tight clusters of 5 points each, 1000 m apart."""
    coords = (
        [(x, 0) for x in range(0, 50, 10)]
        + [(x, 0) for x in range(1000, 1050, 10)]
    )
    return gpd.GeoDataFrame(
        {"id": list(range(len(coords))), "geometry": [Point(*c) for c in coords]},
        crs="EPSG:2154",
    )


@pytest.fixture
def cluster_with_noise() -> gpd.GeoDataFrame:
    """5 points clustered + 1 isolated noise point."""
    coords = [(0, 0), (10, 0), (0, 10), (5, 5), (10, 10), (5000, 5000)]
    return gpd.GeoDataFrame(
        {"id": list(range(len(coords))), "geometry": [Point(*c) for c in coords]},
        crs="EPSG:2154",
    )


class TestDBSCAN:

    def test_dbscan_two_clusters(self, two_clusters):
        result = DBSCANClusterCapability().execute(
            two_clusters, eps=50.0, min_samples=3, crs_meters="EPSG:2154"
        )
        labels = set(result["cluster"])
        # Two clusters, no noise since both have >= 3 points
        assert len(labels - {-1}) == 2

    def test_dbscan_noise_labeled_minus_one(self, cluster_with_noise):
        result = DBSCANClusterCapability().execute(
            cluster_with_noise, eps=50.0, min_samples=3, crs_meters="EPSG:2154"
        )
        # The isolated point at (5000, 5000) should be noise
        noise_point = result[result["id"] == 5].iloc[0]
        assert noise_point["cluster"] == -1

    def test_dbscan_invalid_eps(self, two_clusters):
        with pytest.raises(ValueError, match="eps"):
            DBSCANClusterCapability().execute(two_clusters, eps=0)

    def test_dbscan_empty_gdf(self):
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:2154")
        result = DBSCANClusterCapability().execute(empty, eps=10.0)
        assert len(result) == 0
        assert "cluster" in result.columns

    def test_dbscan_works_on_polygons(self):
        polys = [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(5, 5), (6, 5), (6, 6), (5, 6)]),
            Polygon([(1000, 1000), (1001, 1000), (1001, 1001), (1000, 1001)]),
        ]
        gdf = gpd.GeoDataFrame({"geometry": polys}, crs="EPSG:2154")
        result = DBSCANClusterCapability().execute(
            gdf, eps=10.0, min_samples=2, crs_meters="EPSG:2154"
        )
        # First two polygons (close) cluster together; third is noise
        assert len(result) == 3


class TestKMeans:

    def test_kmeans_two_clusters(self, two_clusters):
        result = KMeansClusterCapability().execute(
            two_clusters, k=2, crs_meters="EPSG:2154"
        )
        labels = set(result["cluster"])
        assert len(labels) == 2
        assert "cluster_dist" in result.columns

    def test_kmeans_k_capped_at_n(self):
        gdf = gpd.GeoDataFrame(
            {"geometry": [Point(0, 0), Point(1, 1)]}, crs="EPSG:2154"
        )
        result = KMeansClusterCapability().execute(gdf, k=10)
        assert len(result) == 2

    def test_kmeans_invalid_k(self, two_clusters):
        with pytest.raises(ValueError, match="k"):
            KMeansClusterCapability().execute(two_clusters, k=0)

    def test_kmeans_reproducible_with_seed(self, two_clusters):
        a = KMeansClusterCapability().execute(
            two_clusters, k=2, random_state=7, crs_meters="EPSG:2154"
        )
        b = KMeansClusterCapability().execute(
            two_clusters, k=2, random_state=7, crs_meters="EPSG:2154"
        )
        assert (a["cluster"].to_numpy() == b["cluster"].to_numpy()).all()

    def test_kmeans_distance_col_disabled(self, two_clusters):
        result = KMeansClusterCapability().execute(
            two_clusters, k=2, distance_col=None, crs_meters="EPSG:2154"
        )
        assert "cluster_dist" not in result.columns


class TestHDBSCAN:

    def test_hdbscan_finds_clusters_with_varying_density(self, two_clusters):
        result = HDBSCANClusterCapability().execute(
            two_clusters, min_cluster_size=3, crs_meters="EPSG:2154"
        )
        # At least one cluster detected
        labels = set(result["cluster"])
        assert len(labels - {-1}) >= 1
        assert "cluster_probability" in result.columns

    def test_hdbscan_invalid_min_cluster_size(self, two_clusters):
        with pytest.raises(ValueError, match="min_cluster_size"):
            HDBSCANClusterCapability().execute(two_clusters, min_cluster_size=1)

    def test_hdbscan_empty_gdf(self):
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:2154")
        result = HDBSCANClusterCapability().execute(empty)
        assert len(result) == 0
        assert "cluster" in result.columns
