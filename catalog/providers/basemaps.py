"""Static basemaps catalog — bundled tile providers."""

from __future__ import annotations

import json
from pathlib import Path

from catalog.models import BasemapEntry, CatalogDomain, FluxProtocol
from catalog.providers.base import CatalogProvider
from catalog.registry import register_provider

_DATA = Path(__file__).parent.parent / "data" / "basemaps.json"


class BasemapsProvider(CatalogProvider):
    name = "basemaps"
    domain = CatalogDomain.BASEMAP
    description = "Tile basemap providers (OSM, IGN, CARTO, Stamen, ...)"

    def __init__(self) -> None:
        raw = json.loads(_DATA.read_text(encoding="utf-8"))
        self._entries: dict[str, BasemapEntry] = {}
        for item in raw:
            entry_id = f"basemap:{item['id']}"
            self._entries[entry_id] = BasemapEntry(
                id=entry_id,
                domain=CatalogDomain.BASEMAP,
                provider="basemaps",
                name=item["name"],
                description=item.get("description", ""),
                tags=item.get("tags", []),
                url_template=item.get("url_template", ""),
                protocol=FluxProtocol(item.get("protocol", "xyz")),
                attribution=item.get("attribution", ""),
                max_zoom=item.get("max_zoom", 19),
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


register_provider(BasemapsProvider())
