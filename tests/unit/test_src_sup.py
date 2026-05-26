"""Unit tests for the gispulse-src-sup plugin."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-sup"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_sup.source", "gispulse_src_sup"):
    sys.modules.pop(_module, None)

from gispulse_src_sup import register  # noqa: E402
from gispulse_src_sup.source import SupSource, sup_partition  # noqa: E402

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import (  # noqa: E402
    SOURCES,
    DataSource,
    ProtocolRegistry,
)


class FakeWFS:
    """Records the AccessSpec it is handed and returns a marker result."""

    protocol = AccessProtocol.WFS

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(
            payload=Payload.VECTOR, mode=mode, data=access.params["typename"]
        )


class FakeDownload:
    """Records resolved AccessSpecs for CNIG SUP bulk archives."""

    protocol = AccessProtocol.DOWNLOAD

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=access.endpoint)


@pytest.fixture
def source() -> SupSource:
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    reg.register(FakeDownload())
    return SupSource(registry=reg)


# --------------------------------------------------------------------------
# Contract conformance + declared axes
# --------------------------------------------------------------------------


def test_sup_is_a_datasource(source: SupSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "sup"
    assert source.domain is SourceDomain.REGLEMENTAIRE
    assert source.payload is Payload.VECTOR
    assert source.jurisdiction == "FR"


def test_catalog_lists_raw_layers_and_filtered_views(source: SupSource) -> None:
    ids = {e.id for e in source.catalog()}
    assert ids == {
        "servitude",
        "assiette-surf",
        "assiette-lin",
        "assiette-pct",
        "generateur-surf",
        "generateur-lin",
        "generateur-pct",
        "acte-sup",
        "heritage-abf",
        "risk-ppr-zoning",
        "pack_sup",
    }


def test_catalog_search_assiette_matches_raw_and_views(source: SupSource) -> None:
    found = source.catalog(search="assiette")
    assert {e.id for e in found} == {
        "assiette-surf",
        "assiette-lin",
        "assiette-pct",
        "heritage-abf",
        "risk-ppr-zoning",
    }


def test_every_entry_carries_classification_axes(source: SupSource) -> None:
    for entry in source.catalog():
        assert entry.domain is SourceDomain.REGLEMENTAIRE, entry.id
        assert entry.payload is Payload.VECTOR, entry.id
        assert entry.jurisdiction == "FR", entry.id


# --------------------------------------------------------------------------
# AccessSpec — WFS SUP namespace on the Géoplateforme
# --------------------------------------------------------------------------


def test_every_entry_targets_wfs_sup_typename(source: SupSource) -> None:
    expected = {
        "servitude": ("wfs_sup:servitude", None),
        "assiette-surf": ("wfs_sup:assiette_sup_s", None),
        "assiette-lin": ("wfs_sup:assiette_sup_l", None),
        "assiette-pct": ("wfs_sup:assiette_sup_p", None),
        "generateur-surf": ("wfs_sup:generateur_sup_s", None),
        "generateur-lin": ("wfs_sup:generateur_sup_l", None),
        "generateur-pct": ("wfs_sup:generateur_sup_p", None),
        "acte-sup": ("wfs_sup:acte_sup", None),
        "heritage-abf": (
            "wfs_sup:assiette_sup_s",
            "suptype IN ('AC1','AC2','AC4')",
        ),
        "risk-ppr-zoning": (
            "wfs_sup:assiette_sup_s",
            "suptype IN ('PM1','PM1BIS','PM3')",
        ),
    }

    for entry in source.catalog():
        if entry.id == "pack_sup":
            continue
        access = entry.access
        typename, cql_filter = expected[entry.id]
        assert access.protocol is AccessProtocol.WFS
        assert access.endpoint == "https://data.geopf.fr/wfs/ows"
        assert access.params["typename"] == typename
        assert access.params.get("cql_filter") == cql_filter
        assert access.format == "application/json"


def test_filtered_views_expose_suptype_filter_metadata(source: SupSource) -> None:
    entries = {e.id: e for e in source.catalog()}

    assert entries["heritage-abf"].metadata["suptype_filter"] == (
        "suptype IN ('AC1','AC2','AC4')"
    )
    assert entries["risk-ppr-zoning"].metadata["suptype_filter"] == (
        "suptype IN ('PM1','PM1BIS','PM3')"
    )
    assert "suptype_filter" not in entries["assiette-surf"].metadata


def test_every_entry_carries_provider_metadata(source: SupSource) -> None:
    for entry in source.catalog():
        if entry.id == "pack_sup":
            continue
        assert entry.metadata["provider"] == "IGN / Géoportail de l'Urbanisme"
        assert entry.metadata["platform"] == "WFS SUP"
        assert entry.metadata["typename"] == entry.access.params["typename"]
        assert entry.metadata["layer"]


# --------------------------------------------------------------------------
# Declarative fetch — delegates to the WFS adapter, zero network code
# --------------------------------------------------------------------------


def test_fetch_delegates_to_wfs_adapter() -> None:
    wfs = FakeWFS()
    reg = ProtocolRegistry()
    reg.register(wfs)
    reg.register(FakeDownload())
    src = SupSource(registry=reg)

    result = src.fetch("heritage-abf")

    assert result.payload is Payload.VECTOR
    assert "wfs_sup:assiette_sup_s" in result.data
    assert len(wfs.calls) == 1
    assert wfs.calls[0].protocol is AccessProtocol.WFS
    assert wfs.calls[0].params["cql_filter"] == "suptype IN ('AC1','AC2','AC4')"


def test_pack_sup_declares_cnig_partition_download(source: SupSource) -> None:
    entry = next(e for e in source.catalog() if e.id == "pack_sup")
    access = entry.access

    assert access.protocol is AccessProtocol.DOWNLOAD
    assert access.endpoint == (
        "https://www.geoportail-urbanisme.gouv.fr/api/document/"
        "download-by-partition/{partition}"
    )
    assert access.params == {
        "partition": "172014607_SUP_69_AC1",
        "codeGeo": "69",
        "categorie": "AC1",
    }
    assert access.format == "application/zip"
    assert entry.metadata["base_key"] == "pack_sup"
    assert entry.metadata["partition_pattern"] == "{idGest_}SUP_<codeGeo>_<categorie>"
    assert entry.metadata["code_geo_default"] == "69"
    assert entry.metadata["join_keys"] == ("idsup", "suptype")


def test_sup_partition_helper_supports_optional_idgest() -> None:
    assert sup_partition("69", "AC1") == "SUP_69_AC1"
    assert sup_partition("69", "AC1", id_gest="172014607") == "172014607_SUP_69_AC1"
    assert sup_partition("R84", "PM1", id_gest="130008915") == "130008915_SUP_R84_PM1"


def test_fetch_pack_sup_resolves_partition_template() -> None:
    download = FakeDownload()
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    reg.register(download)
    src = SupSource(registry=reg)

    result = src.fetch("pack_sup")

    assert result.payload is Payload.VECTOR
    assert result.data.endswith("/download-by-partition/172014607_SUP_69_AC1")
    assert "{partition}" not in result.data
    assert len(download.calls) == 1
    assert download.calls[0].protocol is AccessProtocol.DOWNLOAD


def test_fetch_unknown_entry_raises(source: SupSource) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.fetch("ghost")


# --------------------------------------------------------------------------
# Schema — raw WFS SUP attributes only
# --------------------------------------------------------------------------


def test_schema_exposes_raw_wfs_sup_fields(source: SupSource) -> None:
    schema = source.schema("heritage-abf")

    assert schema["gid"] == "int"
    assert schema["suptype"] == "str"
    assert schema["idsup"] == "str"
    assert schema["nomsuplitt"] == "str"
    assert schema["geometry"] == "geometry"


def test_schema_is_shared_by_raw_layers_and_filtered_views(source: SupSource) -> None:
    raw_schema = source.schema("assiette-surf")
    filtered_schema = source.schema("risk-ppr-zoning")

    assert filtered_schema == raw_schema


def test_pack_sup_schema_exposes_cnig_join_keys(source: SupSource) -> None:
    schema = source.schema("pack_sup")

    assert schema["idsup"] == "str"
    assert schema["suptype"] == "str"
    assert schema["partition"] == "str"


def test_acte_sup_schema_exposes_raw_wfs_sup_fields(source: SupSource) -> None:
    schema = source.schema("acte-sup")

    assert schema["gid"] == "int"
    assert schema["idsup"] == "str"
    assert schema["nomsuplitt"] == "str"
    assert schema["geometry"] == "geometry"


# --------------------------------------------------------------------------
# revision() — HEAD on WFS GetCapabilities, never a fetch()
# --------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for an ``httpx.Response`` — only ``.headers``."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_revision_probes_wfs_capabilities_and_uses_etag(
    source: SupSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_head(url, **_kw):
        captured.append(url)
        return _FakeResponse({"etag": '"sup-millesime-2026-05"'})

    monkeypatch.setattr("httpx.head", fake_head)
    token = source.revision("servitude")

    assert token == "sup-millesime-2026-05"
    assert captured and "GetCapabilities" in captured[0]


def test_revision_falls_back_to_last_modified(
    source: SupSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_head(url, **_kw):
        return _FakeResponse({"last-modified": "Mon, 12 May 2026 06:00:00 GMT"})

    monkeypatch.setattr("httpx.head", fake_head)
    assert source.revision("assiette-surf") == "Mon, 12 May 2026 06:00:00 GMT"


def test_revision_returns_none_on_network_error(
    source: SupSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_head(url, **_kw):
        raise RuntimeError("simulated DNS failure")

    monkeypatch.setattr("httpx.head", fake_head)
    assert source.revision("risk-ppr-zoning") is None


def test_revision_probes_pack_sup_redirect_location(
    source: SupSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_head(url, **_kw):
        captured.append(url)
        return _FakeResponse(
            {
                "location": (
                    "https://www.geoportail-urbanisme.gouv.fr/document/"
                    "sup/2026-05-26/172014607_SUP_69_AC1-a4d8.zip"
                )
            }
        )

    monkeypatch.setattr("httpx.head", fake_head)

    assert source.revision("pack_sup") == (
        "https://www.geoportail-urbanisme.gouv.fr/document/"
        "sup/2026-05-26/172014607_SUP_69_AC1-a4d8.zip"
    )
    assert captured == [
        "https://www.geoportail-urbanisme.gouv.fr/api/document/"
        "download-by-partition/172014607_SUP_69_AC1"
    ]


def test_revision_returns_none_for_pack_sup_without_redirect_or_headers(
    source: SupSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_head(url, **_kw):
        return _FakeResponse({})

    monkeypatch.setattr("httpx.head", fake_head)
    assert source.revision("pack_sup") is None


# --------------------------------------------------------------------------
# register() entry-point hook
# --------------------------------------------------------------------------


def test_register_adds_sup_source_to_registry() -> None:
    SOURCES.clear()
    try:
        register()
        src = SOURCES.get("sup")
        assert isinstance(src, SupSource)
    finally:
        SOURCES.clear()
