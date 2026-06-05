"""Unit tests for the gispulse-src-gipod-be plugin.

Zero-network : le plugin déclare des AccessSpec WFS/OGC et ne fait aucun réseau.
Les fetchers core possèdent HTTP, pagination et matérialisation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-gipod-be"
_PKG_PATH = str(_PKG)
if _PKG_PATH in sys.path:
    sys.path.remove(_PKG_PATH)
sys.path.insert(0, _PKG_PATH)
for _module in ("gispulse_src_gipod_be.source", "gispulse_src_gipod_be"):
    sys.modules.pop(_module, None)

from gispulse_src_gipod_be.source import GipodBeSource  # noqa: E402

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    Payload,
    SourceDomain,
)
from gispulse.core.sources import DataSource  # noqa: E402

pytestmark = pytest.mark.usefixtures("offline_ssrf")


@pytest.fixture
def source() -> GipodBeSource:
    return GipodBeSource()


# ---------------------------------------------------------------------------
# pyproject.toml — déclaration entry-point et manifest
# ---------------------------------------------------------------------------


def test_pyproject_declares_gipod_be_entrypoint_and_manifest() -> None:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((_PKG / "pyproject.toml").read_text())

    assert pyproject["project"]["entry-points"]["gispulse.data_sources"] == {
        "gipod_be": "gispulse_src_gipod_be:register"
    }

    manifest = pyproject["tool"]["gispulse"]["plugin"]
    assert manifest["kind"] == "source"
    assert manifest["domain"] == "reglementaire"
    assert manifest["jurisdiction"] == "BE"


# ---------------------------------------------------------------------------
# Conformité contrat DataSource
# ---------------------------------------------------------------------------


def test_is_a_reglementaire_vector_datasource(source: GipodBeSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "gipod_be"
    assert source.domain is SourceDomain.REGLEMENTAIRE
    assert source.payload is Payload.VECTOR
    assert source.jurisdiction == "BE"


# ---------------------------------------------------------------------------
# entries() — couverture des deux concepts du brief
# ---------------------------------------------------------------------------


def test_declares_inname_and_hinder_entries(source: GipodBeSource) -> None:
    ids = {entry.id for entry in source.entries()}
    assert ids == {"inname", "hinder"}


def test_all_entries_use_wfs_protocol(source: GipodBeSource) -> None:
    for entry in source.entries():
        assert entry.access.protocol is AccessProtocol.WFS


def test_all_entries_target_geo_api_vlaanderen(source: GipodBeSource) -> None:
    for entry in source.entries():
        assert entry.access.endpoint == "https://geo.api.vlaanderen.be/GIPOD/wfs"


# ---------------------------------------------------------------------------
# Entrée inname — occupations du domaine public
# ---------------------------------------------------------------------------


def test_inname_entry_wfs_typename_and_format(source: GipodBeSource) -> None:
    entry = next(e for e in source.entries() if e.id == "inname")

    assert entry.access.params["typename"] == "GIPOD:INNAME"
    assert entry.access.params["version"] == "2.0.0"
    assert entry.access.params["crs"] == "EPSG:4326"
    assert entry.access.format == "application/json"


def test_inname_entry_metadata_identity_and_cotrench(source: GipodBeSource) -> None:
    entry = next(e for e in source.entries() if e.id == "inname")

    assert entry.metadata["provider"] == "Digitaal Vlaanderen / Vlaamse overheid"
    assert entry.metadata["identity_key"] == "GipodId"
    assert entry.metadata["cotrench_key"] == "GroundworkPartOfTrenchSynergy"
    assert entry.metadata["region"] == "Vlaanderen (Flandre)"
    assert entry.metadata["crs_native"] == "EPSG:31370"
    assert "6 mois" in entry.metadata["temporal_window"]


def test_inname_entry_carries_jurisdiction_and_domain(source: GipodBeSource) -> None:
    entry = next(e for e in source.entries() if e.id == "inname")

    assert entry.jurisdiction == "BE"
    assert entry.domain is SourceDomain.REGLEMENTAIRE
    assert entry.payload is Payload.VECTOR


def test_inname_entry_exposes_ogc_features_url_in_metadata(
    source: GipodBeSource,
) -> None:
    entry = next(e for e in source.entries() if e.id == "inname")

    ogc_url = entry.metadata["ogc_features_url"]
    assert "geo.api.vlaanderen.be/GIPOD/ogc/features" in ogc_url
    assert "INNAME" in ogc_url


# ---------------------------------------------------------------------------
# Entrée hinder — perturbations de mobilité planifiées
# ---------------------------------------------------------------------------


def test_hinder_entry_wfs_typename(source: GipodBeSource) -> None:
    entry = next(e for e in source.entries() if e.id == "hinder")

    assert entry.access.params["typename"] == "GIPOD:HINDER"
    assert entry.access.params["crs"] == "EPSG:4326"


def test_hinder_entry_metadata_provider_and_temporal_window(
    source: GipodBeSource,
) -> None:
    entry = next(e for e in source.entries() if e.id == "hinder")

    assert entry.metadata["provider"] == "Digitaal Vlaanderen / Vlaamse overheid"
    assert "6 mois" in entry.metadata["temporal_window"]
    assert entry.metadata["identity_key"] == "GipodId"


# ---------------------------------------------------------------------------
# schema()
# ---------------------------------------------------------------------------


def test_schema_inname_exposes_key_fields(source: GipodBeSource) -> None:
    schema = source.schema("inname")

    assert schema["GipodId"] == "int"
    assert schema["Start"] == "datetime"
    assert schema["End"] == "datetime"
    assert schema["GroundworkPartOfTrenchSynergy"] == "bool"
    assert schema["geometry"] == "geometry"
    assert schema["Status"] == "str"
    assert schema["Owner"] == "str"


def test_schema_hinder_exposes_key_fields(source: GipodBeSource) -> None:
    schema = source.schema("hinder")

    assert schema["GipodId"] == "int"
    assert schema["Start"] == "datetime"
    assert schema["End"] == "datetime"
    assert schema["geometry"] == "geometry"


def test_schema_raises_for_unknown_entry(source: GipodBeSource) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.schema("ghost")


# ---------------------------------------------------------------------------
# revision() — sonde HEAD GetCapabilities (monkeypatched)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, *, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def test_revision_returns_etag_when_present(
    source: GipodBeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.head",
        lambda *_a, **_kw: _FakeResponse(headers={"etag": '"2026-06-01"'}),
    )
    assert source.revision("inname") == "2026-06-01"
    assert source.revision("hinder") == "2026-06-01"


def test_revision_falls_back_to_last_modified(
    source: GipodBeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.head",
        lambda *_a, **_kw: _FakeResponse(
            headers={"last-modified": "Mon, 01 Jun 2026 00:00:00 GMT"}
        ),
    )
    assert source.revision("inname") == "Mon, 01 Jun 2026 00:00:00 GMT"


def test_revision_returns_none_on_network_error(
    source: GipodBeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.head",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert source.revision("inname") is None


def test_revision_returns_none_without_freshness_header(
    source: GipodBeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.head",
        lambda *_a, **_kw: _FakeResponse(headers={}),
    )
    assert source.revision("hinder") is None


def test_revision_raises_for_unknown_entry(
    source: GipodBeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.head",
        lambda *_a, **_kw: _FakeResponse(headers={}),
    )
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.revision("ghost")


def test_revision_probes_wfs_capabilities_url(
    source: GipodBeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_head(url: str, **_kw: object) -> _FakeResponse:
        captured.append(url)
        return _FakeResponse(headers={"etag": '"test"'})

    monkeypatch.setattr("httpx.head", fake_head)
    source.revision("inname")

    assert captured
    assert "geo.api.vlaanderen.be/GIPOD/wfs" in captured[0]
    assert "GetCapabilities" in captured[0]


# ---------------------------------------------------------------------------
# register() — intégration registre
# ---------------------------------------------------------------------------


def test_register_adds_source_to_registry() -> None:
    from gispulse.core.sources import SOURCES
    from gispulse_src_gipod_be import register

    SOURCES.clear()
    try:
        register()
        assert SOURCES.get("gipod_be") is not None
    finally:
        SOURCES.clear()
