"""Unit tests for the gispulse-src-ign pilot plugin (issue #194)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-ign"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from gispulse_src_ign.source import IgnSource  # noqa: E402

from core.plugin_model import (  # noqa: E402
    AccessProtocol,
    FetchMode,
    Payload,
    SourceDomain,
    SourceResult,
)
from core.sources import DataSource, ProtocolRegistry  # noqa: E402


class FakeWFS:
    """Fetcher that echoes the typename it is handed."""

    protocol = AccessProtocol.WFS

    def __init__(self) -> None:
        self.calls: list = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append(access)
        return SourceResult(
            payload=Payload.VECTOR, mode=mode, data=access.params["typename"]
        )


@pytest.fixture
def source() -> IgnSource:
    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    return IgnSource(registry=reg)


class _FakeResponse:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


# --------------------------------------------------------------------------
# Contract conformance
# --------------------------------------------------------------------------


def test_ign_is_a_datasource(source: IgnSource) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "ign"
    assert source.domain is SourceDomain.BASE
    assert source.payload is Payload.VECTOR
    assert source.jurisdiction == "FR"


def test_catalog_lists_six_entries(source: IgnSource) -> None:
    ids = {e.id for e in source.catalog()}
    assert ids == {
        "batiments",
        "routes",
        "cours_eau",
        "communes",
        "departements",
        "regions",
    }


def test_catalog_search(source: IgnSource) -> None:
    found = source.catalog(search="commune")
    assert [e.id for e in found] == ["communes"]


# --------------------------------------------------------------------------
# Declarative multi-layer fetch — delegates to the WFS adapter
# --------------------------------------------------------------------------


def test_fetch_delegates_per_layer(source: IgnSource) -> None:
    assert "BDTOPO_V3:batiment" in source.fetch("batiments").data
    assert "ADMINEXPRESS-COG.LATEST:region" in source.fetch("regions").data


def test_fetch_unknown_entry_raises(source: IgnSource) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.fetch("ghost")


# --------------------------------------------------------------------------
# GEOFLA legacy alias
# --------------------------------------------------------------------------


def test_geofla_alias_resolves_to_communes(source: IgnSource) -> None:
    """The retired GEOFLA name still resolves — to Admin Express communes."""
    result = source.fetch("geofla")
    assert "ADMINEXPRESS-COG.LATEST:commune" in result.data


def test_geofla_alias_not_advertised_in_catalog(source: IgnSource) -> None:
    """The alias works but is not surfaced as a first-class entry."""
    assert "geofla" not in {e.id for e in source.catalog()}


# --------------------------------------------------------------------------
# revision() — cheap WFS freshness probe
# --------------------------------------------------------------------------


def test_revision_uses_wfs_etag(
    source: IgnSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.head", lambda *_a, **_kw: _FakeResponse({"etag": '"ign-2026-04"'})
    )
    assert source.revision("communes") == "ign-2026-04"


def test_revision_resolves_alias(
    source: IgnSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "httpx.head", lambda *_a, **_kw: _FakeResponse({"etag": "x"})
    )
    assert source.revision("geofla") == "x"  # alias must not raise


def test_revision_none_on_network_error(
    source: IgnSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a, **_kw):
        raise RuntimeError("unreachable")

    monkeypatch.setattr("httpx.head", boom)
    assert source.revision("batiments") is None
