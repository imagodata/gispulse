"""Unit tests for the gispulse-src-loyers plugin.

Zero-network: the plugin only declares data.gouv.fr CSV AccessSpecs. Core
fetchers own HTTP, file materialization and lazy scans.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-loyers"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_loyers.source", "gispulse_src_loyers"):
    sys.modules.pop(_module, None)

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import DataSource, ProtocolRegistry  # noqa: E402

pytestmark = pytest.mark.usefixtures("offline_ssrf")


class FakeTableFile:
    """Records table-file AccessSpecs and returns the endpoint."""

    protocol = AccessProtocol.TABLE_FILE

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.TABLE, mode=mode, data=access.endpoint)


def _source_class():
    return importlib.import_module("gispulse_src_loyers.source").LoyersSource


@pytest.fixture
def source():
    reg = ProtocolRegistry()
    reg.register(FakeTableFile())
    return _source_class()(registry=reg)


def test_pyproject_declares_loyers_entrypoint_and_statistical_manifest() -> None:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "loyers": "gispulse_src_loyers:register"
    }

    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "statistique"
    assert manifest["jurisdiction"] == "FR"


def test_register_adds_loyers_source_to_global_registry() -> None:
    from gispulse.core.sources import SOURCES

    LoyersSource = _source_class()
    register = importlib.import_module("gispulse_src_loyers").register

    SOURCES.clear()
    try:
        register()
        registered = SOURCES.get("loyers")
        assert isinstance(registered, LoyersSource)
        assert {entry.id for entry in registered.catalog()} == {
            "loyers_appartement_2025",
            "loyers_appartement_t1_t2_2025",
            "loyers_appartement_t3_plus_2025",
            "loyers_maison_2025",
            "zone_tendue_tlv_2025",
        }
    finally:
        SOURCES.clear()


def test_loyers_is_a_statistical_table_datasource(source) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "loyers"
    assert source.domain is SourceDomain.STATISTIQUE
    assert source.payload is Payload.TABLE
    assert source.jurisdiction == "FR"


def test_catalog_declares_rent_indicator_csv_files(source) -> None:
    entries = {entry.id: entry for entry in source.catalog()}

    appartement = entries["loyers_appartement_2025"]
    assert appartement.access.protocol is AccessProtocol.TABLE_FILE
    assert appartement.access.endpoint == (
        "https://static.data.gouv.fr/resources/"
        "carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/"
        "20251211-145010/pred-app-mef-dhup.csv"
    )
    assert appartement.access.params == {
        "table_format": "csv",
        "delimiter": ";",
        "decimal": ",",
    }
    assert appartement.access.format == "text/csv"
    assert appartement.payload is Payload.TABLE
    assert appartement.metadata["millesime"] == "2025"
    assert appartement.metadata["segment"] == "appartement"
    assert appartement.metadata["resource_id"] == "55b34088-0964-415f-9df7-d87dd98a09be"
    assert appartement.metadata["join_key"] == "INSEE_C"
    assert appartement.metadata["license"] == "License Not Specified (data.gouv.fr)"


def test_catalog_declares_tlv_zone_tendue_csv(source) -> None:
    entry = source._entry("zone_tendue_tlv_2025")

    assert entry.access.protocol is AccessProtocol.TABLE_FILE
    assert entry.access.endpoint == (
        "https://static.data.gouv.fr/resources/"
        "liste-des-communes-selon-le-zonage-tlv-1/"
        "20251230-094759/zonage-tlv-decret-22-dec-2025.csv"
    )
    assert entry.access.params == {"table_format": "csv", "delimiter": ";"}
    assert entry.domain is SourceDomain.REGLEMENTAIRE
    assert entry.payload is Payload.TABLE
    assert entry.metadata["resource_id"] == "efe71da1-15f8-4526-bcb8-5b9a9419c58c"
    assert entry.metadata["join_key"] == "CODGEO25"
    assert entry.metadata["decret"] == "Decret n. 2025-1267 du 22 decembre 2025"


def test_catalog_searches_by_segment_and_tension(source) -> None:
    assert [entry.id for entry in source.catalog(search="maison")] == [
        "loyers_maison_2025"
    ]
    assert [entry.id for entry in source.catalog(search="tendue")] == [
        "zone_tendue_tlv_2025"
    ]


def test_fetch_delegates_loyer_entry_to_table_file_adapter() -> None:
    table_file = FakeTableFile()
    reg = ProtocolRegistry()
    reg.register(table_file)
    src = _source_class()(registry=reg)

    result = src.fetch("loyers_maison_2025")

    assert result.payload is Payload.TABLE
    assert result.data.endswith("/20251211-145039/pred-mai-mef-dhup.csv")
    assert len(table_file.calls) == 1
    assert table_file.calls[0].protocol is AccessProtocol.TABLE_FILE


def test_schema_exposes_raw_loyer_indicator_fields(source) -> None:
    schema = source.schema("loyers_appartement_2025")

    assert schema["INSEE_C"] == "str"
    assert schema["LIBGEO"] == "str"
    assert schema["loypredm2"] == "float"
    assert schema["lwr.IPm2"] == "float"
    assert schema["upr.IPm2"] == "float"
    assert schema["TYPPRED"] == "str"
    assert schema["nbobs_com"] == "int"
    assert schema["nbobs_mail"] == "int"
    assert schema["R2_adj"] == "float"


def test_schema_exposes_raw_tlv_fields(source) -> None:
    schema = source.schema("zone_tendue_tlv_2025")

    assert schema["CODGEO25"] == "str"
    assert schema["DEP"] == "str"
    assert schema["Libell\u00e9 EPCI"] == "str"
    assert schema["Zonage TLV 2023"] == "str"
    assert schema["Zonage TLV post d\u00e9cret 22/12/2025"] == "str"


def test_revision_returns_static_resource_tokens(source) -> None:
    assert source.revision("loyers_appartement_2025") == (
        "data-gouv-loyers-2025-55b34088-0964-415f-9df7-d87dd98a09be-"
        "2025-12-11T14:50:11"
    )
    assert source.revision("zone_tendue_tlv_2025") == (
        "data-gouv-tlv-2025-efe71da1-15f8-4526-bcb8-5b9a9419c58c-"
        "2025-12-30T09:48:00"
    )


def test_unknown_entry_raises(source) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.schema("ghost")
