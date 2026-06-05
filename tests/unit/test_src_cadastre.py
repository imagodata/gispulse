"""Unit tests for the gispulse-src-cadastre pilot plugin (issue #184)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-cadastre"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from gispulse_src_cadastre.source import CadastreSource  # noqa: E402

from gispulse.core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from gispulse.core.sources import DataSource, ProtocolRegistry  # noqa: E402


class FakeWFS:
    """Records the AccessSpec it is handed and returns a marker result."""

    protocol = AccessProtocol.WFS

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=access.params["typename"])


class FakeDownload:
    """Records resolved AccessSpec for the bulk Etalab entries."""

    protocol = AccessProtocol.DOWNLOAD

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=access.endpoint)


@pytest.fixture
def source() -> CadastreSource:
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    reg.register(FakeDownload())
    return CadastreSource(registry=reg)


# --------------------------------------------------------------------------
# Contract conformance + declared axes
# --------------------------------------------------------------------------


def test_cadastre_is_a_datasource(source: CadastreSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "cadastre"
    assert source.domain is SourceDomain.FONCIER
    assert source.payload is Payload.VECTOR
    assert source.jurisdiction == "FR"


def test_catalog_lists_wfs_and_bulk_entries(source: CadastreSource) -> None:
    ids = {e.id for e in source.catalog()}
    assert ids == {
        # IGN Géoplateforme — WFS live
        "parcelles", "communes", "batiments",
        # Etalab — bulk per département
        "parcelles_bulk", "communes_bulk", "sections_bulk", "batiments_bulk",
    }


def test_catalog_search(source: CadastreSource) -> None:
    # WFS + bulk both surface for the same family search term.
    found = {e.id for e in source.catalog(search="parcel")}
    assert found == {"parcelles", "parcelles_bulk"}


# --------------------------------------------------------------------------
# WFS family — declarative fetch + protocol, redistributor metadata
# --------------------------------------------------------------------------


def test_fetch_wfs_delegates_to_wfs_adapter() -> None:
    wfs = FakeWFS()
    reg = ProtocolRegistry()
    reg.register(wfs)
    reg.register(FakeDownload())
    src = CadastreSource(registry=reg)

    result = src.fetch("parcelles")

    assert result.payload is Payload.VECTOR
    assert "PARCELLAIRE_EXPRESS:parcelle" in result.data
    assert len(wfs.calls) == 1
    assert wfs.calls[0].protocol is AccessProtocol.WFS


def test_wfs_entry_metadata_advertises_ign(source: CadastreSource) -> None:
    entry = next(e for e in source.catalog() if e.id == "parcelles")
    assert entry.access.protocol is AccessProtocol.WFS
    assert entry.metadata["provider"] == "DGFiP"
    assert entry.metadata["redistributor"] == "IGN Géoplateforme"
    # Per-entry classification axes (#227) repeat the source-level
    # facets so the worldwide catalogue can index the entry directly.
    assert entry.domain is SourceDomain.FONCIER
    assert entry.jurisdiction == "FR"


def test_fetch_unknown_entry_raises(source: CadastreSource) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.fetch("ghost")


# --------------------------------------------------------------------------
# Bulk family — DOWNLOAD protocol, template endpoint, Etalab metadata
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry_id, layer",
    [
        ("parcelles_bulk", "parcelles"),
        ("communes_bulk", "communes"),
        ("sections_bulk", "sections"),
        ("batiments_bulk", "batiments"),
    ],
)
def test_bulk_entry_declares_download_with_template(
    source: CadastreSource, entry_id: str, layer: str
) -> None:
    entry = next(e for e in source.catalog() if e.id == entry_id)
    access = entry.access
    assert access.protocol is AccessProtocol.DOWNLOAD
    # ``{departement}`` placeholder must be present — resolution happens
    # at fetch time via ``ProtocolRegistry.dispatch_fetch`` (#276).
    assert "{departement}" in access.endpoint
    assert access.endpoint.endswith(f"-{{departement}}-{layer}.json.gz")
    # ``layer`` slot is self-describing (introspection); ``departement``
    # carries a default that callers override at fetch time.
    assert access.params == {"departement": "75", "layer": layer}
    # N3 bulk ingest uses these hints to keep the raw `.json.gz` archive
    # and the stage parquet filename aligned with the Etalab layer.
    assert entry.metadata["base_key"] == layer
    assert entry.metadata["archive_format"] == "json.gz"
    assert entry.metadata["data_format"] == "geojson"


def test_bulk_entry_metadata_advertises_etalab(source: CadastreSource) -> None:
    entry = next(e for e in source.catalog() if e.id == "parcelles_bulk")
    assert entry.metadata["provider"] == "DGFiP"
    assert entry.metadata["redistributor"] == "Etalab"
    assert entry.metadata["mirror"] == "cadastre.data.gouv.fr"
    assert entry.metadata["update_cadence"] == "quarterly"


def test_fetch_bulk_resolves_departement_template() -> None:
    """End-to-end: dispatch_fetch interpolates ``{departement}`` from params."""
    download = FakeDownload()
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    reg.register(download)
    src = CadastreSource(registry=reg)

    result = src.fetch("parcelles_bulk")
    # The fetcher receives a resolved endpoint, not the template.
    assert "{departement}" not in result.data
    assert "departements/75/cadastre-75-parcelles.json.gz" in result.data
    assert len(download.calls) == 1
    assert download.calls[0].protocol is AccessProtocol.DOWNLOAD


# --------------------------------------------------------------------------
# Schema + revision — WFS uses HEAD/Capabilities, bulk uses GET/datagouv
# --------------------------------------------------------------------------


def test_schema_per_layer(source: CadastreSource) -> None:
    # WFS family
    assert "contenance" in source.schema("parcelles")
    assert "code_insee" in source.schema("communes")
    assert source.schema("batiments")["geometry"] == "geometry"
    # Bulk family — Etalab GeoJSON column shape (verified live for dpt 75)
    p_bulk = source.schema("parcelles_bulk")
    assert "arpente" in p_bulk and "updated" in p_bulk
    assert source.schema("sections_bulk")["prefixe"] == "str"
    # Bâtiments have no feature-id — identity is decomposed across props.
    b_bulk = source.schema("batiments_bulk")
    assert "id" not in b_bulk
    assert b_bulk["type"] == "str" and b_bulk["nom"] == "str"


class _FakeResponse:
    """Minimal stand-in for an ``httpx.Response`` — headers + json + raise."""

    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        json_data: object = None,
        status_ok: bool = True,
    ) -> None:
        self.headers = headers or {}
        self._json = json_data
        self._ok = status_ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise RuntimeError("HTTP error")

    def json(self) -> object:
        return self._json


def test_wfs_revision_probes_wfs_and_uses_etag(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """revision() on a WFS entry derives its token from the WFS ETag header."""
    captured: list[str] = []

    def fake_head(url, **_kw):
        captured.append(url)
        return _FakeResponse(headers={"etag": '"millesime-2026-07"'})

    monkeypatch.setattr("httpx.head", fake_head)
    token = source.revision("parcelles")

    assert token == "millesime-2026-07"  # quotes stripped
    assert captured and "GetCapabilities" in captured[0]


def test_wfs_revision_falls_back_to_last_modified(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.head",
        lambda *_a, **_kw: _FakeResponse(
            headers={"last-modified": "Wed, 01 Jul 2026 00:00:00 GMT"}
        ),
    )
    assert source.revision("communes") == "Wed, 01 Jul 2026 00:00:00 GMT"


def test_wfs_revision_returns_none_on_network_error(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.head", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("nope"))
    )
    assert source.revision("parcelles") is None


def test_wfs_revision_returns_none_without_freshness_header(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("httpx.head", lambda *_a, **_kw: _FakeResponse(headers={}))
    assert source.revision("batiments") is None


def test_revision_validates_entry_id(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("httpx.head", lambda *_a, **_kw: _FakeResponse(headers={}))
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.revision("ghost")


def test_bulk_revision_probes_datagouv_api(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bulk entry probes the data.gouv dataset metadata API, not the WFS HEAD."""
    head_calls: list[str] = []
    get_calls: list[str] = []

    monkeypatch.setattr(
        "httpx.head",
        lambda url, **_kw: head_calls.append(url) or _FakeResponse(headers={}),
    )

    def fake_get(url, **_kw):
        get_calls.append(url)
        return _FakeResponse(json_data={"last_modified": "2026-03-01T00:00:00Z"})

    monkeypatch.setattr("httpx.get", fake_get)

    token = source.revision("parcelles_bulk")
    assert token == "2026-03-01T00:00:00Z"
    assert not head_calls, "bulk revision must not HEAD the WFS Capabilities"
    assert get_calls and "data.gouv.fr/api/1/datasets/cadastre" in get_calls[0]


def test_bulk_revision_returns_none_on_network_error(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.get", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("nope"))
    )
    assert source.revision("communes_bulk") is None


def test_bulk_revision_returns_none_on_missing_field(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.get", lambda *_a, **_kw: _FakeResponse(json_data={"title": "Cadastre"})
    )
    assert source.revision("batiments_bulk") is None
