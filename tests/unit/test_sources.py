"""Unit tests for the ETL plugin contracts and protocol registry (issue #178)."""

from __future__ import annotations

import pytest

from core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
    WriteReport,
    WriteSpec,
)
from core.sources import (
    PROTOCOLS,
    DataSink,
    DataSource,
    DeclarativeSink,
    DeclarativeSource,
    Fetcher,
    ProtocolNotSupported,
    ProtocolRegistry,
    SourceEntryRef,
    Writer,
)


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------


class FakeWFS:
    """Fetcher-only adapter."""

    protocol = AccessProtocol.WFS

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=f"rows@{access.endpoint}")


class FakeDB:
    """Adapter that both reads and writes."""

    protocol = AccessProtocol.DB

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        return SourceResult(payload=Payload.TABLE, data="table")

    def write(self, result, spec):
        return WriteReport(destination=spec.destination, rows_written=3, created=True)


class NoProtocol:
    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):  # noqa: ARG002
        return SourceResult(payload=Payload.VECTOR)


class NotAnAdapter:
    protocol = AccessProtocol.WMS


class FakeCadastre(DeclarativeSource):
    name = "cadastre"
    domain = SourceDomain.FONCIER
    payload = Payload.VECTOR
    jurisdiction = "FR"

    def entries(self):
        return [
            SourceEntryRef(
                id="parcelles",
                name="Parcelles cadastrales",
                access=AccessSpec(protocol=AccessProtocol.WFS, endpoint="https://93.184.216.34/wfs"),
                revision_token="2026-01",
            ),
        ]


class FakePostgisSink(DeclarativeSink):
    name = "postgis"


@pytest.fixture
def registry() -> ProtocolRegistry:
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    reg.register(FakeDB())
    return reg


# --------------------------------------------------------------------------
# ProtocolRegistry
# --------------------------------------------------------------------------


def test_register_files_adapter_under_its_roles(registry: ProtocolRegistry) -> None:
    assert isinstance(registry.get_fetcher(AccessProtocol.WFS), FakeWFS)
    assert isinstance(registry.get_fetcher(AccessProtocol.DB), FakeDB)
    assert isinstance(registry.get_writer(AccessProtocol.DB), FakeDB)


def test_fetcher_only_adapter_has_no_writer(registry: ProtocolRegistry) -> None:
    with pytest.raises(ProtocolNotSupported, match="no writer"):
        registry.get_writer(AccessProtocol.WFS)


def test_missing_protocol_raises(registry: ProtocolRegistry) -> None:
    with pytest.raises(ProtocolNotSupported, match="no fetcher"):
        registry.get_fetcher(AccessProtocol.STAC)


def test_register_rejects_adapter_without_protocol() -> None:
    with pytest.raises(ValueError, match="must declare a 'protocol"):
        ProtocolRegistry().register(NoProtocol())


def test_register_rejects_non_adapter() -> None:
    with pytest.raises(TypeError, match="neither a Fetcher nor a Writer"):
        ProtocolRegistry().register(NotAnAdapter())


def test_register_rejects_duplicate_without_override() -> None:
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(FakeWFS())
    reg.register(FakeWFS(), override=True)  # explicit override is fine


def test_dispatch_fetch_resolves_and_runs(registry: ProtocolRegistry) -> None:
    access = AccessSpec(protocol=AccessProtocol.WFS, endpoint="https://93.184.216.34/wfs")
    result = registry.dispatch_fetch(access, mode=FetchMode.REFERENCE)
    assert result.data == "rows@https://93.184.216.34/wfs"
    assert result.mode is FetchMode.REFERENCE


def test_dispatch_write_resolves_and_runs(registry: ProtocolRegistry) -> None:
    spec = WriteSpec(protocol=AccessProtocol.DB, destination="postgis://analyse.t")
    report = registry.dispatch_write(SourceResult(payload=Payload.TABLE), spec)
    assert report.rows_written == 3 and report.created is True


def test_module_level_registry_is_shared() -> None:
    assert isinstance(PROTOCOLS, ProtocolRegistry)


# --------------------------------------------------------------------------
# Structural Protocol conformance
# --------------------------------------------------------------------------


def test_runtime_checkable_roles() -> None:
    assert isinstance(FakeWFS(), Fetcher)
    assert not isinstance(FakeWFS(), Writer)
    assert isinstance(FakeDB(), Fetcher)
    assert isinstance(FakeDB(), Writer)
    assert isinstance(FakeCadastre(), DataSource)
    assert isinstance(FakePostgisSink(), DataSink)


# --------------------------------------------------------------------------
# DeclarativeSource — fetch() delegates, zero network code
# --------------------------------------------------------------------------


def test_declarative_source_fetch_delegates(registry: ProtocolRegistry) -> None:
    src = FakeCadastre(registry=registry)
    result = src.fetch("parcelles")
    assert result.payload is Payload.VECTOR
    assert result.data == "rows@https://93.184.216.34/wfs"


def test_declarative_source_revision_returns_token(registry: ProtocolRegistry) -> None:
    assert FakeCadastre(registry=registry).revision("parcelles") == "2026-01"


def test_declarative_source_unknown_entry_raises(registry: ProtocolRegistry) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        FakeCadastre(registry=registry).fetch("ghost")


def test_declarative_source_catalog_search(registry: ProtocolRegistry) -> None:
    src = FakeCadastre(registry=registry)
    assert len(src.catalog()) == 1
    assert len(src.catalog(search="parcel")) == 1
    assert src.catalog(search="zzz") == []


def test_declarative_source_schema_validates_id(registry: ProtocolRegistry) -> None:
    src = FakeCadastre(registry=registry)
    assert src.schema("parcelles") == {}
    with pytest.raises(KeyError):
        src.schema("ghost")


# --------------------------------------------------------------------------
# DeclarativeSink — write() delegates
# --------------------------------------------------------------------------


def test_declarative_sink_write_delegates(registry: ProtocolRegistry) -> None:
    sink = FakePostgisSink(registry=registry)
    spec = WriteSpec(protocol=AccessProtocol.DB, destination="postgis://analyse.dvf")
    report = sink.write(SourceResult(payload=Payload.TABLE), spec)
    assert report.destination == "postgis://analyse.dvf"
    assert report.rows_written == 3
