"""Tests for persistence.raster_io — raster metadata/read/write + factories.

rasterio is optional at runtime, so we skip the whole module if not
installed. Covers format detection, metadata read, windowed read,
atomic write, and the Dataset/RasterLayer factories.
"""
from __future__ import annotations

from pathlib import Path

import pytest

rasterio = pytest.importorskip("rasterio", reason="rasterio not installed")

import numpy as np
from rasterio.transform import from_origin

from persistence.raster_io import (
    RASTER_DRIVERS,
    RASTER_WRITABLE,
    dataset_from_raster,
    detect_raster_format,
    raster_layer_from_file,
    read_raster,
    read_raster_metadata,
    supported_raster_extensions,
    write_raster,
)


@pytest.fixture
def sample_raster(tmp_path) -> str:
    """Create a small 10x20 single-band GeoTIFF at EPSG:4326."""
    path = tmp_path / "sample.tif"
    data = np.arange(200, dtype=np.float32).reshape(1, 10, 20)
    transform = from_origin(west=0.0, north=10.0, xsize=1.0, ysize=1.0)
    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "width": 20,
        "height": 10,
        "count": 1,
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": -9999,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    return str(path)


@pytest.fixture
def multi_band_raster(tmp_path) -> str:
    """RGB-like 3-band raster for multi-band tests."""
    path = tmp_path / "rgb.tif"
    data = np.stack([
        np.full((5, 5), 10, dtype=np.uint8),
        np.full((5, 5), 20, dtype=np.uint8),
        np.full((5, 5), 30, dtype=np.uint8),
    ])
    transform = from_origin(0.0, 5.0, 1.0, 1.0)
    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "width": 5,
        "height": 5,
        "count": 3,
        "crs": "EPSG:4326",
        "transform": transform,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    return str(path)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


class TestFormatDetection:
    @pytest.mark.parametrize(
        "ext,expected_driver",
        [
            (".tif", "GTiff"),
            (".tiff", "GTiff"),
            (".jp2", "JP2OpenJPEG"),
            (".asc", "AAIGrid"),
            (".png", "PNG"),
            (".nc", "netCDF"),
            (".vrt", "VRT"),
        ],
    )
    def test_known_extensions(self, ext, expected_driver):
        assert detect_raster_format(f"/data/x{ext}") == expected_driver

    def test_unknown_extension_returns_none(self):
        assert detect_raster_format("/data/foo.unknown") is None

    def test_case_insensitive(self):
        assert detect_raster_format("/data/x.TIF") == "GTiff"
        assert detect_raster_format("/data/x.Tiff") == "GTiff"

    def test_supported_extensions_is_sorted(self):
        exts = supported_raster_extensions()
        assert exts == sorted(exts)
        assert ".tif" in exts
        assert ".jp2" in exts

    def test_writable_is_subset_of_supported(self):
        assert RASTER_WRITABLE.issubset(set(RASTER_DRIVERS.keys()))


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestReadRasterMetadata:
    def test_single_band_shape(self, sample_raster):
        meta = read_raster_metadata(sample_raster)
        assert meta["shape"] == {"height": 10, "width": 20}
        assert meta["band_count"] == 1

    def test_crs_populated(self, sample_raster):
        meta = read_raster_metadata(sample_raster)
        assert "4326" in meta["crs"]

    def test_bounds_populated(self, sample_raster):
        meta = read_raster_metadata(sample_raster)
        b = meta["bounds"]
        assert b["minx"] == 0.0
        assert b["maxx"] == 20.0
        assert b["maxy"] == 10.0

    def test_resolution_populated(self, sample_raster):
        meta = read_raster_metadata(sample_raster)
        assert meta["resolution"]["x"] == 1.0

    def test_band_nodata_roundtrips(self, sample_raster):
        meta = read_raster_metadata(sample_raster)
        assert meta["bands"][0]["nodata"] == -9999

    def test_band_dtype(self, sample_raster):
        meta = read_raster_metadata(sample_raster)
        assert meta["bands"][0]["dtype"] == "float32"

    def test_multi_band_count(self, multi_band_raster):
        meta = read_raster_metadata(multi_band_raster)
        assert meta["band_count"] == 3
        assert len(meta["bands"]) == 3

    def test_transform_is_6_elements(self, sample_raster):
        meta = read_raster_metadata(sample_raster)
        assert len(meta["transform"]) == 6


# ---------------------------------------------------------------------------
# Read pixel data
# ---------------------------------------------------------------------------


class TestReadRaster:
    def test_read_full_shape(self, sample_raster):
        data, profile = read_raster(sample_raster)
        assert data.shape == (1, 10, 20)
        assert profile["height"] == 10
        assert profile["width"] == 20

    def test_read_band_subset(self, multi_band_raster):
        data, profile = read_raster(multi_band_raster, bands=[1, 3])
        assert data.shape[0] == 2
        # band 1 is all 10s, band 3 all 30s
        assert data[0, 0, 0] == 10
        assert data[1, 0, 0] == 30

    def test_window_read_restricts_shape(self, sample_raster):
        # (row_off, col_off, height, width)
        data, profile = read_raster(sample_raster, window=(2, 5, 3, 4))
        assert data.shape == (1, 3, 4)
        assert profile["height"] == 3
        assert profile["width"] == 4


# ---------------------------------------------------------------------------
# Write raster
# ---------------------------------------------------------------------------


class TestWriteRaster:
    def test_round_trip_2d_array(self, tmp_path):
        out = tmp_path / "out.tif"
        data = np.arange(25, dtype=np.int16).reshape(5, 5)
        write_raster(
            data,
            str(out),
            crs="EPSG:4326",
            transform=from_origin(0, 5, 1, 1),
        )
        assert out.exists()
        meta = read_raster_metadata(str(out))
        assert meta["band_count"] == 1
        assert meta["shape"] == {"height": 5, "width": 5}

    def test_round_trip_3d_array(self, tmp_path):
        out = tmp_path / "rgb.tif"
        data = np.zeros((3, 4, 6), dtype=np.uint8)
        write_raster(
            data,
            str(out),
            crs="EPSG:4326",
            transform=from_origin(0, 4, 1, 1),
        )
        meta = read_raster_metadata(str(out))
        assert meta["band_count"] == 3

    def test_write_creates_parent_dir(self, tmp_path):
        out = tmp_path / "nested" / "dir" / "x.tif"
        data = np.zeros((2, 2), dtype=np.float32)
        write_raster(
            data,
            str(out),
            crs="EPSG:4326",
            transform=from_origin(0, 2, 1, 1),
        )
        assert out.exists()

    def test_unsupported_extension_raises(self, tmp_path):
        out = tmp_path / "bad.xyz"
        data = np.zeros((2, 2), dtype=np.float32)
        with pytest.raises(ValueError, match="Cannot write"):
            write_raster(
                data, str(out), crs="EPSG:4326",
                transform=from_origin(0, 2, 1, 1),
            )

    def test_accepts_list_transform(self, tmp_path):
        """Affine(*list) conversion path — allows JSON-serialised transforms."""
        out = tmp_path / "out.tif"
        data = np.zeros((2, 2), dtype=np.float32)
        write_raster(
            data,
            str(out),
            crs="EPSG:4326",
            transform=[1.0, 0.0, 0.0, 0.0, -1.0, 2.0],
        )
        assert out.exists()

    def test_nodata_persisted(self, tmp_path):
        out = tmp_path / "nd.tif"
        data = np.zeros((3, 3), dtype=np.float32)
        write_raster(
            data,
            str(out),
            crs="EPSG:4326",
            transform=from_origin(0, 3, 1, 1),
            nodata=-1.5,
        )
        meta = read_raster_metadata(str(out))
        assert meta["bands"][0]["nodata"] == -1.5

    def test_no_tempfile_leftover_on_error(self, tmp_path):
        """Atomic write: failure must not leave a temp file."""
        out = tmp_path / "fail.tif"
        # invalid CRS string forces a rasterio error after tempfile creation
        data = np.zeros((2, 2), dtype=np.float32)
        with pytest.raises(Exception):
            write_raster(
                data, str(out), crs="NOT_A_REAL_CRS:9999",
                transform=from_origin(0, 2, 1, 1),
            )
        # No temp file left behind (all .tif files cleared)
        leftovers = list(tmp_path.glob("*.tif"))
        assert leftovers == []


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class TestRasterLayerFromFile:
    def test_populates_basic_fields(self, sample_raster):
        layer = raster_layer_from_file(sample_raster)
        assert layer.name == "sample"
        assert "4326" in layer.crs
        assert layer.bounds == (0.0, 0.0, 20.0, 10.0)
        assert layer.resolution == (1.0, 1.0)
        assert len(layer.bands) == 1
        assert layer.bands[0].nodata == -9999

    def test_multi_band(self, multi_band_raster):
        layer = raster_layer_from_file(multi_band_raster)
        assert len(layer.bands) == 3
        # metadata passthrough
        assert layer.metadata.get("driver") == "GTiff"

    def test_missing_crs_falls_back_to_epsg_4326(self, tmp_path):
        """When the file has no CRS, RasterLayer.crs defaults to EPSG:4326."""
        path = tmp_path / "nocrs.tif"
        data = np.zeros((2, 2), dtype=np.float32)
        profile = {
            "driver": "GTiff",
            "dtype": "float32",
            "width": 2,
            "height": 2,
            "count": 1,
            "transform": from_origin(0, 2, 1, 1),
            # no crs
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data, 1)
        layer = raster_layer_from_file(str(path))
        assert layer.crs == "EPSG:4326"


class TestDatasetFromRaster:
    def test_dataset_populated(self, sample_raster):
        ds = dataset_from_raster(sample_raster)
        assert ds.name == "sample"
        assert ds.data_category == "raster"
        assert "GTiff" in (ds.format or "") or ds.format  # non-empty
