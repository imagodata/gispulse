"""Unit tests for the gispulse-src-nature plugin."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from gispulse.adapters.rest.rest_fetcher import register_rest_geojson_fetcher
from gispulse.plugins.api import (
    AccessProtocol,
    DataSource,
    Payload,
    ProtocolRegistry,
    SourceDomain,
)

_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-src-nature"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

_API_CARTO_NATURE = "https://apicarto.ign.fr/api/nature"
_EXPECTED_ENTRIES = {
    "natura-habitat": (
        "Natura 2000 directive Habitat",
        "/natura-habitat",
    ),
    "natura-oiseaux": (
        "Natura 2000 directive Oiseaux",
        "/natura-oiseaux",
    ),
    "znieff1": (
        "ZNIEFF type 1",
        "/znieff1",
    ),
    "znieff2": (
        "ZNIEFF type 2",
        "/znieff2",
    ),
}


def _nature_source_cls():
    try:
        module = importlib.import_module("gispulse_src_nature.source")
    except ModuleNotFoundError as exc:
        pytest.fail(f"gispulse-src-nature source module should exist: {exc}")
    return module.NatureSource


@pytest.fixture
def source():
    return _nature_source_cls()()


def test_nature_is_a_datasource(source) -> None:
    assert isinstance(source, DataSource)
    assert source.name == "nature"
    assert source.domain is SourceDomain.ENVIRONNEMENT
    assert source.payload is Payload.VECTOR
    assert source.jurisdiction == "FR"


def test_catalog_lists_four_nature_entries(source) -> None:
    entries = source.catalog()
    assert [entry.id for entry in entries] == list(_EXPECTED_ENTRIES)

    for entry in entries:
        label, path = _EXPECTED_ENTRIES[entry.id]
        assert entry.name == label
        assert entry.domain is SourceDomain.ENVIRONNEMENT
        assert entry.payload is Payload.VECTOR
        assert entry.jurisdiction == "FR"
        assert entry.metadata == {
            "provider": "IGN / INPN",
            "platform": "API Carto Nature",
            "license": "Licence Ouverte 2.0",
        }
        assert entry.access.protocol is AccessProtocol.REST_API
        assert entry.access.endpoint == f"{_API_CARTO_NATURE}{path}"
        assert entry.access.params == {"geom_param": "geom"}
        assert entry.access.format == "application/json"


def test_catalog_search_filters_by_id_or_label(source) -> None:
    assert [entry.id for entry in source.catalog(search="oiseaux")] == [
        "natura-oiseaux"
    ]
    assert [entry.id for entry in source.catalog(search="znieff")] == [
        "znieff1",
        "znieff2",
    ]


def test_schema_exposes_raw_api_carto_nature_fields(source) -> None:
    expected_fields = {
        "gml_id": "str",
        "id": "str",
        "nom": "str",
        "sitename": "str",
        "sitecode": "str",
        "geometry": "geometry",
    }

    for entry_id in _EXPECTED_ENTRIES:
        schema = source.schema(entry_id)
        for field, kind in expected_fields.items():
            assert schema[field] == kind


def test_revision_is_unknown_without_a_runtime_geometry_probe(source) -> None:
    for entry_id in _EXPECTED_ENTRIES:
        assert source.revision(entry_id) is None


def test_unknown_entry_raises(source) -> None:
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.schema("ghost")
    with pytest.raises(KeyError, match="unknown entry 'ghost'"):
        source.revision("ghost")


def test_fetch_delegates_to_rest_geojson_fetcher_with_geom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_get_geojson(url: str, timeout: float) -> dict:
        calls.append({"url": url, "timeout": timeout})
        return {"type": "FeatureCollection", "features": []}

    monkeypatch.setattr(
        "gispulse.adapters.rest.rest_fetcher._get_geojson", fake_get_geojson
    )
    registry = ProtocolRegistry()
    register_rest_geojson_fetcher(registry)

    source = _nature_source_cls()(registry=registry)
    result = source.fetch("znieff1", extent=(1, 2, 3, 4))

    assert result.payload is Payload.VECTOR
    assert len(calls) == 1
    url = str(calls[0]["url"])
    assert url.startswith(f"{_API_CARTO_NATURE}/znieff1?")
    query = parse_qs(urlsplit(url).query)
    geom = json.loads(query["geom"][0])
    assert geom["type"] == "Polygon"


def test_register_hook_registers_nature_source(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("gispulse_src_nature")
    sources = getattr(sys.modules[DataSource.__module__], "SOURCES")

    monkeypatch.setattr(sources, "_sources", {})
    module.register()

    registered = sources.get("nature")
    assert isinstance(registered, DataSource)
    assert registered.domain is SourceDomain.ENVIRONNEMENT
