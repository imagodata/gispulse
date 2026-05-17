"""Provider registry and unified search for the GIS catalog."""

from __future__ import annotations

from gispulse.catalog.models import CatalogDomain, CatalogEntry
from gispulse.catalog.providers.base import CatalogProvider

PROVIDERS: dict[str, CatalogProvider] = {}


def register_provider(provider: CatalogProvider) -> CatalogProvider:
    """Register a catalog provider globally."""
    key = f"{provider.domain.value}:{provider.name}"
    PROVIDERS[key] = provider
    return provider


def search(
    domain: CatalogDomain | None = None,
    search: str | None = None,
    tags: list[str] | None = None,
    provider: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[CatalogEntry]:
    """Unified search across all providers (or filtered by domain/provider)."""
    results: list[CatalogEntry] = []
    for _key, prov in PROVIDERS.items():
        if domain and prov.domain != domain:
            continue
        if provider and prov.name != provider:
            continue
        results.extend(prov.list_entries(search=search, tags=tags, limit=9999))
    results.sort(key=lambda e: e.name)
    return results[offset : offset + limit]


def get_entry(entry_id: str) -> CatalogEntry | None:
    """Lookup by full ID."""
    for prov in PROVIDERS.values():
        entry = prov.get_entry(entry_id)
        if entry:
            return entry
    return None


def list_providers() -> list[dict]:
    """Return metadata about all registered providers."""
    return [
        {
            "name": prov.name,
            "domain": prov.domain.value,
            "description": prov.description,
            "entry_count": prov.count(),
        }
        for prov in PROVIDERS.values()
    ]


#: Entry-point group for third-party catalog-provider plugins.
_CATALOG_PROVIDER_GROUP = "gispulse.catalog_providers"


def _discover_providers() -> list[dict[str, str]]:
    """Register catalog-provider plugins discovered by the ExtensionHub.

    Issue #193 — the :class:`~core.plugin_hub.ExtensionHub` owns the single
    ``gispulse.catalog_providers`` entry-point scan; this function no
    longer scans a second time, it *consumes* ``ExtensionHub.records``.

    Each catalog-provider record is an ``EXTENSION`` whose loaded ``obj``
    is the plugin's register callable — invoking it calls
    :func:`register_provider`. A record the hub locked (tier/trust gate)
    or failed to load is reported but never invoked.

    Returns:
        List of dicts with ``name``, ``module`` and ``status`` for each
        catalog-provider record the hub discovered.
    """
    loaded: list[dict[str, str]] = []
    try:
        from gispulse.core.plugin_hub import ExtensionHub
        from gispulse.core.plugin_model import PluginState

        hub = ExtensionHub.get()
    except Exception:
        return loaded

    for rec in hub.records:
        ep = rec.entry_point
        if ep is None or getattr(ep, "group", None) != _CATALOG_PROVIDER_GROUP:
            continue
        module = str(getattr(ep, "value", ""))
        if rec.state is not PluginState.ACTIVE:
            loaded.append(
                {
                    "name": rec.name,
                    "module": module,
                    "status": f"skipped: {rec.detail or rec.state.value}",
                }
            )
            continue
        try:
            rec.obj()  # the loaded register callable
            loaded.append({"name": rec.name, "module": module, "status": "ok"})
        except Exception as exc:
            loaded.append(
                {"name": rec.name, "module": module, "status": f"error: {exc}"}
            )
    return loaded
