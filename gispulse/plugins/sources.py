"""Catalog and source-client primitives supported for plugin authors."""

from __future__ import annotations

from catalog import registry
from catalog.models import CatalogEntry, FluxEntry
from core.models import OGCSourceConfig
from gispulse.adapters.ogc.wfs_client import fetch_wfs


def get_catalog_entry(entry_id: str) -> CatalogEntry | None:
    """Return a catalog entry by id through the host catalog registry."""
    return registry.get_entry(entry_id)


def get_flux_entry(entry_id: str) -> FluxEntry | None:
    """Return a flux catalog entry by id, or None when the entry is absent or another type."""
    entry = get_catalog_entry(entry_id)
    if isinstance(entry, FluxEntry):
        return entry
    return None


__all__ = [
    "CatalogEntry",
    "FluxEntry",
    "OGCSourceConfig",
    "fetch_wfs",
    "get_catalog_entry",
    "get_flux_entry",
]
