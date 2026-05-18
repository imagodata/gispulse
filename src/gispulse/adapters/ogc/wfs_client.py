"""WFS and OGC API Features clients for GISPulse.

Each client handles pagination transparently and returns a single
consolidated ``GeoDataFrame``.  An optional GeoParquet disk cache
with TTL avoids redundant network round-trips.
"""

from __future__ import annotations

import hashlib
import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import requests

from gispulse.core.models import OGCSourceConfig
from gispulse.adapters.ogc.auth import build_auth_headers

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 1000
DEFAULT_CACHE_TTL = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_key(cfg: OGCSourceConfig, bbox: tuple[float, ...] | None, extra: str = "") -> str:
    """Deterministic hash for a request configuration."""
    raw = f"{cfg.url}|{cfg.layer_name}|{cfg.version}|{cfg.crs}|{bbox}|{extra}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _try_read_cache(
    cache_dir: Path | str | None,
    key: str,
    ttl: int = DEFAULT_CACHE_TTL,
) -> gpd.GeoDataFrame | None:
    """Return cached GeoParquet if it exists and is fresh, else ``None``."""
    if cache_dir is None:
        return None
    path = Path(cache_dir) / f"{key}.parquet"
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ttl:
        logger.debug("Cache expired for %s (age=%.0fs, ttl=%ds)", key, age, ttl)
        path.unlink(missing_ok=True)
        return None
    logger.debug("Cache hit for %s", key)
    return gpd.read_parquet(path)


def _write_cache(
    cache_dir: Path | str | None,
    key: str,
    gdf: gpd.GeoDataFrame,
) -> None:
    """Persist *gdf* as GeoParquet in the cache directory."""
    if cache_dir is None or gdf.empty:
        return
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(directory / f"{key}.parquet")


# ---------------------------------------------------------------------------
# WFS client (1.1 / 2.0)
# ---------------------------------------------------------------------------


def fetch_wfs(
    cfg: OGCSourceConfig,
    bbox: tuple[float, float, float, float] | None = None,
    cql_filter: str | None = None,
    cache_dir: Path | str | None = None,
    cache_ttl: int = DEFAULT_CACHE_TTL,
) -> gpd.GeoDataFrame:
    """Fetch features from a WFS endpoint with automatic pagination.

    Parameters
    ----------
    cfg:
        OGC source configuration (url, layer_name, version, auth, ...).
    bbox:
        Optional bounding box filter ``(minx, miny, maxx, maxy)``.
    cql_filter:
        Optional CQL filter string (GeoServer / vendor param).
    cache_dir:
        Directory for GeoParquet cache.  ``None`` disables caching.
    cache_ttl:
        Cache time-to-live in seconds (default 3600).

    Returns
    -------
    GeoDataFrame with all fetched features.
    """
    key = _cache_key(cfg, bbox, extra=cql_filter or "")
    cached = _try_read_cache(cache_dir, key, ttl=cache_ttl)
    if cached is not None:
        return cached

    headers = build_auth_headers(cfg.auth)
    page_size = cfg.max_features or DEFAULT_PAGE_SIZE
    version = cfg.version or "2.0.0"
    is_v2 = version.startswith("2.")

    frames: list[gpd.GeoDataFrame] = []
    start_index = 0

    while True:
        params: dict[str, Any] = {
            "service": "WFS",
            "version": version,
            "request": "GetFeature",
            "typeNames" if is_v2 else "typeName": cfg.layer_name,
            "outputFormat": "application/json",
            "srsName": cfg.crs,
            "startIndex": start_index,
        }

        if is_v2:
            params["count"] = page_size
        else:
            params["maxFeatures"] = page_size

        if bbox is not None:
            params["bbox"] = ",".join(str(c) for c in bbox) + f",{cfg.crs}"

        if cql_filter:
            params["CQL_FILTER"] = cql_filter

        # Merge any user-supplied extra params
        params.update(cfg.params)

        logger.debug("WFS request startIndex=%d page_size=%d", start_index, page_size)
        resp = requests.get(cfg.url, params=params, headers=headers, timeout=120)
        resp.raise_for_status()

        gdf = gpd.read_file(BytesIO(resp.content))
        if gdf.empty:
            break

        frames.append(gdf)

        # If we got fewer features than requested, we've reached the end
        if len(gdf) < page_size:
            break

        start_index += len(gdf)

    if not frames:
        result = gpd.GeoDataFrame()
    else:
        result = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True))

    _write_cache(cache_dir, key, result)
    logger.info(
        "WFS fetch complete: %d features from %s/%s",
        len(result),
        cfg.url,
        cfg.layer_name,
    )
    return result


# ---------------------------------------------------------------------------
# OGC API Features client
# ---------------------------------------------------------------------------


def fetch_ogc_api_features(
    cfg: OGCSourceConfig,
    bbox: tuple[float, float, float, float] | None = None,
    cache_dir: Path | str | None = None,
    cache_ttl: int = DEFAULT_CACHE_TTL,
) -> gpd.GeoDataFrame:
    """Fetch features from an OGC API Features endpoint.

    Follows the ``next`` link in the response for automatic pagination.

    Parameters
    ----------
    cfg:
        OGC source configuration.
    bbox:
        Optional bounding box filter ``(minx, miny, maxx, maxy)``.
    cache_dir:
        Directory for GeoParquet cache.  ``None`` disables caching.
    cache_ttl:
        Cache time-to-live in seconds (default 3600).

    Returns
    -------
    GeoDataFrame with all fetched features.
    """
    key = _cache_key(cfg, bbox, extra="ogcapi")
    cached = _try_read_cache(cache_dir, key, ttl=cache_ttl)
    if cached is not None:
        return cached

    headers = build_auth_headers(cfg.auth)
    headers.setdefault("Accept", "application/geo+json")

    page_size = cfg.max_features or DEFAULT_PAGE_SIZE
    base_url = cfg.url.rstrip("/")
    url: str | None = f"{base_url}/collections/{cfg.layer_name}/items"

    params: dict[str, Any] = {"limit": page_size}
    if bbox is not None:
        params["bbox"] = ",".join(str(c) for c in bbox)
    params.update(cfg.params)

    frames: list[gpd.GeoDataFrame] = []

    while url is not None:
        logger.debug("OGC API Features request: %s", url)
        resp = requests.get(url, params=params, headers=headers, timeout=120)
        resp.raise_for_status()

        data = resp.json()
        # Parse features from the GeoJSON FeatureCollection
        if data.get("features"):
            gdf = gpd.GeoDataFrame.from_features(data["features"], crs=cfg.crs)
            frames.append(gdf)

        # Follow pagination via 'next' link
        url = None
        params = {}  # params are encoded in the 'next' URL
        for link in data.get("links", []):
            if link.get("rel") == "next":
                url = link["href"]
                break

    if not frames:
        result = gpd.GeoDataFrame()
    else:
        result = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True))

    _write_cache(cache_dir, key, result)
    logger.info(
        "OGC API Features fetch complete: %d features from %s/%s",
        len(result),
        cfg.url,
        cfg.layer_name,
    )
    return result
