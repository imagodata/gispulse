"""Integration tests for raster I/O (persistence.raster_io)."""

from __future__ import annotations

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio", reason="rasterio not installed")

from rasterio.transform import from_bounds  # noqa: E402

from persistence.raster_io import (  # noqa: E402
    dataset_from_raster,
    detect_raster_format,
    read_raster,
    read_raster_metadata,
    raster_layer_from_file,
    supported_raster_extensions,
    write_raster,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_geotiff(tmp_path):
    """Create a small single-band GeoTIFF."""
    path = str(tmp_path / "dem.tif")
    height, width = 10, 15
    data = np.random.rand(1, height, width).astype("float32")
    transform = from_bounds(2.0, 48.0, 3.0, 49.0, width, height)

    with rasterio.open(
        path, "w",
        driver="GTiff",
        dtype="float32",
        width=width,
        height=height,
        count=1,
        crs="EPSG:4326",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(data)

    return path


@pytest.fixture
def tmp_multiband_tiff(tmp_path):
    """Create a 3-band GeoTIFF (RGB-like)."""
    path = str(tmp_path / "rgb.tif")
    height, width = 8, 12
    data = np.random.randint(0, 255, (3, height, width), dtype="uint8")
    transform = from_bounds(0.0, 0.0, 1.0, 1.0, width, height)

    with rasterio.open(
        path, "w",
        driver="GTiff",
        dtype="uint8",
        width=width,
        height=height,
        count=3,
        crs="EPSG:32631",
        transform=transform,
    ) as dst:
        dst.write(data)

    return path


# ---------------------------------------------------------------------------
# detect_raster_format
# ---------------------------------------------------------------------------


class TestDetectRasterFormat:
    def test_geotiff(self):
        assert detect_raster_format("data/dem.tif") == "GTiff"

    def test_tiff(self):
        assert detect_raster_format("data/image.tiff") == "GTiff"

    def test_jp2(self):
        assert detect_raster_format("image.jp2") == "JP2OpenJPEG"

    def test_asc(self):
        assert detect_raster_format("grid.asc") == "AAIGrid"

    def test_netcdf(self):
        assert detect_raster_format("climate.nc") == "netCDF"

    def test_unknown(self):
        assert detect_raster_format("file.xyz") is None

    def test_supported_extensions(self):
        exts = supported_raster_extensions()
        assert ".tif" in exts
        assert ".nc" in exts


# ---------------------------------------------------------------------------
# read_raster_metadata
# ---------------------------------------------------------------------------


class TestReadRasterMetadata:
    def test_single_band(self, tmp_geotiff):
        meta = read_raster_metadata(tmp_geotiff)
        assert meta["driver"] == "GTiff"
        assert meta["crs"] == "EPSG:4326"
        assert meta["band_count"] == 1
        assert meta["shape"]["height"] == 10
        assert meta["shape"]["width"] == 15
        assert meta["bands"][0]["dtype"] == "float32"
        assert meta["bands"][0]["nodata"] == -9999.0

    def test_multiband(self, tmp_multiband_tiff):
        meta = read_raster_metadata(tmp_multiband_tiff)
        assert meta["band_count"] == 3
        assert len(meta["bands"]) == 3
        assert all(b["dtype"] == "uint8" for b in meta["bands"])

    def test_bounds(self, tmp_geotiff):
        meta = read_raster_metadata(tmp_geotiff)
        bounds = meta["bounds"]
        assert bounds["minx"] == pytest.approx(2.0, abs=0.01)
        assert bounds["maxy"] == pytest.approx(49.0, abs=0.01)

    def test_resolution(self, tmp_geotiff):
        meta = read_raster_metadata(tmp_geotiff)
        res = meta["resolution"]
        assert res["x"] > 0
        assert res["y"] > 0


# ---------------------------------------------------------------------------
# read_raster
# ---------------------------------------------------------------------------


class TestReadRaster:
    def test_read_all_bands(self, tmp_geotiff):
        data, profile = read_raster(tmp_geotiff)
        assert data.shape == (1, 10, 15)
        assert profile["crs"].to_epsg() == 4326

    def test_read_specific_bands(self, tmp_multiband_tiff):
        data, profile = read_raster(tmp_multiband_tiff, bands=[1, 3])
        assert data.shape[0] == 2

    def test_read_window(self, tmp_geotiff):
        # Read a 5x5 window from row 2, col 3
        data, profile = read_raster(tmp_geotiff, window=(2, 3, 5, 5))
        assert data.shape == (1, 5, 5)


# ---------------------------------------------------------------------------
# write_raster
# ---------------------------------------------------------------------------


class TestWriteRaster:
    def test_write_geotiff(self, tmp_path):
        path = str(tmp_path / "output.tif")
        data = np.ones((1, 5, 5), dtype="float32") * 42.0
        transform = from_bounds(0, 0, 1, 1, 5, 5)
        write_raster(data, path, crs="EPSG:4326", transform=transform)

        meta = read_raster_metadata(path)
        assert meta["band_count"] == 1
        assert meta["shape"]["height"] == 5

    def test_write_2d_array(self, tmp_path):
        path = str(tmp_path / "single.tif")
        data = np.zeros((10, 10), dtype="float32")
        transform = from_bounds(0, 0, 1, 1, 10, 10)
        write_raster(data, path, crs="EPSG:4326", transform=transform)

        result, _ = read_raster(path)
        assert result.shape == (1, 10, 10)

    def test_write_with_nodata(self, tmp_path):
        path = str(tmp_path / "nodata.tif")
        data = np.ones((1, 5, 5), dtype="float32")
        transform = from_bounds(0, 0, 1, 1, 5, 5)
        write_raster(data, path, crs="EPSG:4326", transform=transform, nodata=-9999)

        meta = read_raster_metadata(path)
        assert meta["bands"][0]["nodata"] == -9999.0

    def test_write_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "sub" / "dir" / "out.tif")
        data = np.ones((1, 3, 3), dtype="float32")
        transform = from_bounds(0, 0, 1, 1, 3, 3)
        write_raster(data, path, crs="EPSG:4326", transform=transform)
        assert (tmp_path / "sub" / "dir" / "out.tif").exists()

    def test_write_unsupported_format_raises(self, tmp_path):
        path = str(tmp_path / "out.ecw")
        data = np.ones((1, 3, 3), dtype="float32")
        transform = from_bounds(0, 0, 1, 1, 3, 3)
        with pytest.raises(ValueError, match="Cannot write raster"):
            write_raster(data, path, crs="EPSG:4326", transform=transform)

    def test_roundtrip(self, tmp_path):
        path = str(tmp_path / "roundtrip.tif")
        original = np.random.rand(2, 8, 8).astype("float32")
        transform = from_bounds(0, 0, 1, 1, 8, 8)
        write_raster(original, path, crs="EPSG:4326", transform=transform)

        loaded, profile = read_raster(path)
        np.testing.assert_array_almost_equal(loaded, original, decimal=5)


# ---------------------------------------------------------------------------
# raster_layer_from_file
# ---------------------------------------------------------------------------


class TestRasterLayerFromFile:
    def test_creates_raster_layer(self, tmp_geotiff):
        rl = raster_layer_from_file(tmp_geotiff)
        assert rl.name == "dem"
        assert rl.crs == "EPSG:4326"
        assert len(rl.bands) == 1
        assert rl.bands[0].dtype == "float32"
        assert rl.bands[0].nodata == -9999.0

    def test_multiband(self, tmp_multiband_tiff):
        rl = raster_layer_from_file(tmp_multiband_tiff)
        assert len(rl.bands) == 3
        assert rl.resolution[0] > 0


# ---------------------------------------------------------------------------
# dataset_from_raster
# ---------------------------------------------------------------------------


class TestDatasetFromRaster:
    def test_creates_dataset(self, tmp_geotiff):
        ds = dataset_from_raster(tmp_geotiff)
        assert ds.name == "dem"
        assert ds.data_category == "raster"
        assert ds.format == "GTiff"
        assert ds.crs == "EPSG:4326"
        assert ds.metadata["band_count"] == 1

    def test_multiband_dataset(self, tmp_multiband_tiff):
        ds = dataset_from_raster(tmp_multiband_tiff)
        assert ds.metadata["band_count"] == 3
        assert len(ds.metadata["bands"]) == 3
        assert ds.metadata["shape"]["height"] == 8
