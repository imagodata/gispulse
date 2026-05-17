"""Generic client for API Carto GeoJSON endpoints."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from gispulse.catalog import registry
from gispulse.catalog.models import OpenDataEntry

JsonMapping = Mapping[str, Any]
GetJson = Callable[[str, float], JsonMapping]


class ApiCartoGeoJsonClient:
    """Load API Carto catalog entries that accept a GeoJSON geometry query."""

    def __init__(self, *, get_json: GetJson | None = None, timeout: float = 20.0) -> None:
        self._get_json = get_json or _get_json
        self._timeout = timeout

    def fetch_geojson_for_geometry(
        self,
        catalog_entry_id: str,
        geometry: Mapping[str, Any],
        *,
        limit: int = 1000,
    ) -> JsonMapping:
        entry = registry.get_entry(catalog_entry_id)
        if not isinstance(entry, OpenDataEntry):
            raise ValueError(f"Catalog entry is not an open-data source: {catalog_entry_id}")
        if entry.format != "geojson":
            raise ValueError(f"Catalog entry is not a GeoJSON source: {catalog_entry_id}")

        query_param = entry.metadata.get("query_param")
        if not isinstance(query_param, str) or not query_param:
            raise ValueError(f"Catalog entry has no geometry query parameter: {catalog_entry_id}")

        geom = json.dumps(geometry, separators=(",", ":"))
        query = urlencode({query_param: geom, "_limit": limit})
        url = f"{entry.source_url}?{query}"
        payload = self._get_json(url, self._timeout)
        if payload.get("type") != "FeatureCollection":
            raise ValueError(f"API Carto response is not a FeatureCollection: {catalog_entry_id}")
        return payload


def _get_json(url: str, timeout: float) -> JsonMapping:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
