"""Tests for capabilities/raster.py — S11 capabilities.

Covers: ZonalStatsCapability, RasterClipCapability, NdviCapability,
        RasterReprojectCapability, RasterMergeCapability, ChangeDetectionCapability.

Includes tier-gating tests (all raster caps require Pro).
"""

from __future__ import annotations

import os

import geopandas as gpd
import pytest
from shapely.geometry import box

# Skip the whole module when optional deps are missing
rasterio = pytest.importorskip("rasterio", reason="rasterio not installed")
rasterstats = pytest.importorskip("rasterstats", reason="rasterstats not installed")

import numpy as np
from rasterio.transform import from_bounds

from gispulse.capabilities.raster import (
    ChangeDetectionCapability,
    NdviCapability,
    RasterClipCapability,
    RasterMergeCapability,
    RasterReprojectCapability,
    ZonalStatsCapability,
)
from gispulse.persistence.tier import TierError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_tiff(
    path: str,
    data: np.ndarray,
    bounds=(0, 0, 1, 1),
    crs: str = "EPSG:4326",
    nodata: float | None = None,
) -> None:
    """Write a single-band GeoTIFF to *path*."""
    height, width = data.shape[-2], data.shape[-1]
    # Support both 2D (H, W) and 3D (bands, H, W) arrays
    if data.ndim == 2:
        bands = 1
        write_data = data[np.newaxis]
    else:
        bands = data.shape[0]
        write_data = data

    transform = from_bounds(*bounds, width=width, height=height)
    kwargs = dict(
        driver="GTiff",
        height=height,
        width=width,
        count=bands,
        dtype=write_data.dtype,
        crs=crs,
        transform=transform,
    )
    if nodata is not None:
        kwargs["nodata"] = nodata

    with rasterio.open(path, "w", **kwargs) as dst:
        dst.write(write_data)


@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def uniform_raster(tmp_dir) -> str:
    """Single-band raster 10×10, all cells = 5."""
    path = os.path.join(tmp_dir, "uniform.tif")
    data = np.full((10, 10), 5.0, dtype=np.float32)
    _write_tiff(path, data)
    return path


@pytest.fixture
def changed_raster(tmp_dir) -> str:
    """Single-band raster 10×10: left half = 5, right half = 50."""
    path = os.path.join(tmp_dir, "changed.tif")
    data = np.full((10, 10), 5.0, dtype=np.float32)
    data[:, 5:] = 50.0
    _write_tiff(path, data)
    return path


@pytest.fixture
def multiband_raster(tmp_dir) -> str:
    """4-band raster 10×10: RED=band3=100, NIR=band4=200."""
    path = os.path.join(tmp_dir, "multiband.tif")
    data = np.zeros((4, 10, 10), dtype=np.float32)
    data[2] = 100.0  # RED  (band 3)
    data[3] = 200.0  # NIR  (band 4)
    _write_tiff(path, data)
    return path


@pytest.fixture
def polygon_gdf() -> gpd.GeoDataFrame:
    """Single polygon covering the full raster extent (0,0)→(1,1) in EPSG:4326."""
    return gpd.GeoDataFrame(
        [{"id": 1, "geometry": box(0, 0, 1, 1)}],
        crs="EPSG:4326",
    )


def _pro_env(monkeypatch):
    """Set environment variables to activate the Pro tier."""
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv("GISPULSE_LICENSE_KEY", "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


def _community_env(monkeypatch):
    """Reset to community tier."""
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.delenv("GISPULSE_LICENSE_KEY", raising=False)


# ---------------------------------------------------------------------------
# Tier gating — all raster capabilities require Pro
# ---------------------------------------------------------------------------


class TestRasterTierGating:
    def test_zonal_stats_blocked_in_community(self, monkeypatch, polygon_gdf, uniform_raster):
        _community_env(monkeypatch)
        cap = ZonalStatsCapability()
        with pytest.raises(TierError):
            cap.execute(polygon_gdf, raster_path=uniform_raster)

    def test_raster_clip_blocked_in_community(self, monkeypatch, polygon_gdf, uniform_raster, tmp_dir):
        _community_env(monkeypatch)
        cap = RasterClipCapability()
        with pytest.raises(TierError):
            cap.execute(
                polygon_gdf,
                raster_path=uniform_raster,
                output_path=os.path.join(tmp_dir, "out.tif"),
            )

    def test_ndvi_blocked_in_community(self, monkeypatch, multiband_raster, tmp_dir):
        _community_env(monkeypatch)
        cap = NdviCapability()
        with pytest.raises(TierError):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_path=multiband_raster,
                output_path=os.path.join(tmp_dir, "ndvi.tif"),
            )

    def test_raster_reproject_blocked_in_community(self, monkeypatch, uniform_raster, tmp_dir):
        _community_env(monkeypatch)
        cap = RasterReprojectCapability()
        with pytest.raises(TierError):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_path=uniform_raster,
                output_path=os.path.join(tmp_dir, "reproj.tif"),
            )

    def test_raster_merge_blocked_in_community(self, monkeypatch, uniform_raster, tmp_dir):
        _community_env(monkeypatch)
        cap = RasterMergeCapability()
        with pytest.raises(TierError):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_paths=[uniform_raster, uniform_raster],
                output_path=os.path.join(tmp_dir, "merged.tif"),
            )

    def test_change_detection_blocked_in_community(self, monkeypatch, uniform_raster, changed_raster):
        _community_env(monkeypatch)
        cap = ChangeDetectionCapability()
        with pytest.raises(TierError):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_before=uniform_raster,
                raster_after=changed_raster,
            )


# ---------------------------------------------------------------------------
# ZonalStatsCapability
# ---------------------------------------------------------------------------


class TestZonalStats:
    def test_returns_geodataframe(self, monkeypatch, polygon_gdf, uniform_raster):
        _pro_env(monkeypatch)
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster)
        assert isinstance(result, gpd.GeoDataFrame)

    def test_adds_stat_columns(self, monkeypatch, polygon_gdf, uniform_raster):
        _pro_env(monkeypatch)
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster)
        assert "rs_mean" in result.columns
        assert "rs_min" in result.columns
        assert "rs_max" in result.columns

    def test_uniform_mean(self, monkeypatch, polygon_gdf, uniform_raster):
        _pro_env(monkeypatch)
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster, stats=["mean"])
        assert result["rs_mean"].iloc[0] == pytest.approx(5.0, abs=0.1)

    def test_custom_prefix(self, monkeypatch, polygon_gdf, uniform_raster):
        _pro_env(monkeypatch)
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster, prefix="z_", stats=["mean"])
        assert "z_mean" in result.columns

    def test_original_columns_preserved(self, monkeypatch, polygon_gdf, uniform_raster):
        _pro_env(monkeypatch)
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster)
        assert "id" in result.columns

    def test_sum_stat(self, monkeypatch, polygon_gdf, uniform_raster):
        _pro_env(monkeypatch)
        cap = ZonalStatsCapability()
        result = cap.execute(polygon_gdf, raster_path=uniform_raster, stats=["sum"])
        assert result["rs_sum"].iloc[0] > 0

    def test_missing_raster_raises(self, monkeypatch, polygon_gdf):
        _pro_env(monkeypatch)
        cap = ZonalStatsCapability()
        with pytest.raises(FileNotFoundError):
            cap.execute(polygon_gdf, raster_path="/nonexistent/file.tif")

    def test_no_raster_path_raises(self, monkeypatch, polygon_gdf):
        _pro_env(monkeypatch)
        cap = ZonalStatsCapability()
        with pytest.raises(ValueError, match="raster_path"):
            cap.execute(polygon_gdf)

    def test_schema_contains_required(self):
        cap = ZonalStatsCapability()
        schema = cap.get_schema()
        assert "raster_path" in schema["required"]


# ---------------------------------------------------------------------------
# RasterClipCapability
# ---------------------------------------------------------------------------


class TestRasterClip:
    def test_returns_geodataframe(self, monkeypatch, polygon_gdf, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterClipCapability()
        out = os.path.join(tmp_dir, "clipped.tif")
        result = cap.execute(polygon_gdf, raster_path=uniform_raster, output_path=out)
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 1

    def test_output_file_created(self, monkeypatch, polygon_gdf, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterClipCapability()
        out = os.path.join(tmp_dir, "clipped.tif")
        cap.execute(polygon_gdf, raster_path=uniform_raster, output_path=out)
        assert os.path.exists(out)

    def test_output_path_column_present(self, monkeypatch, polygon_gdf, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterClipCapability()
        out = os.path.join(tmp_dir, "clipped.tif")
        result = cap.execute(polygon_gdf, raster_path=uniform_raster, output_path=out)
        assert "output_path" in result.columns
        assert result["output_path"].iloc[0] == out

    def test_missing_raster_raises(self, monkeypatch, polygon_gdf, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterClipCapability()
        with pytest.raises(FileNotFoundError):
            cap.execute(
                polygon_gdf,
                raster_path="/nonexistent.tif",
                output_path=os.path.join(tmp_dir, "out.tif"),
            )

    def test_no_raster_path_raises(self, monkeypatch, polygon_gdf, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterClipCapability()
        with pytest.raises(ValueError, match="raster_path"):
            cap.execute(polygon_gdf, output_path=os.path.join(tmp_dir, "out.tif"))

    def test_no_output_path_raises(self, monkeypatch, polygon_gdf, uniform_raster):
        _pro_env(monkeypatch)
        cap = RasterClipCapability()
        with pytest.raises(ValueError, match="output_path"):
            cap.execute(polygon_gdf, raster_path=uniform_raster)

    def test_empty_gdf_raises(self, monkeypatch, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterClipCapability()
        with pytest.raises(ValueError):
            cap.execute(
                gpd.GeoDataFrame(columns=["geometry"], geometry="geometry"),
                raster_path=uniform_raster,
                output_path=os.path.join(tmp_dir, "out.tif"),
            )

    def test_schema_required_params(self):
        cap = RasterClipCapability()
        schema = cap.get_schema()
        assert "raster_path" in schema["required"]
        assert "output_path" in schema["required"]


# ---------------------------------------------------------------------------
# NdviCapability
# ---------------------------------------------------------------------------


class TestNdvi:
    def test_returns_geodataframe(self, monkeypatch, multiband_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = NdviCapability()
        out = os.path.join(tmp_dir, "ndvi.tif")
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_path=multiband_raster,
            output_path=out,
            red_band=3,
            nir_band=4,
        )
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 1

    def test_output_file_created(self, monkeypatch, multiband_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = NdviCapability()
        out = os.path.join(tmp_dir, "ndvi.tif")
        cap.execute(gpd.GeoDataFrame(), raster_path=multiband_raster, output_path=out)
        assert os.path.exists(out)

    def test_ndvi_stats_columns_present(self, monkeypatch, multiband_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = NdviCapability()
        out = os.path.join(tmp_dir, "ndvi.tif")
        result = cap.execute(gpd.GeoDataFrame(), raster_path=multiband_raster, output_path=out)
        assert "ndvi_mean" in result.columns
        assert "ndvi_min" in result.columns
        assert "ndvi_max" in result.columns

    def test_ndvi_mean_correct_value(self, monkeypatch, multiband_raster, tmp_dir):
        """RED=100, NIR=200 → NDVI = (200-100)/(200+100) = 1/3 ≈ 0.333."""
        _pro_env(monkeypatch)
        cap = NdviCapability()
        out = os.path.join(tmp_dir, "ndvi.tif")
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_path=multiband_raster,
            output_path=out,
            red_band=3,
            nir_band=4,
        )
        assert result["ndvi_mean"].iloc[0] == pytest.approx(1 / 3, abs=0.01)

    def test_invalid_band_index_raises(self, monkeypatch, multiband_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = NdviCapability()
        with pytest.raises(ValueError, match="bands"):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_path=multiband_raster,
                output_path=os.path.join(tmp_dir, "ndvi.tif"),
                red_band=10,
                nir_band=11,
            )

    def test_missing_raster_raises(self, monkeypatch, tmp_dir):
        _pro_env(monkeypatch)
        cap = NdviCapability()
        with pytest.raises(FileNotFoundError):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_path="/no/such/file.tif",
                output_path=os.path.join(tmp_dir, "ndvi.tif"),
            )

    def test_no_raster_path_raises(self, monkeypatch, tmp_dir):
        _pro_env(monkeypatch)
        cap = NdviCapability()
        with pytest.raises(ValueError, match="raster_path"):
            cap.execute(gpd.GeoDataFrame(), output_path=os.path.join(tmp_dir, "ndvi.tif"))

    def test_schema_required_params(self):
        cap = NdviCapability()
        schema = cap.get_schema()
        assert "raster_path" in schema["required"]
        assert "output_path" in schema["required"]


# ---------------------------------------------------------------------------
# RasterReprojectCapability
# ---------------------------------------------------------------------------


class TestRasterReproject:
    def test_returns_geodataframe(self, monkeypatch, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterReprojectCapability()
        out = os.path.join(tmp_dir, "reproj.tif")
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_path=uniform_raster,
            output_path=out,
            target_crs="EPSG:3857",
        )
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 1

    def test_output_file_created(self, monkeypatch, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterReprojectCapability()
        out = os.path.join(tmp_dir, "reproj.tif")
        cap.execute(gpd.GeoDataFrame(), raster_path=uniform_raster, output_path=out, target_crs="EPSG:3857")
        assert os.path.exists(out)

    def test_output_has_correct_crs(self, monkeypatch, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterReprojectCapability()
        out = os.path.join(tmp_dir, "reproj.tif")
        cap.execute(gpd.GeoDataFrame(), raster_path=uniform_raster, output_path=out, target_crs="EPSG:3857")
        with rasterio.open(out) as src:
            assert src.crs.to_epsg() == 3857

    def test_target_crs_column_in_result(self, monkeypatch, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterReprojectCapability()
        out = os.path.join(tmp_dir, "reproj.tif")
        result = cap.execute(
            gpd.GeoDataFrame(), raster_path=uniform_raster, output_path=out, target_crs="EPSG:3857"
        )
        assert "target_crs" in result.columns

    def test_invalid_resampling_raises(self, monkeypatch, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterReprojectCapability()
        with pytest.raises(ValueError, match="resampling"):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_path=uniform_raster,
                output_path=os.path.join(tmp_dir, "reproj.tif"),
                resampling="unknown_algo",
            )

    def test_missing_raster_raises(self, monkeypatch, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterReprojectCapability()
        with pytest.raises(FileNotFoundError):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_path="/no/such.tif",
                output_path=os.path.join(tmp_dir, "reproj.tif"),
            )

    def test_no_raster_path_raises(self, monkeypatch, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterReprojectCapability()
        with pytest.raises(ValueError, match="raster_path"):
            cap.execute(gpd.GeoDataFrame(), output_path=os.path.join(tmp_dir, "reproj.tif"))


# ---------------------------------------------------------------------------
# RasterMergeCapability
# ---------------------------------------------------------------------------


class TestRasterMerge:
    def test_returns_geodataframe(self, monkeypatch, uniform_raster, changed_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterMergeCapability()
        out = os.path.join(tmp_dir, "merged.tif")
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_paths=[uniform_raster, changed_raster],
            output_path=out,
        )
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) == 1

    def test_output_file_created(self, monkeypatch, uniform_raster, changed_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterMergeCapability()
        out = os.path.join(tmp_dir, "merged.tif")
        cap.execute(gpd.GeoDataFrame(), raster_paths=[uniform_raster, changed_raster], output_path=out)
        assert os.path.exists(out)

    def test_n_sources_column(self, monkeypatch, uniform_raster, changed_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterMergeCapability()
        out = os.path.join(tmp_dir, "merged.tif")
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_paths=[uniform_raster, changed_raster],
            output_path=out,
        )
        assert result["n_sources"].iloc[0] == 2

    def test_single_raster_raises(self, monkeypatch, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterMergeCapability()
        with pytest.raises(ValueError, match="at least 2"):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_paths=[uniform_raster],
                output_path=os.path.join(tmp_dir, "out.tif"),
            )

    def test_empty_list_raises(self, monkeypatch, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterMergeCapability()
        with pytest.raises(ValueError, match="at least 2"):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_paths=[],
                output_path=os.path.join(tmp_dir, "out.tif"),
            )

    def test_nonexistent_raster_raises(self, monkeypatch, uniform_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterMergeCapability()
        with pytest.raises(FileNotFoundError):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_paths=[uniform_raster, "/no/such.tif"],
                output_path=os.path.join(tmp_dir, "out.tif"),
            )

    def test_invalid_method_raises(self, monkeypatch, uniform_raster, changed_raster, tmp_dir):
        _pro_env(monkeypatch)
        cap = RasterMergeCapability()
        with pytest.raises(ValueError, match="method"):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_paths=[uniform_raster, changed_raster],
                output_path=os.path.join(tmp_dir, "out.tif"),
                method="invalid",
            )

    def test_no_output_path_raises(self, monkeypatch, uniform_raster, changed_raster):
        _pro_env(monkeypatch)
        cap = RasterMergeCapability()
        with pytest.raises(ValueError, match="output_path"):
            cap.execute(
                gpd.GeoDataFrame(),
                raster_paths=[uniform_raster, changed_raster],
            )


# ---------------------------------------------------------------------------
# ChangeDetectionCapability
# ---------------------------------------------------------------------------


class TestChangeDetection:
    def test_returns_geodataframe(self, monkeypatch, uniform_raster, changed_raster):
        _pro_env(monkeypatch)
        cap = ChangeDetectionCapability()
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_before=uniform_raster,
            raster_after=changed_raster,
            threshold=10.0,
        )
        assert isinstance(result, gpd.GeoDataFrame)

    def test_detects_change(self, monkeypatch, uniform_raster, changed_raster):
        _pro_env(monkeypatch)
        cap = ChangeDetectionCapability()
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_before=uniform_raster,
            raster_after=changed_raster,
            threshold=10.0,
        )
        assert len(result) > 0

    def test_no_change_when_identical(self, monkeypatch, uniform_raster):
        _pro_env(monkeypatch)
        cap = ChangeDetectionCapability()
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_before=uniform_raster,
            raster_after=uniform_raster,
            threshold=0.0,
        )
        assert len(result) == 0

    def test_diff_mean_positive(self, monkeypatch, uniform_raster, changed_raster):
        _pro_env(monkeypatch)
        cap = ChangeDetectionCapability()
        result = cap.execute(
            gpd.GeoDataFrame(),
            raster_before=uniform_raster,
            raster_after=changed_raster,
            threshold=10.0,
        )
        assert result["diff_mean"].iloc[0] > 0

    def test_missing_before_raises(self, monkeypatch, changed_raster):
        _pro_env(monkeypatch)
        cap = ChangeDetectionCapability()
        with pytest.raises(ValueError, match="raster_before"):
            cap.execute(gpd.GeoDataFrame(), raster_after=changed_raster)
