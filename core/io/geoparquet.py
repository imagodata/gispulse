"""
GeoParquet I/O for GISPulse.

Provides read/write helpers for the GeoParquet format with two strategies:

- **Small files** (< ``DUCKDB_THRESHOLD`` rows): geopandas.read_parquet() — simple,
  zero-config, handles CRS and geometry encoding natively.
- **Large files** (>= threshold, or when DuckDB engine is active): DuckDB spatial
  extension — columnar pushdown, predicate filter, bbox scan.

Usage::

    from core.io.geoparquet import read_geoparquet, write_geoparquet

    gdf = read_geoparquet("parcels.parquet", bbox=(2.2, 48.8, 2.4, 48.9))
    write_geoparquet(gdf, "output.parquet", compression="zstd")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import geopandas as gpd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from core.config import settings as _cfg

#: Row threshold above which DuckDB is preferred over in-process pandas.
DUCKDB_THRESHOLD: int = _cfg.jobs.duckdb_threshold


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_geoparquet(
    path: str,
    bbox: Optional[tuple[float, float, float, float]] = None,
    columns: Optional[list[str]] = None,
    use_duckdb: Optional[bool] = None,
    crs: Optional[str] = None,
    **kwargs: Any,
) -> gpd.GeoDataFrame:
    """Read a GeoParquet file into a GeoDataFrame.

    Strategy selection:
    - ``use_duckdb=True``  -> always use DuckDB (requires duckdb + spatial extension).
    - ``use_duckdb=False`` -> always use geopandas.read_parquet().
    - ``use_duckdb=None``  -> auto: DuckDB if file has >= ``DUCKDB_THRESHOLD`` rows,
      else geopandas.

    Args:
        path:       Path to the .parquet file.
        bbox:       Optional spatial filter as (minx, miny, maxx, maxy) in the
                    file's native CRS.
        columns:    Column subset to read (projection pushdown). None = all.
        use_duckdb: Force DuckDB (True), force geopandas (False), or auto (None).
        crs:        Assign this CRS when the file has none.
        **kwargs:   Extra keyword arguments forwarded to the underlying reader.

    Returns:
        GeoDataFrame with a "geometry" column.

    Raises:
        FileNotFoundError: If the path does not exist.
        ValueError:        If DuckDB spatial extension is unavailable but required.
    """
    path_obj = Path(path)
    if not path_obj.exists():
        raise FileNotFoundError(f"GeoParquet file not found: {path}")

    # Auto-detect strategy based on row count
    if use_duckdb is None:
        use_duckdb = _should_use_duckdb(path)

    if use_duckdb:
        gdf = _read_via_duckdb(path, bbox=bbox, columns=columns, **kwargs)
    else:
        gdf = _read_via_geopandas(path, bbox=bbox, columns=columns, **kwargs)

    if crs and (gdf.crs is None):
        gdf = gdf.set_crs(crs)

    return gdf


def write_geoparquet(
    gdf: gpd.GeoDataFrame,
    path: str,
    compression: str = "snappy",
    geometry_encoding: str = "WKB",
    write_covering_bbox: bool = True,
    **kwargs: Any,
) -> None:
    """Write a GeoDataFrame to GeoParquet.

    Wraps ``gdf.to_parquet()`` with sensible defaults for spatial workloads:
    - snappy compression (good balance speed/ratio; override with ``compression``).
    - WKB geometry encoding (broadest compatibility).
    - Optional covering bbox column for range-scan pushdown.

    Args:
        gdf:                  GeoDataFrame to serialize.
        path:                 Output .parquet path (created if absent).
        compression:          Parquet compression codec ("snappy", "zstd", "gzip", "none").
        geometry_encoding:    "WKB" (default) or "geoarrow".
        write_covering_bbox:  Append bbox columns (xmin/ymin/xmax/ymax) for fast
                              spatial filtering by downstream tools.
        **kwargs:             Extra keyword arguments passed to ``gdf.to_parquet()``.

    Raises:
        ValueError: If gdf has no geometry column.
    """
    # Check for active geometry column without triggering GeoDataFrame.__getattr__
    geom_col = gdf._geometry_column_name
    if geom_col is None or geom_col not in gdf.columns:
        raise ValueError("GeoDataFrame has no active geometry column.")

    Path(path).parent.mkdir(parents=True, exist_ok=True)

    write_kwargs: dict[str, Any] = {
        "compression": compression,
        "geometry_encoding": geometry_encoding,
        **kwargs,
    }

    # write_covering_bbox is supported from geopandas >= 1.0 / pyarrow >= 14
    try:
        gdf.to_parquet(path, write_covering_bbox=write_covering_bbox, **write_kwargs)
    except TypeError:
        # Older geopandas/pyarrow — fall back without bbox column
        gdf.to_parquet(path, **write_kwargs)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _should_use_duckdb(path: str) -> bool:
    """Return True if the file is large enough to benefit from DuckDB."""
    try:
        import pyarrow.parquet as pq

        meta = pq.read_metadata(path)
        return meta.num_rows >= DUCKDB_THRESHOLD
    except Exception:
        return False


def _read_via_geopandas(
    path: str,
    bbox: Optional[tuple[float, float, float, float]] = None,
    columns: Optional[list[str]] = None,
    **kwargs: Any,
) -> gpd.GeoDataFrame:
    """Read with geopandas.read_parquet — simple path for small files."""
    gp_kwargs: dict[str, Any] = {**kwargs}
    if bbox is not None:
        gp_kwargs["bbox"] = bbox
    if columns is not None:
        gp_kwargs["columns"] = columns
    return gpd.read_parquet(path, **gp_kwargs)


def _read_via_duckdb(
    path: str,
    bbox: Optional[tuple[float, float, float, float]] = None,
    columns: Optional[list[str]] = None,
    **kwargs: Any,
) -> gpd.GeoDataFrame:
    """Read with DuckDB + spatial extension — efficient path for large files.

    Falls back to geopandas if DuckDB or its spatial extension is unavailable.
    """
    try:
        import duckdb
    except ImportError:
        return _read_via_geopandas(path, bbox=bbox, columns=columns, **kwargs)

    try:
        conn = duckdb.connect(":memory:")
        conn.execute("INSTALL spatial; LOAD spatial;")
        conn.execute("INSTALL parquet; LOAD parquet;")
    except Exception:
        return _read_via_geopandas(path, bbox=bbox, columns=columns, **kwargs)

    # Build SELECT clause
    if columns:
        # Always include geometry
        cols = list(columns)
        col_sql = ", ".join(f'"{c}"' for c in cols)
        select_clause = col_sql
    else:
        select_clause = "*"

    # Build WHERE clause for bbox spatial filter
    where_clause = ""
    if bbox is not None:
        minx, miny, maxx, maxy = bbox
        # Use DuckDB spatial to filter — assumes geometry column is named "geometry"
        where_clause = (
            f" WHERE ST_Intersects("
            f"ST_GeomFromWKB(geometry), "
            f"ST_MakeEnvelope({minx}, {miny}, {maxx}, {maxy}))"
        )

    sql = f"SELECT {select_clause} FROM read_parquet(?){where_clause}"

    try:
        result = conn.execute(sql, [path]).fetchdf()
        conn.close()
    except Exception:
        conn.close()
        return _read_via_geopandas(path, bbox=bbox, columns=columns, **kwargs)

    # Convert WKB geometry column back to Shapely geometries
    if "geometry" in result.columns:
        from shapely import wkb

        result["geometry"] = result["geometry"].apply(
            lambda v: wkb.loads(bytes(v)) if v is not None else None
        )
        gdf = gpd.GeoDataFrame(result, geometry="geometry")
        return gdf

    # No geometry column detected — return as-is (caller can re-add geometry)
    return gpd.GeoDataFrame(result)
