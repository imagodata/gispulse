"""Bridge: expose a legacy CatalogProvider as a DataSource (issue #179).

The catalog subsystem predates the unified plugin model: a
:class:`~catalog.providers.base.CatalogProvider` only *describes*
datasets (search / list), it does not ingest them.

:class:`CatalogProviderSource` adapts any provider to the
:class:`~core.sources.DataSource` contract as a *discovery-only* source —
``catalog()`` delegates to the provider while ``fetch()`` raises
:class:`~core.sources.ProtocolNotSupported`. This folds the
``gispulse.catalog_providers`` family into the source family without
rewriting the seven built-in providers; providers that can actually
ingest data are reimplemented as native ``DataSource`` subclasses over
time (issue #184 pilot packages).
"""

from __future__ import annotations

from typing import Any

from gispulse.catalog.models import CatalogDomain
from gispulse.catalog.providers.base import CatalogProvider
from gispulse.catalog.registry import PROVIDERS
from gispulse.core.plugin_model import FetchMode, Payload, SourceDomain, SourceResult
from gispulse.core.sources import ProtocolNotSupported

# Legacy catalog domains are coarse — a bridged provider keeps
# ``SourceDomain.BASE``. Native DataSource reimplementations carry the
# precise domain (foncier, reglementaire, …).
_DOMAIN_MAP: dict[CatalogDomain, SourceDomain] = {
    CatalogDomain.PROJECTION: SourceDomain.BASE,
    CatalogDomain.BASEMAP: SourceDomain.BASE,
    CatalogDomain.FLUX: SourceDomain.BASE,
    CatalogDomain.OPENDATA: SourceDomain.BASE,
}

# Best-effort payload per legacy domain — informational only, since a
# bridged source refuses ``fetch()`` anyway.
_PAYLOAD_MAP: dict[CatalogDomain, Payload] = {
    CatalogDomain.PROJECTION: Payload.TABLE,
    CatalogDomain.BASEMAP: Payload.TILES,
    CatalogDomain.FLUX: Payload.TILES,
    CatalogDomain.OPENDATA: Payload.VECTOR,
}


class CatalogProviderSource:
    """A discovery-only :class:`~core.sources.DataSource` over a CatalogProvider.

    Structurally satisfies the ``DataSource`` Protocol. ``fetch()`` is
    deliberately unsupported: a catalog provider answers *what exists*,
    not *how to ingest it*.
    """

    jurisdiction = "*"

    def __init__(self, provider: CatalogProvider) -> None:
        self._provider = provider
        self.name = provider.name
        self.domain = _DOMAIN_MAP.get(provider.domain, SourceDomain.BASE)
        self.payload = _PAYLOAD_MAP.get(provider.domain, Payload.VECTOR)

    def catalog(self, search: str | None = None) -> list[Any]:
        """Delegate discovery to the wrapped provider."""
        return self._provider.list_entries(search=search)

    def fetch(
        self,
        entry_id: str,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        """Always refused — a CatalogProvider describes, it does not ingest."""
        raise ProtocolNotSupported(
            f"'{self.name}' is a discovery-only catalog provider; it cannot "
            f"fetch entry '{entry_id}' — use a native DataSource instead"
        )

    def schema(self, entry_id: str) -> dict[str, Any]:
        """Empty schema; raises ``KeyError`` for an unknown entry."""
        if self._provider.get_entry(entry_id) is None:
            raise KeyError(f"{self.name}: unknown entry '{entry_id}'")
        return {}

    def revision(self, entry_id: str) -> str | None:
        """Legacy catalog providers expose no freshness token."""
        return None


def bridge_catalog_providers() -> list[CatalogProviderSource]:
    """Wrap every currently-registered catalog provider as a DataSource.

    Snapshots ``catalog.registry.PROVIDERS`` at call time.
    """
    return [CatalogProviderSource(provider) for provider in PROVIDERS.values()]


__all__ = ["CatalogProviderSource", "bridge_catalog_providers"]
