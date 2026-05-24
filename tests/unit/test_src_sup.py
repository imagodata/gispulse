"""Unit tests for the gispulse-src-sup plugin."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-sup"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from gispulse_src_sup import register  # noqa: E402
from gispulse_src_sup.source import SupSource  # noqa: E402

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


@pytest.fixture
def source() -> SupSource:
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
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
        "heritage-abf",
        "risk-ppr-zoning",
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
        assert entry.metadata["provider"] == "IGN / Géoportail de l'Urbanisme"
        assert entry.metadata["platform"] == "WFS SUP"
        assert entry.metadata["typename"] == entry.access.params["typename"]


# --------------------------------------------------------------------------
# Declarative fetch — delegates to the WFS adapter, zero network code
# --------------------------------------------------------------------------


def test_fetch_delegates_to_wfs_adapter() -> None:
    wfs = FakeWFS()
    reg = ProtocolRegistry()
    reg.register(wfs)
    src = SupSource(registry=reg)

    result = src.fetch("heritage-abf")

    assert result.payload is Payload.VECTOR
    assert "wfs_sup:assiette_sup_s" in result.data
    assert len(wfs.calls) == 1
    assert wfs.calls[0].protocol is AccessProtocol.WFS
    assert wfs.calls[0].params["cql_filter"] == "suptype IN ('AC1','AC2','AC4')"


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
