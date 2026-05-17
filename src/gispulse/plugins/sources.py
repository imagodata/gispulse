"""Catalog and source-client primitives supported for plugin authors.

Lightweight catalog helpers (``CatalogEntry``, ``FluxEntry``,
``OGCSourceConfig``, ``get_catalog_entry``, ``get_flux_entry``) are
importable immediately.  Heavy source-client objects (``ApiCartoGeoJsonClient``
and ``fetch_wfs``) are deferred inside accessor functions so that importing
this module does NOT pull in ``requests`` or the apicarto adapter when
plugins don't need them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gispulse.catalog import registry
from gispulse.catalog.models import CatalogEntry, FluxEntry
from gispulse.core.models import OGCSourceConfig

if TYPE_CHECKING:
    from gispulse.adapters.apicarto import ApiCartoGeoJsonClient
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


def __getattr__(name: str) -> object:
    """Lazy-load heavy source-client symbols on first access."""
    if name == "ApiCartoGeoJsonClient":
        from gispulse.adapters.apicarto import ApiCartoGeoJsonClient
        globals()["ApiCartoGeoJsonClient"] = ApiCartoGeoJsonClient
        return ApiCartoGeoJsonClient
    if name == "fetch_wfs":
        from gispulse.adapters.ogc.wfs_client import fetch_wfs
        globals()["fetch_wfs"] = fetch_wfs
        return fetch_wfs
    raise AttributeError(f"module 'gispulse.plugins.sources' has no attribute {name!r}")


__all__ = [
    "ApiCartoGeoJsonClient",
    "CatalogEntry",
    "FluxEntry",
    "OGCSourceConfig",
    "fetch_wfs",
    "get_catalog_entry",
    "get_flux_entry",
]
