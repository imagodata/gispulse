"""OSM-based tile and Overpass services."""

from __future__ import annotations

from gispulse.catalog.models import CatalogDomain, FluxEntry, FluxProtocol
from gispulse.catalog.providers.base import CatalogProvider
from gispulse.catalog.registry import register_provider

_OSM_FLUX: list[dict] = [
    {
        "id": "osm-tiles",
        "name": "OSM Standard Tiles",
        "description": "Tuiles raster standard OpenStreetMap",
        "service_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "protocol": "xyz",
        "tags": ["osm", "tiles", "global"],
    },
    {
        "id": "osm-fr-tiles",
        "name": "OSM France Tiles",
        "description": "Rendu OSM adapté pour la France",
        "service_url": "https://tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
        "protocol": "xyz",
        "tags": ["osm", "tiles", "france"],
    },
    {
        "id": "osm-overpass",
        "name": "Overpass API",
        "description": "Requêtes sur les données OSM brutes (bâtiments, POI, routes...)",
        "service_url": "https://overpass-api.de/api/interpreter",
        "protocol": "wfs",
        "tags": ["osm", "overpass", "query", "vector", "global"],
    },
    {
        "id": "osm-nominatim",
        "name": "Nominatim Geocoding",
        "description": "Géocodage et géocodage inverse OpenStreetMap",
        "service_url": "https://nominatim.openstreetmap.org/search",
        "protocol": "wfs",
        "tags": ["osm", "geocoding", "global"],
    },
]


class OSMFluxProvider(CatalogProvider):
    name = "osm"
    domain = CatalogDomain.FLUX
    description = "OpenStreetMap tile services, Overpass API, Nominatim"

    def __init__(self) -> None:
        self._entries: dict[str, FluxEntry] = {}
        for item in _OSM_FLUX:
            entry_id = f"flux:osm:{item['id']}"
            self._entries[entry_id] = FluxEntry(
                id=entry_id,
                domain=CatalogDomain.FLUX,
                provider="osm",
                name=item["name"],
                description=item.get("description", ""),
                tags=item.get("tags", []),
                service_url=item["service_url"],
                protocol=FluxProtocol(item["protocol"]),
                attribution="&copy; OpenStreetMap contributors",
                default_crs="EPSG:4326",
            )

    def list_entries(self, search=None, tags=None, limit=50, offset=0):
        entries = list(self._entries.values())
        if search:
            q = search.lower()
            entries = [
                e
                for e in entries
                if q in e.name.lower()
                or q in e.description.lower()
                or any(q in t for t in e.tags)
            ]
        if tags:
            entries = [e for e in entries if any(t in e.tags for t in tags)]
        return entries[offset : offset + limit]

    def get_entry(self, entry_id):
        return self._entries.get(entry_id)


register_provider(OSMFluxProvider())
