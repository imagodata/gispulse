"""Unit tests for the CatalogProvider → DataSource bridge (issue #179)."""

from __future__ import annotations

import pytest

from catalog.models import CatalogDomain, CatalogEntry
from catalog.providers.base import CatalogProvider
from catalog.registry import PROVIDERS, register_provider
from catalog.source_bridge import CatalogProviderSource, bridge_catalog_providers
from core.plugin_model import Payload, SourceDomain
from core.sources import DataSource, ProtocolNotSupported


class FakeProvider(CatalogProvider):
    """In-memory catalog provider with two entries."""

    name = "fake-ign"
    domain = CatalogDomain.OPENDATA
    description = "fake provider for tests"

    def __init__(self) -> None:
        self._entries = {
            f"opendata:fake-ign:{eid}": CatalogEntry(
                id=f"opendata:fake-ign:{eid}",
                domain=CatalogDomain.OPENDATA,
                provider="fake-ign",
                name=label,
            )
            for eid, label in [("bdtopo", "BD TOPO"), ("rpg", "RPG")]
        }

    def list_entries(self, search=None, tags=None, limit=50, offset=0):
        items = list(self._entries.values())
        if search:
            q = search.lower()
            items = [e for e in items if q in e.name.lower()]
        return items[offset : offset + limit]

    def get_entry(self, entry_id):
        return self._entries.get(entry_id)


@pytest.fixture
def source() -> CatalogProviderSource:
    return CatalogProviderSource(FakeProvider())


@pytest.fixture
def _clean_registry():
    snapshot = dict(PROVIDERS)
    yield
    PROVIDERS.clear()
    PROVIDERS.update(snapshot)


# --------------------------------------------------------------------------
# Contract conformance
# --------------------------------------------------------------------------


def test_bridge_satisfies_datasource_protocol(source: CatalogProviderSource) -> None:
    assert isinstance(source, DataSource)


def test_bridge_maps_axes(source: CatalogProviderSource) -> None:
    assert source.name == "fake-ign"
    assert source.domain is SourceDomain.BASE       # legacy domains stay coarse
    assert source.payload is Payload.VECTOR         # OPENDATA → vector
    assert source.jurisdiction == "*"


def test_basemap_provider_maps_to_tiles() -> None:
    class BasemapProvider(FakeProvider):
        name = "fake-basemap"
        domain = CatalogDomain.BASEMAP

    assert CatalogProviderSource(BasemapProvider()).payload is Payload.TILES


# --------------------------------------------------------------------------
# Discovery delegates; ingestion is refused
# --------------------------------------------------------------------------


def test_catalog_delegates_to_provider(source: CatalogProviderSource) -> None:
    assert len(source.catalog()) == 2
    found = source.catalog(search="topo")
    assert [e.name for e in found] == ["BD TOPO"]


def test_fetch_is_refused(source: CatalogProviderSource) -> None:
    with pytest.raises(ProtocolNotSupported, match="discovery-only"):
        source.fetch("opendata:fake-ign:bdtopo")


def test_schema_validates_entry(source: CatalogProviderSource) -> None:
    assert source.schema("opendata:fake-ign:bdtopo") == {}
    with pytest.raises(KeyError, match="unknown entry"):
        source.schema("opendata:fake-ign:ghost")


def test_revision_is_none(source: CatalogProviderSource) -> None:
    assert source.revision("opendata:fake-ign:bdtopo") is None


# --------------------------------------------------------------------------
# bridge_catalog_providers
# --------------------------------------------------------------------------


def test_bridge_catalog_providers_wraps_registered(_clean_registry) -> None:
    register_provider(FakeProvider())
    bridged = bridge_catalog_providers()
    names = {s.name for s in bridged}
    assert "fake-ign" in names
    assert all(isinstance(s, DataSource) for s in bridged)
