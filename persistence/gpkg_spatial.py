"""
Spatial query engine for GeoPackage — RTree prefilter + Shapely refinement.

Two-phase approach (same pattern as PostGIS GiST + exact predicate):

1. **RTree prefilter** — SQLite query on the ``rtree_<table>_<geom>`` virtual
   table to narrow candidates by bounding box.  This is pure SQLite, no
   mod_spatialite needed.
2. **Shapely refinement** — exact spatial predicate (intersects, within,
   contains, etc.) applied in Python on the candidate GeoDataFrame.

GDAL/pyogrio automatically creates RTree triggers when writing layers to GPKG,
so every layer has a spatial index out of the box.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Literal

import geopandas as gpd
import numpy as np
from shapely import box
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)

# Supported spatial predicates
SpatialPredicate = Literal[
    "intersects",
    "within",
    "contains",
    "overlaps",
    "crosses",
    "touches",
    "disjoint",
]


# ---------------------------------------------------------------------------
# RTree bounding-box query
# ---------------------------------------------------------------------------


def _rtree_table_name(layer_name: str, geom_col: str = "geom") -> str:
    """Return the GDAL-generated RTree virtual table name."""
    return f"rtree_{layer_name}_{geom_col}"


def _detect_geom_column(conn: sqlite3.Connection, layer_name: str) -> str | None:
    """Detect the geometry column name from gpkg_geometry_columns."""
    try:
        row = conn.execute(
            "SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?",
            (layer_name,),
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def _rtree_exists(conn: sqlite3.Connection, rtree_name: str) -> bool:
    """Check if the RTree virtual table exists."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (rtree_name,),
    ).fetchone()
    return row is not None


def rtree_bbox_filter(
    conn: sqlite3.Connection,
    layer_name: str,
    bbox: tuple[float, float, float, float],
    geom_col: str | None = None,
) -> list[int]:
    """Query the GPKG RTree spatial index for candidate feature IDs.

    Args:
        conn:       Open SQLite connection to the GPKG.
        layer_name: Spatial table name.
        bbox:       (minx, miny, maxx, maxy) bounding box.
        geom_col:   Geometry column name (auto-detected if None).

    Returns:
        List of ``fid`` values whose envelopes intersect the bbox.
    """
    if geom_col is None:
        geom_col = _detect_geom_column(conn, layer_name) or "geom"

    rtree_name = _rtree_table_name(layer_name, geom_col)

    if not _rtree_exists(conn, rtree_name):
        logger.warning("rtree_missing: %s — falling back to full scan", rtree_name)
        return []

    minx, miny, maxx, maxy = bbox
    rows = conn.execute(
        f"""
        SELECT id FROM "{rtree_name}"
        WHERE minx <= ? AND maxx >= ?
          AND miny <= ? AND maxy >= ?
        """,
        (maxx, minx, maxy, miny),
    ).fetchall()

    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Shapely exact predicates
# ---------------------------------------------------------------------------

_PREDICATE_MAP = {
    "intersects": lambda gdf, geom: gdf.intersects(geom),
    "within": lambda gdf, geom: gdf.within(geom),
    "contains": lambda gdf, geom: gdf.contains(geom),
    "overlaps": lambda gdf, geom: gdf.overlaps(geom),
    "crosses": lambda gdf, geom: gdf.crosses(geom),
    "touches": lambda gdf, geom: gdf.touches(geom),
    "disjoint": lambda gdf, geom: gdf.disjoint(geom),
}


def spatial_filter(
    gdf: gpd.GeoDataFrame,
    query_geom: BaseGeometry,
    predicate: SpatialPredicate = "intersects",
) -> gpd.GeoDataFrame:
    """Apply an exact spatial predicate on a GeoDataFrame.

    Uses Shapely 2.0+ vectorised operations (GEOS STRtree under the hood).

    Args:
        gdf:        Input GeoDataFrame.
        query_geom: Geometry to test against.
        predicate:  Spatial predicate name.

    Returns:
        Filtered GeoDataFrame.
    """
    pred_fn = _PREDICATE_MAP.get(predicate)
    if pred_fn is None:
        raise ValueError(
            f"Unknown predicate: {predicate!r}. "
            f"Available: {sorted(_PREDICATE_MAP.keys())}"
        )
    mask = pred_fn(gdf, query_geom)
    return gdf.loc[mask].copy()


# ---------------------------------------------------------------------------
# Combined: RTree + Shapely (two-phase)
# ---------------------------------------------------------------------------


def spatial_query(
    conn: sqlite3.Connection,
    gdf: gpd.GeoDataFrame,
    layer_name: str,
    query_geom: BaseGeometry,
    predicate: SpatialPredicate = "intersects",
    geom_col: str | None = None,
) -> gpd.GeoDataFrame:
    """Two-phase spatial query: RTree prefilter + Shapely refinement.

    Args:
        conn:       Open SQLite connection to the GPKG.
        gdf:        Full GeoDataFrame for the layer (or preloaded subset).
        layer_name: Table name in the GPKG (for RTree lookup).
        query_geom: Query geometry.
        predicate:  Spatial predicate.
        geom_col:   Geometry column name (auto-detected if None).

    Returns:
        Filtered GeoDataFrame matching the spatial predicate.
    """
    bbox = query_geom.bounds  # (minx, miny, maxx, maxy)

    # Phase 1: RTree prefilter
    candidate_fids = rtree_bbox_filter(conn, layer_name, bbox, geom_col)

    if candidate_fids:
        # Filter GeoDataFrame to candidates (fid is usually the index or a column)
        if "fid" in gdf.columns:
            candidates = gdf[gdf["fid"].isin(candidate_fids)]
        elif gdf.index.name == "fid":
            candidates = gdf.loc[gdf.index.isin(candidate_fids)]
        else:
            # Try matching by positional index (fid is 1-based in GPKG)
            idx = [f - 1 for f in candidate_fids if 0 < f <= len(gdf)]
            candidates = gdf.iloc[idx] if idx else gdf
    else:
        # No RTree available — full scan
        candidates = gdf

    if candidates.empty:
        return candidates

    # Phase 2: Shapely exact predicate
    return spatial_filter(candidates, query_geom, predicate)


def bbox_filter_gdf(
    conn: sqlite3.Connection,
    gdf: gpd.GeoDataFrame,
    layer_name: str,
    bbox: tuple[float, float, float, float],
    geom_col: str | None = None,
) -> gpd.GeoDataFrame:
    """Fast bounding-box filter using only the RTree index.

    Args:
        conn:       Open SQLite connection.
        gdf:        Full GeoDataFrame.
        layer_name: Table name for RTree lookup.
        bbox:       (minx, miny, maxx, maxy).
        geom_col:   Geometry column name.

    Returns:
        Subset of gdf within the bounding box.
    """
    query_geom = box(*bbox)
    return spatial_query(conn, gdf, layer_name, query_geom, "intersects", geom_col)
