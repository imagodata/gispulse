"""Static EPSG projections catalog — bundled common CRS definitions."""

from __future__ import annotations

import json
from pathlib import Path

from catalog.models import CatalogDomain, ProjectionEntry
from catalog.providers.base import CatalogProvider
from catalog.registry import register_provider

_DATA = Path(__file__).parent.parent / "data" / "epsg_common.json"


class ProjectionsProvider(CatalogProvider):
    name = "epsg"
    domain = CatalogDomain.PROJECTION
    description = "Common EPSG coordinate reference systems"

    def __init__(self) -> None:
        raw = json.loads(_DATA.read_text(encoding="utf-8"))
        self._entries: dict[str, ProjectionEntry] = {}
        for item in raw:
            entry_id = f"projection:epsg:{item['epsg_code']}"
            self._entries[entry_id] = ProjectionEntry(
                id=entry_id,
                domain=CatalogDomain.PROJECTION,
                provider="epsg",
                name=item["name"],
                description=item.get("description", ""),
                tags=item.get("tags", []),
                epsg_code=item["epsg_code"],
                bounds=item.get("bounds"),
                area_of_use=item.get("area_of_use", ""),
                unit=item.get("unit", "metre"),
            )

    def list_entries(self, search=None, tags=None, limit=50, offset=0):
        entries = list(self._entries.values())
        if search:
            q = search.lower()
            entries = [
                e
                for e in entries
                if q in e.name.lower()
                or q in str(e.epsg_code)
                or q in e.area_of_use.lower()
                or q in e.description.lower()
                or any(q in t for t in e.tags)
            ]
        if tags:
            entries = [e for e in entries if any(t in e.tags for t in tags)]
        return entries[offset : offset + limit]

    def get_entry(self, entry_id):
        return self._entries.get(entry_id)


register_provider(ProjectionsProvider())
