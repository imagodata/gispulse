"""data.gouv.fr open data provider — remote API search."""

from __future__ import annotations

import logging

from catalog.models import CatalogDomain, OpenDataEntry
from catalog.providers.base import CatalogProvider
from catalog.registry import register_provider

log = logging.getLogger(__name__)

_API = "https://www.data.gouv.fr/api/1"
_GEO_FORMATS = {"geojson", "shp", "gpkg", "csv", "geoparquet", "fgb", "gml"}


class DataGouvProvider(CatalogProvider):
    name = "datagouv"
    domain = CatalogDomain.OPENDATA
    description = "data.gouv.fr — portail open data français"

    def list_entries(self, search=None, tags=None, limit=50, offset=0):
        try:
            import httpx
        except ImportError:
            log.warning("httpx not installed — data.gouv.fr provider disabled")
            return []

        params: dict = {"page_size": min(limit, 50), "page": (offset // max(limit, 1)) + 1}
        if search:
            params["q"] = search
        else:
            params["q"] = "géographie"
        if tags:
            params["tag"] = tags[0]

        try:
            resp = httpx.get(f"{_API}/datasets/", params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return [self._to_entry(d) for d in data.get("data", [])]
        except Exception as exc:
            log.warning("datagouv_search_failed: %s", str(exc))
            return []

    def get_entry(self, entry_id):
        if not entry_id.startswith("opendata:datagouv:"):
            return None
        try:
            import httpx
        except ImportError:
            return None

        slug = entry_id.removeprefix("opendata:datagouv:")
        try:
            resp = httpx.get(f"{_API}/datasets/{slug}/", timeout=10)
            if resp.status_code == 200:
                return self._to_entry(resp.json())
        except Exception:
            pass
        return None

    def count(self, search=None, tags=None) -> int:
        # Avoid fetching all results for count
        return 0

    @staticmethod
    def _to_entry(raw: dict) -> OpenDataEntry:
        resources = raw.get("resources", [])
        geo_resource = next(
            (
                r
                for r in resources
                if r.get("format", "").lower() in _GEO_FORMATS
            ),
            None,
        )
        return OpenDataEntry(
            id=f"opendata:datagouv:{raw.get('slug', raw.get('id', ''))}",
            domain=CatalogDomain.OPENDATA,
            provider="datagouv",
            name=raw.get("title", ""),
            description=(raw.get("description", "") or "")[:300],
            tags=[t.lower() for t in raw.get("tags", []) if t],
            source_url=raw.get("page", ""),
            format=geo_resource["format"] if geo_resource else "",
            license=raw.get("license", ""),
            download_url=geo_resource["url"] if geo_resource else None,
            update_frequency=raw.get("frequency", ""),
            spatial_coverage=(raw.get("spatial") or {}).get("zones", [""])[0]
            if (raw.get("spatial") or {}).get("zones")
            else "",
        )


register_provider(DataGouvProvider())
