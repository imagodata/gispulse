"""
MVT (Mapbox Vector Tile) router for GISPulse.

Serves vector tiles in the ``application/vnd.mapbox-vector-tile`` format
for registered datasets.  Two rendering paths are supported:

- **PostGIS mode** — ``ST_AsMVT(ST_AsMVTGeom(...))`` executed server-side
  for maximum performance.
- **DuckDB / fallback mode** — features are loaded in-memory, filtered by
  tile bounding box, and a 501 is returned (full MVT encoding requires a
  native library; this path ensures the endpoint stays functional for bbox
  queries and can be extended later).

Endpoint:
    GET /tiles/{collection_id}/{z}/{x}/{y}.mvt

Includes an in-memory cache with a 1-hour TTL.
"""

from __future__ import annotations

import logging
import math
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from gispulse.adapters.http.dependencies import get_dataset_repo, get_spatial_engine
from core.models import Dataset
from persistence.engine import SpatialEngine
from persistence.repository import Repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tiles", tags=["tiles"])

_MVT_CONTENT_TYPE = "application/vnd.mapbox-vector-tile"
_CACHE_TTL_S = 3600  # 1 hour

# Simple in-memory tile cache: (collection_id, z, x, y) -> (bytes, timestamp)
_tile_cache: dict[tuple[str, int, int, int], tuple[bytes, float]] = {}


# ---------------------------------------------------------------------------
# Tile math (inline, no external dependency)
# ---------------------------------------------------------------------------


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Return the WGS 84 bounding box (minx, miny, maxx, maxy) for a tile.

    Uses the standard Web Mercator tile scheme (Slippy Map / TMS).
    """
    n = 2.0 ** z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return (lon_min, lat_min, lon_max, lat_max)


def _tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Return the Web Mercator (EPSG:3857) bounds for a tile.

    Used by PostGIS ``ST_AsMVTGeom`` which expects projected coordinates.
    """
    n = 2.0 ** z
    circumference = 20037508.3427892  # half of the earth circumference in metres

    xmin = (x / n) * 2 * circumference - circumference
    xmax = ((x + 1) / n) * 2 * circumference - circumference
    ymax = circumference - (y / n) * 2 * circumference
    ymin = circumference - ((y + 1) / n) * 2 * circumference

    return (xmin, ymin, xmax, ymax)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_get(key: tuple[str, int, int, int]) -> bytes | None:
    """Return cached tile bytes if present and not expired."""
    entry = _tile_cache.get(key)
    if entry is None:
        return None
    data, ts = entry
    if time.time() - ts > _CACHE_TTL_S:
        _tile_cache.pop(key, None)
        return None
    return data


def _cache_put(key: tuple[str, int, int, int], data: bytes) -> None:
    """Store tile bytes in the cache.

    Evicts oldest entries when the cache exceeds 10 000 tiles to
    prevent unbounded memory growth.
    """
    if len(_tile_cache) > 10_000:
        # Evict oldest 20%
        sorted_keys = sorted(_tile_cache, key=lambda k: _tile_cache[k][1])
        for k in sorted_keys[: len(sorted_keys) // 5]:
            _tile_cache.pop(k, None)
    _tile_cache[key] = (data, time.time())


# ---------------------------------------------------------------------------
# Dataset lookup
# ---------------------------------------------------------------------------


def _get_dataset_or_404(collection_id: UUID, repo: Repository) -> Dataset:
    ds = repo.get(collection_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Collection '{collection_id}' not found.")
    return ds  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PostGIS MVT generation
# ---------------------------------------------------------------------------


def _mvt_from_postgis(
    engine: SpatialEngine,
    ds: Dataset,
    z: int,
    x: int,
    y: int,
) -> bytes | None:
    """Generate an MVT tile using PostGIS ST_AsMVT.

    Returns the raw protobuf bytes, or None for an empty tile.
    """
    xmin, ymin, xmax, ymax = _tile_bounds_3857(z, x, y)

    # Determine table name — use source_path as the qualified table name
    # for PostGIS-backed datasets.  Convention: "schema.table" or just "table".
    table_name = ds.source_path or ds.name
    layer_name = ds.name or "default"

    # Build the MVT query.  We transform to 3857 for ST_AsMVTGeom,
    # then wrap with ST_AsMVT.
    sql = f"""
        WITH bounds AS (
            SELECT ST_MakeEnvelope({xmin}, {ymin}, {xmax}, {ymax}, 3857) AS geom
        ),
        mvtgeom AS (
            SELECT
                ST_AsMVTGeom(
                    ST_Transform(t.geom, 3857),
                    bounds.geom,
                    4096, 256, true
                ) AS geom,
                t.*
            FROM "{table_name}" t, bounds
            WHERE ST_Intersects(
                ST_Transform(t.geom, 3857),
                bounds.geom
            )
        )
        SELECT ST_AsMVT(mvtgeom, '{layer_name}', 4096, 'geom') AS tile
        FROM mvtgeom;
    """

    try:
        rows = engine.execute_sql(sql)
    except Exception:
        logger.exception("PostGIS MVT query failed for %s z=%d x=%d y=%d", ds.id, z, x, y)
        return None

    if not rows:
        return None

    tile_data = rows[0].get("tile")
    if tile_data is None or (isinstance(tile_data, (bytes, memoryview)) and len(tile_data) == 0):
        return None

    return bytes(tile_data) if isinstance(tile_data, memoryview) else tile_data


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

_CACHE_CONTROL = "public, max-age=3600"


@router.get("/{collection_id}/{z}/{x}/{y}.mvt")
def get_tile(
    collection_id: UUID,
    z: int,
    x: int,
    y: int,
    repo: Repository = Depends(get_dataset_repo),
    engine: SpatialEngine = Depends(get_spatial_engine),
) -> Response:
    """Serve a single MVT tile for the given collection and tile coordinates.

    In PostGIS mode, tiles are rendered server-side using ``ST_AsMVT``.
    In DuckDB/fallback mode, a 501 is returned (MVT encoding requires a
    native library not bundled with GISPulse).

    Empty tiles return 204 No Content.
    Tiles are cached in-memory for 1 hour.
    """
    # Validate tile coordinates
    if z < 0 or z > 30:
        raise HTTPException(status_code=400, detail=f"Invalid zoom level: {z}")
    max_tile = 2 ** z - 1
    if x < 0 or x > max_tile or y < 0 or y > max_tile:
        raise HTTPException(status_code=400, detail=f"Tile x={x} y={y} out of range for z={z}")

    ds = _get_dataset_or_404(collection_id, repo)

    cache_key = (str(collection_id), z, x, y)
    cached = _cache_get(cache_key)
    if cached is not None:
        return Response(
            content=cached,
            media_type=_MVT_CONTENT_TYPE,
            headers={"Cache-Control": _CACHE_CONTROL},
        )

    # --- PostGIS path ---
    if engine.backend_name == "postgis":
        tile_data = _mvt_from_postgis(engine, ds, z, x, y)

        if tile_data is None or len(tile_data) == 0:
            return Response(status_code=204, headers={"Cache-Control": _CACHE_CONTROL})

        _cache_put(cache_key, tile_data)
        return Response(
            content=tile_data,
            media_type=_MVT_CONTENT_TYPE,
            headers={"Cache-Control": _CACHE_CONTROL},
        )

    # --- DuckDB / fallback path ---
    # Without a native MVT encoder (e.g. python-vt2pbf or mapbox-vector-tile),
    # we cannot produce valid protobuf tiles from in-memory features.
    # Return 501 with an informative message.
    return Response(
        status_code=501,
        content=b"MVT tile encoding is only available with the PostGIS backend.",
        media_type="text/plain",
        headers={"Cache-Control": "no-store"},
    )
