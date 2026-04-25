"""
STAC (SpatioTemporal Asset Catalog) client for GISPulse.

Supports querying any STAC-compliant catalog (Copernicus/Planetary Computer,
USGS, IGN Géoplateforme, etc.) to discover and download geospatial assets.

Two backends are tried in order:
1. ``pystac-client`` — feature-rich, handles paging and signing.
2. ``urllib.request`` — stdlib fallback with no extra dependency.

Pre-configured well-known catalogs are available via ``KNOWN_CATALOGS``.

Usage::

    from catalog.providers.stac_client import STACClient, KNOWN_CATALOGS

    client = STACClient(KNOWN_CATALOGS["planetary_computer"])
    items = client.search(
        bbox=[-1.5, 47.2, -1.3, 47.4],
        datetime="2023-06-01/2023-08-31",
        collections=["sentinel-2-l2a"],
        limit=5,
    )
    local_path = client.download_asset(items[0], "B04", "/tmp/stac_downloads/")
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pre-configured catalogs
# ---------------------------------------------------------------------------

#: Well-known STAC catalog root URLs.
KNOWN_CATALOGS: dict[str, str] = {
    "planetary_computer": "https://planetarycomputer.microsoft.com/api/stac/v1",
    "earth_search": "https://earth-search.aws.element84.com/v1",
    "usgs_landsat": "https://landsateuwest.blob.core.windows.net/landsat-c2",
    "ign_geoplateforme": "https://data.geopf.fr/stac",
}


# ---------------------------------------------------------------------------
# STACClient
# ---------------------------------------------------------------------------


class STACClient:
    """Lightweight STAC catalog client.

    Args:
        catalog_url: Root URL of the STAC catalog (e.g. ``KNOWN_CATALOGS["earth_search"]``).
        timeout:     HTTP request timeout in seconds.
    """

    def __init__(self, catalog_url: str, timeout: int = 30) -> None:
        self._url = catalog_url.rstrip("/")
        self._timeout = timeout
        self._pystac_available: Optional[bool] = None  # lazily resolved

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        bbox: list[float],
        datetime: str,
        collections: list[str],
        limit: int = 10,
        query: Optional[dict[str, Any]] = None,
    ) -> list[dict]:
        """Search the catalog for STAC items matching spatial and temporal criteria.

        Args:
            bbox:        Bounding box as [minx, miny, maxx, maxy] in EPSG:4326.
            datetime:    ISO 8601 date or interval, e.g. "2023-06-01" or
                         "2023-06-01/2023-08-31".
            collections: List of STAC collection IDs to search.
            limit:       Maximum number of items to return.
            query:       Optional CQL2 / STAC query extension dict for property
                         filtering (e.g. ``{"eo:cloud_cover": {"lt": 20}}``).

        Returns:
            List of STAC item dicts (GeoJSON Feature format).
        """
        if self._use_pystac():
            return self._search_pystac(bbox, datetime, collections, limit, query)
        return self._search_urllib(bbox, datetime, collections, limit, query)

    def download_asset(
        self,
        item: dict,
        asset_key: str,
        output_dir: str,
        overwrite: bool = False,
    ) -> str:
        """Download a specific asset from a STAC item to local storage.

        Args:
            item:       STAC item dict as returned by ``search()``.
            asset_key:  Key of the asset to download (e.g. "B04", "visual", "data").
            output_dir: Local directory where the file will be written.
            overwrite:  Re-download even if the file already exists.

        Returns:
            Absolute path to the downloaded file.

        Raises:
            KeyError:   If ``asset_key`` is not in the item's assets.
            OSError:    If the download fails.
        """
        assets: dict = item.get("assets", {})
        if asset_key not in assets:
            available = list(assets.keys())
            raise KeyError(
                f"Asset '{asset_key}' not found. Available: {available}"
            )

        href: str = assets[asset_key].get("href", "")
        if not href:
            raise KeyError(f"Asset '{asset_key}' has no 'href' field.")

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(href.split("?")[0]).name  # strip query string
        dest = out_dir / filename

        if dest.exists() and not overwrite:
            log.debug("Asset already present — skipping download: %s", dest)
            return str(dest)

        log.info("Downloading STAC asset %s -> %s", href, dest)
        try:
            urllib.request.urlretrieve(href, dest)
        except Exception as exc:
            raise OSError(f"Failed to download {href}: {exc}") from exc

        return str(dest)

    def list_collections(self) -> list[dict]:
        """List all collections available in the catalog.

        Returns:
            List of STAC collection summary dicts (id, title, description, ...).
        """
        url = f"{self._url}/collections"
        try:
            data = self._get_json(url)
        except Exception as exc:
            log.warning("Could not list collections from %s: %s", self._url, exc)
            return []

        raw: list[dict] = data.get("collections", data if isinstance(data, list) else [])
        return [
            {
                "id": c.get("id", ""),
                "title": c.get("title", c.get("id", "")),
                "description": c.get("description", ""),
                "extent": c.get("extent", {}),
                "links": c.get("links", []),
            }
            for c in raw
        ]

    # ------------------------------------------------------------------
    # Internal — pystac-client backend
    # ------------------------------------------------------------------

    def _use_pystac(self) -> bool:
        """Lazily check whether pystac-client is importable."""
        if self._pystac_available is None:
            try:
                import pystac_client  # noqa: F401

                self._pystac_available = True
            except ImportError:
                self._pystac_available = False
                log.debug(
                    "pystac-client not installed — using urllib fallback. "
                    "Install with: pip install pystac-client"
                )
        return bool(self._pystac_available)

    def _search_pystac(
        self,
        bbox: list[float],
        datetime: str,
        collections: list[str],
        limit: int,
        query: Optional[dict],
    ) -> list[dict]:
        import pystac_client

        catalog = pystac_client.Client.open(self._url)
        search_kwargs: dict[str, Any] = {
            "bbox": bbox,
            "datetime": datetime,
            "collections": collections,
            "max_items": limit,
        }
        if query:
            search_kwargs["query"] = query

        search = catalog.search(**search_kwargs)
        items = list(search.items())
        return [item.to_dict() for item in items]

    # ------------------------------------------------------------------
    # Internal — urllib fallback backend
    # ------------------------------------------------------------------

    def _search_urllib(
        self,
        bbox: list[float],
        datetime: str,
        collections: list[str],
        limit: int,
        query: Optional[dict],
    ) -> list[dict]:
        """POST to /search endpoint — STAC API spec."""
        url = f"{self._url}/search"
        payload: dict[str, Any] = {
            "bbox": bbox,
            "datetime": datetime,
            "collections": collections,
            "limit": limit,
        }
        if query:
            payload["query"] = query

        try:
            data = self._post_json(url, payload)
        except Exception as exc:
            log.error("STAC search failed (%s): %s", url, exc)
            return []

        features: list[dict] = data.get("features", [])
        return features

    def _get_json(self, url: str) -> dict:
        """HTTP GET and parse JSON response."""
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "GISPulse/1.0"},
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read().decode())

    def _post_json(self, url: str, payload: dict) -> dict:
        """HTTP POST JSON body and parse JSON response."""
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "GISPulse/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read().decode())
