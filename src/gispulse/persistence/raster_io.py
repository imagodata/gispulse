"""
Raster I/O for GISPulse.

Supports reading raster metadata and data via rasterio/GDAL.
Handles GeoTIFF, COG, JPEG2000, ASCII Grid, NetCDF, HDF5, and other
rasterio-supported formats.

Usage::

    from gispulse.persistence.raster_io import read_raster_metadata, dataset_from_raster

    meta = read_raster_metadata("data/dem.tif")
    dataset = dataset_from_raster("data/satellite.tif")
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import numpy as np

from gispulse.core.models import Dataset, DataCategory, RasterBand, RasterLayer


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

#: Map of file extensions to rasterio/GDAL driver names.
RASTER_DRIVERS: dict[str, str] = {
    ".tif": "GTiff",
    ".tiff": "GTiff",
    ".jp2": "JP2OpenJPEG",
    ".img": "HFA",
    ".asc": "AAIGrid",
    ".nc": "netCDF",
    ".hdf": "HDF4",
    ".h5": "HDF5",
    ".hdf5": "HDF5",
    ".vrt": "VRT",
    ".ecw": "ECW",
    ".sid": "MrSID",
    ".hgt": "SRTMHGT",
    ".png": "PNG",
}

#: Formats writable via rasterio.
RASTER_WRITABLE = {".tif", ".tiff", ".png", ".jp2", ".asc"}


def detect_raster_format(path: str) -> str | None:
    """Detect raster format from file extension."""
    ext = Path(path).suffix.lower()
    return RASTER_DRIVERS.get(ext)


def supported_raster_extensions() -> list[str]:
    """Return all supported raster file extensions."""
    return sorted(RASTER_DRIVERS.keys())


# ---------------------------------------------------------------------------
# Read metadata (lightweight, no pixel data loaded)
# ---------------------------------------------------------------------------


def read_raster_metadata(path: str) -> dict[str, Any]:
    """Read raster metadata without loading pixel data.

    Returns a dict with: crs, bounds, resolution, shape, band_count,
    dtypes, nodata values, driver, and per-band statistics if available.
    """
    import rasterio

    with rasterio.open(path) as src:
        bands_info = []
        for i in range(1, src.count + 1):
            band_info: dict[str, Any] = {
                "index": i,
                "dtype": str(src.dtypes[i - 1]),
                "nodata": src.nodatavals[i - 1],
            }
            # Try to get band descriptions
            desc = src.descriptions[i - 1] if src.descriptions else None
            band_info["name"] = desc

            # Statistics if available in tags
            tags = src.tags(i)
            if "STATISTICS_MINIMUM" in tags:
                band_info["min"] = float(tags["STATISTICS_MINIMUM"])
            if "STATISTICS_MAXIMUM" in tags:
                band_info["max"] = float(tags["STATISTICS_MAXIMUM"])

            bands_info.append(band_info)

        return {
            "driver": src.driver,
            "crs": str(src.crs) if src.crs else None,
            "bounds": {
                "minx": src.bounds.left,
                "miny": src.bounds.bottom,
                "maxx": src.bounds.right,
                "maxy": src.bounds.top,
            },
            "resolution": {"x": src.res[0], "y": src.res[1]},
            "shape": {"height": src.height, "width": src.width},
            "band_count": src.count,
            "bands": bands_info,
            "transform": list(src.transform)[:6],
            "is_tiled": src.is_tiled,
        }


# ---------------------------------------------------------------------------
# Read pixel data
# ---------------------------------------------------------------------------


def read_raster(
    path: str,
    bands: Optional[list[int]] = None,
    window: Optional[tuple[int, int, int, int]] = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Read raster pixel data as a numpy array.

    Args:
        path:   Path to raster file.
        bands:  Band indices to read (1-based). None = all bands.
        window: Pixel window (row_off, col_off, height, width). None = full extent.

    Returns:
        Tuple of (ndarray of shape [bands, height, width], profile dict).
    """
    import rasterio
    from rasterio.windows import Window

    with rasterio.open(path) as src:
        rio_window = None
        if window:
            rio_window = Window(
                col_off=window[1], row_off=window[0],
                width=window[3], height=window[2],
            )

        indexes = bands or list(range(1, src.count + 1))
        data = src.read(indexes=indexes, window=rio_window)

        profile = dict(src.profile)
        if rio_window:
            profile.update({
                "height": rio_window.height,
                "width": rio_window.width,
                "transform": src.window_transform(rio_window),
            })

        return data, profile


# ---------------------------------------------------------------------------
# Write raster
# ---------------------------------------------------------------------------


def write_raster(
    data: "np.ndarray",
    path: str,
    crs: str,
    transform: Any,
    nodata: Optional[float] = None,
    dtype: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Write a numpy array as a raster file.

    Args:
        data:      Array of shape [bands, height, width] or [height, width].
        path:      Output file path.
        crs:       CRS string (e.g. "EPSG:4326").
        transform: Affine transform.
        nodata:    Nodata value.
        dtype:     Output dtype. Defaults to data.dtype.
        **kwargs:  Extra rasterio profile options.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import Affine

    ext = Path(path).suffix.lower()
    if ext not in RASTER_WRITABLE:
        raise ValueError(f"Cannot write raster to '{ext}'. Writable: {sorted(RASTER_WRITABLE)}")

    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if data.ndim == 2:
        data = data[np.newaxis, :, :]

    count, height, width = data.shape

    if isinstance(transform, (list, tuple)):
        transform = Affine(*transform[:6])

    profile = {
        "driver": RASTER_DRIVERS.get(ext, "GTiff"),
        "dtype": dtype or str(data.dtype),
        "width": width,
        "height": height,
        "count": count,
        "crs": crs,
        "transform": transform,
    }
    if nodata is not None:
        profile["nodata"] = nodata

    profile.update(kwargs)

    # Atomic write: write to temp file first, then rename
    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, dir=str(Path(path).parent))
    try:
        import os
        os.close(tmp_fd)
        with rasterio.open(tmp_path, "w", **profile) as dst:
            dst.write(data)
        Path(tmp_path).replace(path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Domain factory
# ---------------------------------------------------------------------------


def raster_layer_from_file(path: str) -> RasterLayer:
    """Create a RasterLayer domain object from a raster file.

    Reads metadata only (no pixel data loaded).
    """
    meta = read_raster_metadata(path)
    path_obj = Path(path)

    bands = [
        RasterBand(
            index=b["index"],
            name=b.get("name"),
            nodata=b.get("nodata"),
            min=b.get("min"),
            max=b.get("max"),
            dtype=b.get("dtype"),
        )
        for b in meta["bands"]
    ]

    crs = meta["crs"] or "EPSG:4326"
    bounds = meta["bounds"]

    return RasterLayer(
        name=path_obj.stem,
        source=str(path_obj.resolve()),
        crs=crs,
        resolution=(meta["resolution"]["x"], meta["resolution"]["y"]),
        bounds=(bounds["minx"], bounds["miny"], bounds["maxx"], bounds["maxy"]),
        bands=bands,
        nodata=bands[0].nodata if bands else None,
        metadata={
            "driver": meta["driver"],
            "shape": meta["shape"],
            "is_tiled": meta["is_tiled"],
        },
    )


def dataset_from_raster(path: str) -> Dataset:
    """Create a Dataset domain object from a raster file.

    Args:
        path: Path to a raster file (.tif, .jp2, .asc, .nc, etc.)

    Returns:
        Dataset with raster metadata populated.
    """
    meta = read_raster_metadata(path)
    path_obj = Path(path)

    crs = meta["crs"] or "EPSG:4326"
    bounds = meta["bounds"]

    dataset = Dataset(
        name=path_obj.stem,
        source_path=str(path_obj.resolve()),
        data_category=DataCategory.RASTER.value,
        crs=crs,
        format=meta["driver"],
    )

    dataset.metadata["band_count"] = meta["band_count"]
    dataset.metadata["shape"] = meta["shape"]
    dataset.metadata["resolution"] = meta["resolution"]
    dataset.metadata["bounds"] = bounds
    dataset.metadata["is_tiled"] = meta["is_tiled"]
    dataset.metadata["bands"] = [
        {
            "index": b["index"],
            "name": b.get("name"),
            "dtype": b.get("dtype"),
            "nodata": b.get("nodata"),
        }
        for b in meta["bands"]
    ]

    return dataset
