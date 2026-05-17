"""Tests for capabilities/raster.py — ZonalStatsCapability, ChangeDetectionCapability."""

from __future__ import annotations

import os

import geopandas as gpd
import pytest
from shapely.geometry import box

# ── Skip the whole module when optional deps are missing ──────────────────────
rasterio = pytest.importorskip("rasterio", reason="rasterio not installed")
rasterstats = pytest.importorskip("rasterstats", reason="rasterstats not installed")

import numpy as np
from rasterio.transform import from_bounds

from gispulse.capabilities.raster import ChangeDetectionCapability, ZonalStatsCapability


@pytest.fixture(autouse=True)
def pro_tier(monkeypatch):
    """All raster capabilities require Pro tier — activate it for every test in this module."""
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv("GISPULSE_LICENSE_KEY", "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


# ---------------------------------------------------------------------------
# Helpers — creates minimal GeoTIFF in a temp dir
# ---------------------------------------------------------------------------


def _write_tiff(path: str, data: np.ndarray, bounds=(0, 0, 1, 1), crs="EPSG:4326") -> None:
    height, width = data.shape
    transform = from_bounds(*bounds, width=width, height=height)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=data.dtype,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(data, 1)


@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def uniform_raster(tmp_dir) -> str:
    """Raster 10×10 avec toutes les cellules à 5."""
    path = os.path.join(tmp_dir, "uniform.tif")
    data = np.full((10, 10), 5.0, dtype=np.float32)
    _write_tiff(path, data)
    return path


@pytest.fixture
def changed_raster(tmp_dir) -> str:
    """Raster 10×10 : moitié gauche = 5, moitié droite = 50 (changement marqué)."""
    path = os.path.join(tmp_dir, "changed.tif")
    data = np.full((10, 10), 5.0, dtype=np.float32)
    data[:, 5:] = 50.0
    _write_tiff(path, data)
    return path


@pytest.fixture
def polygon_gdf() -> gpd.GeoDataFrame:
    """Un seul polygone couvrant tout le raster (0,0)→(1,1) en EPSG:4326."""
    return gpd.GeoDataFrame(
        [{"id": 1, "geometry": box(0, 0, 1, 1)}],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# ZonalStatsCapability
# ---------------------------------------------------------------------------


class TestZonalStats:
    def test_returns_geodataframe(self, polygon_gdf, uniform_raster):
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_adds_stat_columns(self, polygon_gdf, uniform_raster):
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster)
        assert "rs_mean" in result.columns
        assert "rs_min" in result.columns
        assert "rs_max" in result.columns

    def test_uniform_mean(self, polygon_gdf, uniform_raster):
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster, stats=["mean"])
        assert result["rs_mean"].iloc[0] == pytest.approx(5.0, abs=0.1)

    def test_custom_prefix(self, polygon_gdf, uniform_raster):
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster, prefix="z_", stats=["mean"])
        assert "z_mean" in result.columns

    def test_original_columns_preserved(self, polygon_gdf, uniform_raster):
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster)
        assert "id" in result.columns

    def test_missing_raster_raises(self, polygon_gdf):
        cap = ZonalStatsCapability()
        with pytest.raises(FileNotFoundError):
            cap.execute(polygon_gdf, raster_path="/nonexistent/file.tif")

    def test_no_raster_path_raises(self, polygon_gdf):
        cap = ZonalStatsCapability()
        with pytest.raises(ValueError, match="raster_path"):
            cap.execute(polygon_gdf)


# ---------------------------------------------------------------------------
# ChangeDetectionCapability
# ---------------------------------------------------------------------------


class TestChangeDetection:
    def test_returns_geodataframe(self, uniform_raster, changed_raster):
        cap = ChangeDetectionCapability()
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_before=uniform_raster,
            raster_after=changed_raster,
            threshold=10.0,
        )
        assert isinstance(result, gpd.GeoDataFrame)

    def test_detects_change(self, uniform_raster, changed_raster):
        cap = ChangeDetectionCapability()
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_before=uniform_raster,
            raster_after=changed_raster,
            threshold=10.0,
        )
        assert len(result) > 0

    def test_no_change_when_identical(self, uniform_raster):
        cap = ChangeDetectionCapability()
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_before=uniform_raster,
            raster_after=uniform_raster,
            threshold=0.0,
        )
        assert len(result) == 0

    def test_diff_mean_positive(self, uniform_raster, changed_raster):
        cap = ChangeDetectionCapability()
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_before=uniform_raster,
            raster_after=changed_raster,
            threshold=10.0,
        )
        assert result["diff_mean"].iloc[0] > 0

    def test_high_threshold_no_change(self, uniform_raster, changed_raster):
        cap = ChangeDetectionCapability()
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_before=uniform_raster,
            raster_after=changed_raster,
            threshold=1000.0,
        )
        assert len(result) == 0

    def test_missing_before_raises(self, changed_raster):
        cap = ChangeDetectionCapability()
        with pytest.raises(ValueError, match="raster_before"):
            cap.execute(gpd.GeoDataFrame(), raster_after=changed_raster)

    def test_nonexistent_file_raises(self, uniform_raster):
        cap = ChangeDetectionCapability()
        with pytest.raises(FileNotFoundError):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_before=uniform_raster,
                raster_after="/nonexistent.tif",
            )
