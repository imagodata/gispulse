"""Unit tests for the gispulse-src-cadastre pilot plugin (issue #184)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-cadastre"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from gispulse_src_cadastre.source import CadastreSource  # noqa: E402

from gispulse.core.plugin_model import AccessProtocol, FetchMode, Payload, SourceDomain, SourceResult  # noqa: E402
from gispulse.core.sources import DataSource, ProtocolRegistry  # noqa: E402


class FakeWFS:
    """Records the AccessSpec it is handed and returns a marker result."""

    protocol = AccessProtocol.WFS

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(payload=Payload.VECTOR, mode=mode, data=access.params["typename"])


@pytest.fixture
def source() -> CadastreSource:
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
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


def test_catalog_lists_three_entries(source: CadastreSource) -> None:
    ids = {e.id for e in source.catalog()}
    assert ids == {"parcelles", "communes", "batiments"}


def test_catalog_search(source: CadastreSource) -> None:
    found = source.catalog(search="parcel")
    assert [e.id for e in found] == ["parcelles"]


# --------------------------------------------------------------------------
# Declarative fetch — delegates to the WFS adapter, zero network code
# --------------------------------------------------------------------------


def test_fetch_delegates_to_wfs_adapter() -> None:
    wfs = FakeWFS()
    reg = ProtocolRegistry()
    reg.register(wfs)
    src = CadastreSource(registry=reg)

    result = src.fetch("parcelles")

    assert result.payload is Payload.VECTOR
    assert "PARCELLAIRE_EXPRESS:parcelle" in result.data
    assert len(wfs.calls) == 1
    assert wfs.calls[0].protocol is AccessProtocol.WFS


def test_fetch_unknown_entry_raises(source: CadastreSource) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.fetch("ghost")


# --------------------------------------------------------------------------
# Schema + revision
# --------------------------------------------------------------------------


def test_schema_per_layer(source: CadastreSource) -> None:
    assert "contenance" in source.schema("parcelles")
    assert "code_insee" in source.schema("communes")
    assert source.schema("batiments")["geometry"] == "geometry"


class _FakeResponse:
    """Minimal stand-in for an ``httpx.Response`` — only ``.headers``."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_revision_probes_wfs_and_uses_etag(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """revision() derives its token from the WFS ETag header (#198)."""
    captured: list[str] = []

    def fake_head(url, **_kw):
        captured.append(url)
        return _FakeResponse({"etag": '"millesime-2026-07"'})

    monkeypatch.setattr("httpx.head", fake_head)
    token = source.revision("parcelles")

    assert token == "millesime-2026-07"  # quotes stripped
    assert captured and "GetCapabilities" in captured[0]


def test_revision_falls_back_to_last_modified(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.head",
        lambda *_a, **_kw: _FakeResponse(
            {"last-modified": "Wed, 01 Jul 2026 00:00:00 GMT"}
        ),
    )
    assert source.revision("communes") == "Wed, 01 Jul 2026 00:00:00 GMT"


def test_revision_returns_none_on_network_error(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable endpoint yields None — the watcher skips it."""

    def boom(*_a, **_kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("httpx.head", boom)
    assert source.revision("parcelles") is None


def test_revision_returns_none_without_freshness_header(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("httpx.head", lambda *_a, **_kw: _FakeResponse({}))
    assert source.revision("batiments") is None


def test_revision_validates_entry_id(
    source: CadastreSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("httpx.head", lambda *_a, **_kw: _FakeResponse({}))
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.revision("ghost")
