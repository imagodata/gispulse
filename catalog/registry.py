"""Provider registry and unified search for the GIS catalog."""

from __future__ import annotations

from catalog.models import CatalogDomain, CatalogEntry
from catalog.providers.base import CatalogProvider

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


def _discover_providers() -> list[dict[str, str]]:
    """Discover catalog providers from installed packages via entry-points.

    Scans the ``gispulse.catalog_providers`` entry-point group. Each
    entry-point must point to a callable that registers providers when
    invoked (e.g. by calling :func:`register_provider`).

    Returns:
        List of dicts with ``name``, ``module``, and ``status`` for each
        discovered plugin entry-point.
    """
    loaded: list[dict[str, str]] = []
    try:
        from importlib.metadata import entry_points

        eps = entry_points(group="gispulse.catalog_providers")
        for ep in eps:
            try:
                register_fn = ep.load()
                register_fn()
                loaded.append({"name": ep.name, "module": ep.value, "status": "ok"})
            except Exception as exc:
                loaded.append({"name": ep.name, "module": ep.value, "status": f"error: {exc}"})
    except Exception:
        pass
    return loaded
