"""Unit tests for pointcloud capabilities (LAS load, classification filter, zonal, grid)."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from capabilities.pointcloud import (
    PointcloudFilterClassificationCapability,
    PointcloudGridSummaryCapability,
    PointcloudLoadLasCapability,
    PointcloudZonalHeightCapability,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lidar_points() -> gpd.GeoDataFrame:
    """Synthetic 3D point cloud — 30 points in a 10×10 area."""
    rng = np.random.default_rng(42)
    n = 30
    x = rng.uniform(0, 10, n)
    y = rng.uniform(0, 10, n)
    z = rng.uniform(0, 30, n)
    classes = rng.choice([2, 6, 7], n, p=[0.6, 0.3, 0.1])  # ground/building/noise
    geoms = [Point(xi, yi, zi) for xi, yi, zi in zip(x, y, z)]
    return gpd.GeoDataFrame(
        {"classification": classes, "intensity": rng.integers(0, 65535, n)},
        geometry=geoms,
        crs="EPSG:2154",
    )


@pytest.fixture
def building_footprints() -> gpd.GeoDataFrame:
    """Two square footprints at (0..3, 0..3) and (5..8, 5..8)."""
    return gpd.GeoDataFrame(
        {
            "fid": ["A", "B"],
            "ground_z": [0.0, 5.0],
            "geometry": [
                Polygon([(0, 0), (3, 0), (3, 3), (0, 3)]),
                Polygon([(5, 5), (8, 5), (8, 8), (5, 8)]),
            ],
        },
        crs="EPSG:2154",
    )


@pytest.fixture
def known_height_pointcloud() -> gpd.GeoDataFrame:
    """Three points inside the first footprint with known Z values for assertion."""
    return gpd.GeoDataFrame(
        {"classification": [6, 6, 6]},
        geometry=[
            Point(1.0, 1.0, 5.0),
            Point(1.5, 1.5, 12.0),  # max
            Point(2.0, 2.0, 8.0),
        ],
        crs="EPSG:2154",
    )


# ---------------------------------------------------------------------------
# PointcloudLoadLas — uses laspy (real LAS file written to a temp path)
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_las(tmp_path: Path) -> Path:
    """Write a tiny synthetic LAS file for tests that exercise the loader."""
    laspy = pytest.importorskip("laspy")
    header = laspy.LasHeader(point_format=3, version="1.4")
    header.scales = (0.01, 0.01, 0.01)
    header.offsets = (0.0, 0.0, 0.0)

    las = laspy.LasData(header)
    n = 50
    rng = np.random.default_rng(123)
    las.x = rng.uniform(0, 100, n)
    las.y = rng.uniform(0, 100, n)
    las.z = rng.uniform(0, 50, n)
    las.intensity = rng.integers(0, 65535, n).astype(np.uint16)
    las.classification = rng.choice([2, 6, 7], n).astype(np.uint8)

    out = tmp_path / "synth.las"
    las.write(str(out))
    return out


class TestPointcloudLoadLas:
    def test_loads_all_points(self, synthetic_las):
        out = PointcloudLoadLasCapability().execute(
            gpd.GeoDataFrame(geometry=[]),
            path=str(synthetic_las),
            crs="EPSG:2154",
        )
        assert len(out) == 50
        assert "classification" in out.columns
        assert "intensity" in out.columns
        assert all(g.has_z for g in out.geometry)

    def test_classifications_filter(self, synthetic_las):
        out = PointcloudLoadLasCapability().execute(
            gpd.GeoDataFrame(geometry=[]),
            path=str(synthetic_las),
            crs="EPSG:2154",
            classifications=[2],
        )
        # Only ground points kept
        assert (out["classification"] == 2).all()

    def test_max_points_caps_load(self, synthetic_las):
        out = PointcloudLoadLasCapability().execute(
            gpd.GeoDataFrame(geometry=[]),
            path=str(synthetic_las),
            crs="EPSG:2154",
            max_points=10,
        )
        assert len(out) == 10

    def test_missing_path_raises(self):
        with pytest.raises(ValueError, match="requires 'path'"):
            PointcloudLoadLasCapability().execute(gpd.GeoDataFrame(geometry=[]))

    def test_nonexistent_path_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            PointcloudLoadLasCapability().execute(
                gpd.GeoDataFrame(geometry=[]),
                path=str(tmp_path / "nope.las"),
            )


# ---------------------------------------------------------------------------
# PointcloudFilterClassification
# ---------------------------------------------------------------------------


class TestPointcloudFilterClassification:
    def test_keep_ground_and_building(self, lidar_points):
        out = PointcloudFilterClassificationCapability().execute(
            lidar_points, keep=[2, 6],
        )
        assert set(out["classification"].unique()).issubset({2, 6})
        assert (out["classification"] == 7).sum() == 0

    def test_drop_noise(self, lidar_points):
        n_noise = (lidar_points["classification"] == 7).sum()
        out = PointcloudFilterClassificationCapability().execute(
            lidar_points, drop=[7],
        )
        assert len(out) == len(lidar_points) - n_noise
        assert (out["classification"] == 7).sum() == 0

    def test_either_keep_or_drop_required(self, lidar_points):
        with pytest.raises(ValueError, match="requires 'keep' or 'drop'"):
            PointcloudFilterClassificationCapability().execute(lidar_points)

    def test_both_raises(self, lidar_points):
        with pytest.raises(ValueError, match="not both"):
            PointcloudFilterClassificationCapability().execute(
                lidar_points, keep=[2], drop=[7],
            )

    def test_missing_column_raises(self):
        gdf = gpd.GeoDataFrame({"x": [1]}, geometry=[Point(0, 0, 0)], crs="EPSG:2154")
        with pytest.raises(KeyError):
            PointcloudFilterClassificationCapability().execute(gdf, keep=[2])


# ---------------------------------------------------------------------------
# PointcloudZonalHeight
# ---------------------------------------------------------------------------


class TestPointcloudZonalHeight:
    def test_computes_max_min_count_height(self, building_footprints, known_height_pointcloud):
        out = PointcloudZonalHeightCapability().execute(
            building_footprints,
            ref_gdf=known_height_pointcloud,
            stats=["max", "min", "count"],
        )
        assert "z_max" in out.columns
        assert "z_min" in out.columns
        assert "z_count" in out.columns
        assert "z_height" in out.columns
        # First footprint contains the 3 known points (Z=5, 12, 8)
        a = out[out["fid"] == "A"].iloc[0]
        assert a["z_max"] == 12.0
        assert a["z_min"] == 5.0
        assert a["z_count"] == 3
        assert a["z_height"] == 7.0  # 12 - 5
        # Second footprint has no points → NaN
        b = out[out["fid"] == "B"].iloc[0]
        assert pd.isna(b["z_max"])

    def test_height_with_ground_col(self, building_footprints, known_height_pointcloud):
        out = PointcloudZonalHeightCapability().execute(
            building_footprints,
            ref_gdf=known_height_pointcloud,
            stats=["max"],
            ground_col="ground_z",
        )
        a = out[out["fid"] == "A"].iloc[0]
        # ground_z=0 → height = max(12) - 0 = 12
        assert a["z_height"] == 12.0

    def test_percentiles(self, building_footprints, known_height_pointcloud):
        out = PointcloudZonalHeightCapability().execute(
            building_footprints,
            ref_gdf=known_height_pointcloud,
            stats=["p95"],
        )
        a = out[out["fid"] == "A"].iloc[0]
        # p95 of [5, 8, 12] = 12 - 0.05*(12-8) ~ approx 11.6
        assert a["z_p95"] == pytest.approx(11.6, abs=0.5)

    def test_unknown_stat_raises(self, building_footprints, known_height_pointcloud):
        with pytest.raises(ValueError, match="Unknown stat"):
            PointcloudZonalHeightCapability().execute(
                building_footprints,
                ref_gdf=known_height_pointcloud,
                stats=["bogus"],
            )

    def test_no_ref_raises(self, building_footprints):
        with pytest.raises(ValueError, match="reference layer"):
            PointcloudZonalHeightCapability().execute(building_footprints, ref_gdf=None)

    def test_no_intersection_returns_nans(self, building_footprints):
        # Points outside both footprints
        far_points = gpd.GeoDataFrame(
            {"classification": [2]},
            geometry=[Point(100, 100, 5)],
            crs="EPSG:2154",
        )
        out = PointcloudZonalHeightCapability().execute(
            building_footprints, ref_gdf=far_points,
        )
        assert out["z_max"].isna().all()
        assert out["z_height"].isna().all()


# ---------------------------------------------------------------------------
# PointcloudGridSummary
# ---------------------------------------------------------------------------


class TestPointcloudGridSummary:
    def test_grid_has_polygons(self, lidar_points):
        out = PointcloudGridSummaryCapability().execute(
            lidar_points, cell_size=2.0, stats=["mean", "count"],
        )
        assert "z_mean" in out.columns
        assert "z_count" in out.columns
        assert all(g.geom_type == "Polygon" for g in out.geometry)
        # Total point count across all cells equals input size
        assert out["z_count"].sum() == len(lidar_points)

    def test_drop_empty_default(self, lidar_points):
        out = PointcloudGridSummaryCapability().execute(
            lidar_points, cell_size=2.0,
        )
        # No cell has count == 0
        assert (out["z_count"] >= 1).all()

    def test_known_aggregation(self):
        # 4 points clustered into 2 cells of size 1
        gdf = gpd.GeoDataFrame(
            geometry=[
                Point(0.5, 0.5, 10), Point(0.7, 0.7, 20),  # cell (0,0): mean 15
                Point(1.5, 0.5, 100),                       # cell (0,1): mean 100
                Point(1.7, 1.7, 50),                        # cell (1,1): mean 50
            ],
            crs="EPSG:2154",
        )
        out = PointcloudGridSummaryCapability().execute(
            gdf, cell_size=1.0, stats=["mean", "count"],
        )
        # 3 unique cells expected
        assert len(out) == 3
        assert set(out["z_count"]) == {1, 1, 2}

    def test_zero_cell_size_raises(self, lidar_points):
        with pytest.raises(ValueError, match="cell_size must be > 0"):
            PointcloudGridSummaryCapability().execute(lidar_points, cell_size=0)

    def test_unknown_stat_raises(self, lidar_points):
        with pytest.raises(ValueError, match="Unknown stat"):
            PointcloudGridSummaryCapability().execute(
                lidar_points, cell_size=1.0, stats=["bogus"],
            )

    def test_empty_input(self):
        gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:2154")
        out = PointcloudGridSummaryCapability().execute(gdf, cell_size=1.0)
        assert len(out) == 0
