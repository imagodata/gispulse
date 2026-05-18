"""
Unified multi-format I/O for GISPulse.

Supports reading and writing vector and tabular-geo files via GeoPandas/Fiona.
Works for both session mode (local files) and persistent mode (import into PostGIS).

Supported vector formats:
    GPKG, GeoJSON, Shapefile, FlatGeobuf, GML, KML, DXF, CSV (with geometry),
    GeoParquet, SpatiaLite, TopoJSON, OpenFileGDB.

Usage::

    from gispulse.persistence.io import read_vector, write_vector, detect_format, dataset_from_file

    gdf = read_vector("data/parcels.geojson")
    write_vector(gdf, "output/result.fgb")

    dataset = dataset_from_file("data/network.gpkg")
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import geopandas as gpd

from gispulse.core.models import Dataset, DataCategory


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

#: Map of file extensions to Fiona/GDAL driver names.
VECTOR_DRIVERS: dict[str, str] = {
    ".gpkg": "GPKG",
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".shp": "ESRI Shapefile",
    ".fgb": "FlatGeobuf",
    ".gml": "GML",
    ".kml": "KML",
    ".dxf": "DXF",
    ".sqlite": "SQLite",
    ".gdb": "OpenFileGDB",
    ".parquet": "Parquet",
    ".csv": "CSV",
    ".tsv": "CSV",
    ".xlsx": "XLSX",
}

#: Formats that support multiple layers.
MULTI_LAYER_FORMATS = {".gpkg", ".gdb", ".sqlite"}

#: Formats that are write-capable via GeoPandas/Fiona.
WRITABLE_FORMATS = {
    ".gpkg", ".geojson", ".json", ".shp", ".fgb",
    ".gml", ".parquet", ".sqlite",
}


def detect_format(path: str) -> str | None:
    """Detect the spatial file format from extension.

    Returns the Fiona/GDAL driver name, or None if unrecognized.
    """
    ext = Path(path).suffix.lower()
    return VECTOR_DRIVERS.get(ext)


def supported_extensions() -> list[str]:
    """Return all supported file extensions."""
    return sorted(VECTOR_DRIVERS.keys())


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def list_layers(path: str) -> list[str]:
    """List available layers in a multi-layer file (GPKG, GDB, SQLite).

    For single-layer formats, returns a list with one empty-string element.
    """
    import pyogrio

    ext = Path(path).suffix.lower()
    if ext in MULTI_LAYER_FORMATS:
        info = pyogrio.list_layers(path)
        return [row[0] for row in info]
    return [""]


def read_vector(
    path: str,
    layer: Optional[str] = None,
    crs: Optional[str] = None,
    bbox: Optional[tuple[float, float, float, float]] = None,
    rows: Optional[int] = None,
    **kwargs: Any,
) -> gpd.GeoDataFrame:
    """Read a vector/tabular-geo file into a GeoDataFrame.

    Args:
        path:  File path or URI.
        layer: Layer name (for multi-layer formats). None = first layer.
        crs:   Force a CRS if the file has none (e.g. CSV without .prj).
        bbox:  Spatial filter (minx, miny, maxx, maxy).
        rows:  Read only the first N rows.
        **kwargs: Extra arguments passed to geopandas.read_file / read_parquet.

    Returns:
        GeoDataFrame with the file contents.

    Raises:
        ValueError: If the format is not recognized.
        FileNotFoundError: If the file does not exist.
    """
    path_obj = Path(path)
    if not path_obj.exists():
        raise FileNotFoundError(f"File not found: {path}")

    ext = path_obj.suffix.lower()
    driver = VECTOR_DRIVERS.get(ext)
    if driver is None:
        raise ValueError(
            f"Unsupported format '{ext}'. Supported: {supported_extensions()}"
        )

    # --- GeoParquet: dedicated reader with bbox pushdown ---
    if ext == ".parquet":
        parquet_kwargs: dict[str, Any] = {**kwargs}
        if bbox:
            # Use native bbox pushdown (GeoPandas 0.14+ / pyarrow)
            parquet_kwargs["bbox"] = tuple(bbox) if not isinstance(bbox, tuple) else bbox
        gdf = gpd.read_parquet(path, **parquet_kwargs)
        if rows:
            gdf = gdf.head(rows)
        if crs and gdf.crs is None:
            gdf = gdf.set_crs(crs)
        return gdf

    # --- CSV/TSV: try lat/lon columns ---
    if ext in (".csv", ".tsv"):
        return _read_csv_geo(path, crs=crs, rows=rows, **kwargs)

    # --- XLSX: pandas then geopandas ---
    if ext == ".xlsx":
        return _read_xlsx_geo(path, crs=crs, rows=rows, **kwargs)

    # --- Standard Fiona-based formats ---
    read_kwargs: dict[str, Any] = {}
    if layer:
        read_kwargs["layer"] = layer
    if bbox:
        read_kwargs["bbox"] = bbox
    if rows:
        read_kwargs["rows"] = rows
    if driver == "KML":
        read_kwargs["driver"] = "KML"

    read_kwargs.update(kwargs)
    gdf = gpd.read_file(path, **read_kwargs)

    if crs and gdf.crs is None:
        gdf = gdf.set_crs(crs)

    return gdf


def read_vector_chunked(
    path: str,
    layer: Optional[str] = None,
    chunk_size: int = 50_000,
    crs: Optional[str] = None,
    bbox: Optional[tuple[float, float, float, float]] = None,
    **kwargs: Any,
) -> Iterator[gpd.GeoDataFrame]:
    """Read a vector file in chunks to avoid OOM on large datasets.

    Yields GeoDataFrame chunks of ``chunk_size`` features each.
    Supported for GPKG, Shapefile, FlatGeobuf, and other Fiona-based formats.
    GeoParquet uses pyarrow row-group based chunking.

    Args:
        path:       File path.
        layer:      Layer name (multi-layer formats).
        chunk_size: Number of features per chunk.
        crs:        Force CRS if missing from file.
        bbox:       Bounding box filter (minx, miny, maxx, maxy).
        **kwargs:   Extra arguments passed to the reader.

    Yields:
        GeoDataFrame chunks.
    """

    path_obj = Path(path)
    ext = path_obj.suffix.lower()

    if ext == ".parquet":
        # GeoParquet: use pyarrow for efficient chunked reading
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=chunk_size):
            chunk = gpd.GeoDataFrame.from_arrow(batch)
            if bbox:
                chunk = chunk.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
            if crs and chunk.crs is None:
                chunk = chunk.set_crs(crs)
            if len(chunk) > 0:
                yield chunk
        return

    # Fiona-based formats: use rows parameter for offset pagination
    import pyogrio

    total = pyogrio.read_info(path, layer=layer).get("features", 0) if layer else \
            pyogrio.read_info(path).get("features", 0)

    offset = 0
    while offset < total:
        read_kwargs: dict[str, Any] = {"skip_features": offset, "max_features": chunk_size}
        if layer:
            read_kwargs["layer"] = layer
        if bbox:
            read_kwargs["bbox"] = bbox
        read_kwargs.update(kwargs)

        chunk = gpd.read_file(path, **read_kwargs)
        if crs and chunk.crs is None:
            chunk = chunk.set_crs(crs)
        if len(chunk) == 0:
            break
        yield chunk
        offset += chunk_size


def _read_csv_geo(
    path: str,
    crs: Optional[str] = None,
    rows: Optional[int] = None,
    lat_col: str | None = None,
    lon_col: str | None = None,
    geom_col: str | None = None,
    **kwargs: Any,
) -> gpd.GeoDataFrame:
    """Read a CSV/TSV with geographic columns into a GeoDataFrame.

    Auto-detects common lat/lon column names if not provided.
    Also supports a WKT geometry column.
    """
    import pandas as pd

    sep = "\t" if path.endswith(".tsv") else ","
    nrows = rows if rows else None
    df = pd.read_csv(path, sep=sep, nrows=nrows, **kwargs)

    # Try WKT geometry column first
    if geom_col and geom_col in df.columns:
        from shapely import wkt
        gdf = gpd.GeoDataFrame(
            df, geometry=df[geom_col].apply(wkt.loads), crs=crs or "EPSG:4326"
        )
        return gdf

    # Auto-detect WKT column
    wkt_candidates = [c for c in df.columns if c.lower() in ("geom", "geometry", "wkt", "the_geom")]
    if wkt_candidates:
        from shapely import wkt
        col = wkt_candidates[0]
        gdf = gpd.GeoDataFrame(
            df, geometry=df[col].apply(wkt.loads), crs=crs or "EPSG:4326"
        )
        return gdf

    # Auto-detect lat/lon columns
    lat_candidates = ("lat", "latitude", "y", "lat_wgs84", "LAT", "Latitude")
    lon_candidates = ("lon", "lng", "longitude", "x", "lon_wgs84", "LON", "Longitude")

    if lat_col is None:
        for c in lat_candidates:
            if c in df.columns:
                lat_col = c
                break
    if lon_col is None:
        for c in lon_candidates:
            if c in df.columns:
                lon_col = c
                break

    if lat_col is None or lon_col is None:
        raise ValueError(
            f"Cannot detect geometry columns in CSV. "
            f"Columns found: {list(df.columns)}. "
            f"Provide lat_col/lon_col or a WKT geom_col."
        )

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[lon_col], df[lat_col]),
        crs=crs or "EPSG:4326",
    )
    return gdf


def _read_xlsx_geo(
    path: str,
    crs: Optional[str] = None,
    rows: Optional[int] = None,
    lat_col: str | None = None,
    lon_col: str | None = None,
    sheet_name: int | str = 0,
    **kwargs: Any,
) -> gpd.GeoDataFrame:
    """Read an XLSX file with lat/lon or WKT columns into a GeoDataFrame."""
    import pandas as pd

    nrows = rows if rows else None
    df = pd.read_excel(path, sheet_name=sheet_name, nrows=nrows, **kwargs)

    # Reuse the CSV geo logic on the dataframe
    # Auto-detect lat/lon
    lat_candidates = ("lat", "latitude", "y", "LAT", "Latitude")
    lon_candidates = ("lon", "lng", "longitude", "x", "LON", "Longitude")

    if lat_col is None:
        for c in lat_candidates:
            if c in df.columns:
                lat_col = c
                break
    if lon_col is None:
        for c in lon_candidates:
            if c in df.columns:
                lon_col = c
                break

    if lat_col is None or lon_col is None:
        raise ValueError(
            f"Cannot detect lat/lon columns in XLSX. "
            f"Columns found: {list(df.columns)}. Provide lat_col/lon_col."
        )

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[lon_col], df[lat_col]),
        crs=crs or "EPSG:4326",
    )
    return gdf


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_vector(
    gdf: gpd.GeoDataFrame,
    path: str,
    layer: Optional[str] = None,
    mode: str = "w",
    write_style_sidecars: bool = True,
    **kwargs: Any,
) -> None:
    """Write a GeoDataFrame to a spatial file.

    If the GeoDataFrame carries ``gispulse_style`` or ``gispulse_legend``
    metadata in ``gdf.attrs`` (emitted by classify/choropleth/categorical/…),
    matching sidecar files (``.style.qml``, ``.style.sld``, ``.legend.json``)
    are written next to the main file so QGIS, GeoServer, and the portal can
    consume the renderer without re-deriving it.

    Args:
        gdf:   GeoDataFrame to write.
        path:  Output file path.
        layer: Layer name (for multi-layer formats like GPKG).
        mode:  'w' (overwrite) or 'a' (append, GPKG only).
        write_style_sidecars: Set False to skip QML/SLD/legend emission.
        **kwargs: Extra arguments passed to gdf.to_file / to_parquet.

    Raises:
        ValueError: If the format is not writable.
    """
    ext = Path(path).suffix.lower()

    if ext not in WRITABLE_FORMATS:
        raise ValueError(
            f"Cannot write to '{ext}'. Writable formats: {sorted(WRITABLE_FORMATS)}"
        )

    # Ensure parent directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    # --- GeoParquet ---
    if ext == ".parquet":
        gdf.to_parquet(path, **kwargs)
    else:
        # --- Standard Fiona-based ---
        driver = VECTOR_DRIVERS[ext]
        write_kwargs: dict[str, Any] = {"driver": driver}
        if layer:
            write_kwargs["layer"] = layer
        if ext == ".gpkg":
            write_kwargs["mode"] = mode

        write_kwargs.update(kwargs)
        gdf.to_file(path, **write_kwargs)

    # Sidecars — best-effort, failures logged but not raised.
    if write_style_sidecars:
        try:
            from gispulse.persistence.style_sidecar import write_style_sidecars as _write_sidecars

            _write_sidecars(gdf, path, layer_name=layer)
        except Exception:
            pass




# ---------------------------------------------------------------------------
# Multi-layer read / write
# ---------------------------------------------------------------------------


def read_all_vectors(
    path: str,
    crs: str | None = None,
) -> dict[str, gpd.GeoDataFrame]:
    """Read all layers from a multi-layer file into a dict.

    For single-layer formats, returns a dict with one entry keyed by stem name.
    """
    import pyogrio

    ext = Path(path).suffix.lower()

    if ext in MULTI_LAYER_FORMATS:
        layer_info = pyogrio.list_layers(path)
        layer_names = [row[0] for row in layer_info]
    else:
        layer_names = [Path(path).stem]

    result: dict[str, gpd.GeoDataFrame] = {}
    for lname in layer_names:
        read_kwargs: dict[str, Any] = {}
        if ext in MULTI_LAYER_FORMATS:
            read_kwargs["layer"] = lname
        gdf = gpd.read_file(path, **read_kwargs)
        if crs and gdf.crs is None:
            gdf = gdf.set_crs(crs)
        result[lname] = gdf
    return result


def write_all_vectors(
    layers: dict[str, gpd.GeoDataFrame],
    path: str,
) -> None:
    """Write multiple layers to a file.

    For multi-layer formats (GPKG, SQLite), all layers go into one file.
    For single-layer formats, only the first layer is written.
    """
    ext = Path(path).suffix.lower()
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    if ext in MULTI_LAYER_FORMATS:
        driver = VECTOR_DRIVERS[ext]
        first = True
        for layer_name, gdf in layers.items():
            layer_kwargs: dict[str, Any] = {
                "driver": driver,
                "layer": layer_name,
            }
            if ext == ".gpkg":
                layer_kwargs["mode"] = "w" if first else "a"
            gdf.to_file(path, **layer_kwargs)
            first = False
    else:
        first_name = next(iter(layers))
        write_vector(layers[first_name], path)

# ---------------------------------------------------------------------------
# GeoParquet convenience functions
# ---------------------------------------------------------------------------


def write_geoparquet(
    gdf: gpd.GeoDataFrame,
    path: str,
    **kwargs: Any,
) -> None:
    """Write a GeoDataFrame to GeoParquet format.

    GeoParquet is the recommended format for intermediate results in
    GISPulse pipelines due to its columnar storage, fast I/O, and
    native geometry encoding (no WKB serialization overhead).

    Args:
        gdf:      GeoDataFrame to write.
        path:     Output ``.parquet`` file path.
        **kwargs: Extra arguments passed to ``gdf.to_parquet()``.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(path, **kwargs)


def read_geoparquet(
    path: str,
    bbox: Optional[tuple[float, float, float, float]] = None,
    **kwargs: Any,
) -> gpd.GeoDataFrame:
    """Read a GeoParquet file into a GeoDataFrame.

    Supports optional spatial filtering via bounding box. Uses the
    native ``geopandas.read_parquet()`` reader which leverages Arrow
    for columnar I/O and respects GeoParquet metadata (CRS, geometry
    encoding).

    Args:
        path:     Path to the ``.parquet`` file.
        bbox:     Optional spatial filter ``(minx, miny, maxx, maxy)``.
                  Applied as a post-read crop via ``cx[]`` indexer.
        **kwargs: Extra arguments passed to ``gpd.read_parquet()``.

    Returns:
        GeoDataFrame with the file contents.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not Path(path).exists():
        raise FileNotFoundError(f"GeoParquet file not found: {path}")

    gdf = gpd.read_parquet(path, **kwargs)
    if bbox:
        gdf = gdf.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
    return gdf


# ---------------------------------------------------------------------------
# Domain factory
# ---------------------------------------------------------------------------


def dataset_from_file(path: str) -> Dataset:
    """Create a Dataset domain object from any supported vector file.

    Inspects the file to populate metadata (layers, CRS, feature count,
    geometry type). Works for all supported formats. Uses pyogrio for
    fast metadata inspection (3-5x faster than fiona).

    Args:
        path: Absolute or relative path to the spatial file.

    Returns:
        Dataset with metadata populated.
    """
    import pyogrio

    path_obj = Path(path)
    ext = path_obj.suffix.lower()
    driver = VECTOR_DRIVERS.get(ext)

    if driver is None:
        raise ValueError(f"Unsupported format '{ext}'.")

    # Determine data category
    if ext in (".csv", ".tsv", ".xlsx"):
        data_cat = DataCategory.TABULAR_GEO.value
    else:
        data_cat = DataCategory.VECTOR.value

    fmt_label = driver or ext.upper().lstrip(".")

    dataset = Dataset(
        name=path_obj.stem,
        source_path=str(path_obj.resolve()),
        data_category=data_cat,
        format=fmt_label,
    )

    # --- GeoParquet: metadata via geopandas ---
    if ext == ".parquet":
        gdf = gpd.read_parquet(path)
        crs_str = str(gdf.crs) if gdf.crs else "EPSG:4326"
        geom_type = gdf.geometry.geom_type.unique().tolist() if not gdf.empty else []
        dataset.crs = crs_str
        dataset.metadata["layers"] = [
            {
                "id": str(uuid4()),
                "name": path_obj.stem,
                "geometry_type": geom_type[0] if geom_type else None,
                "feature_count": len(gdf),
                "crs": crs_str,
            }
        ]
        dataset.metadata["layer_count"] = 1
        return dataset

    # --- CSV/XLSX: no inspection, just basic metadata ---
    if ext in (".csv", ".tsv", ".xlsx"):
        dataset.metadata["layers"] = [
            {
                "id": str(uuid4()),
                "name": path_obj.stem,
                "geometry_type": "Point",
                "feature_count": None,
                "crs": "EPSG:4326",
            }
        ]
        dataset.metadata["layer_count"] = 1
        return dataset

    # --- Vector formats: pyogrio metadata inspection ---
    if ext in MULTI_LAYER_FORMATS:
        layer_info = pyogrio.list_layers(path)
        layer_names = [row[0] for row in layer_info]
    else:
        layer_names = [path_obj.stem]

    layers_meta: list[dict] = []
    for lname in layer_names:
        read_kwargs: dict[str, Any] = {}
        if ext in MULTI_LAYER_FORMATS:
            read_kwargs["layer"] = lname

        try:
            info = pyogrio.read_info(path, **read_kwargs)
            crs_str = "EPSG:4326"
            if info.get("crs"):
                from pyproj import CRS
                try:
                    epsg = CRS(info["crs"]).to_epsg()
                    if epsg:
                        crs_str = f"EPSG:{epsg}"
                except Exception:
                    crs_str = str(info["crs"])

            geom_type = info.get("geometry_type", None)
            feature_count = info.get("features", 0)
        except Exception:
            crs_str = "EPSG:4326"
            geom_type = None
            feature_count = 0

        layers_meta.append(
            {
                "id": str(uuid4()),
                "name": lname,
                "geometry_type": geom_type,
                "feature_count": feature_count,
                "crs": crs_str,
            }
        )

    if layers_meta:
        dataset.crs = layers_meta[0]["crs"]

    dataset.metadata["layers"] = layers_meta
    dataset.metadata["layer_count"] = len(layers_meta)

    # Extract styles from GPKG files
    if ext == ".gpkg":
        from gispulse.persistence.gpkg import extract_layer_styles, extract_full_style_defs

        styles = extract_layer_styles(path)
        if styles:
            dataset.metadata["styles"] = styles
            # Map styles to their layers
            style_map: dict[str, list[dict]] = {}
            for s in styles:
                lname = s.get("layer_name", "")
                style_map.setdefault(lname, []).append(s)
            for lmeta in layers_meta:
                layer_styles = style_map.get(lmeta["name"], [])
                if layer_styles:
                    lmeta["style"] = layer_styles[0]

        # Also extract full advanced style definitions
        try:
            full_defs = extract_full_style_defs(path)
            if full_defs:
                dataset.metadata["style_defs"] = full_defs
                for lmeta in layers_meta:
                    if lmeta["name"] in full_defs:
                        lmeta["style_def"] = full_defs[lmeta["name"]]
        except Exception:
            pass  # Non-critical: legacy color extraction still works

    return dataset
