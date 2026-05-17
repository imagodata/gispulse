"""Tests for the REST GeoJSON fetcher in the ProtocolRegistry (#192)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from gispulse.core.plugin_model import AccessProtocol, AccessSpec, FetchMode, Payload
from gispulse.core.sources import ProtocolNotSupported, ProtocolRegistry
from gispulse.adapters.rest.rest_fetcher import (
    RestGeoJsonFetcher,
    _bbox_from_extent,
    _bbox_polygon,
    register_rest_geojson_fetcher,
)


def _feature_collection(n: int) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(i), float(i)]},
                "properties": {"id": f"f{i}"},
            }
            for i in range(n)
        ],
    }


@pytest.fixture
def mock_get(monkeypatch: pytest.MonkeyPatch) -> list:
    """Patch _get_geojson; return the list of (url, timeout) calls."""
    calls: list = []

    def fake(url: str, timeout: float) -> dict:
        calls.append({"url": url, "timeout": timeout})
        return _feature_collection(3)

    monkeypatch.setattr(
        "gispulse.adapters.rest.rest_fetcher._get_geojson", fake
    )
    return calls


def _query_of(url: str) -> dict:
    """Return the parsed query string of ``url`` as a flat dict."""
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_bbox_from_extent_valid() -> None:
    assert _bbox_from_extent((1, 2, 3, 4)) == (1.0, 2.0, 3.0, 4.0)


@pytest.mark.parametrize("bad", [None, (1, 2, 3), (1, 2, 3, 4, 5), "nope", 42])
def test_bbox_from_extent_rejects_non_bbox(bad: object) -> None:
    assert _bbox_from_extent(bad) is None


def test_bbox_polygon_is_closed_ring() -> None:
    poly = _bbox_polygon((0.0, 0.0, 10.0, 20.0))
    assert poly["type"] == "Polygon"
    ring = poly["coordinates"][0]
    assert ring[0] == ring[-1] == [0.0, 0.0]
    assert len(ring) == 5


# ---------------------------------------------------------------------------
# RestGeoJsonFetcher.fetch
# ---------------------------------------------------------------------------


def test_fetcher_declares_rest_protocol() -> None:
    assert RestGeoJsonFetcher.protocol is AccessProtocol.REST_API


def test_fetch_returns_vector_source_result(mock_get: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.REST_API,
        endpoint="https://apicarto.example.org/api/cadastre/parcelle",
        params={},
    )
    result = RestGeoJsonFetcher().fetch(access)

    assert result.payload is Payload.VECTOR
    assert len(result.data) == 3
    assert result.crs == "EPSG:4326"
    assert result.metadata["feature_count"] == 3


def test_geom_param_injects_bbox_polygon(mock_get: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.REST_API,
        endpoint="https://apicarto.example.org/api/cadastre/parcelle",
        params={"geom_param": "geom"},
    )
    RestGeoJsonFetcher().fetch(access, extent=(0, 0, 10, 10))

    query = _query_of(mock_get[0]["url"])
    assert "geom" in query
    geom = json.loads(query["geom"])
    assert geom["type"] == "Polygon"


def test_no_geom_param_means_no_geometry_query(mock_get: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.REST_API,
        endpoint="https://apicarto.example.org/api/cadastre/parcelle",
        params={},
    )
    RestGeoJsonFetcher().fetch(access, extent=(0, 0, 10, 10))
    assert urlsplit(mock_get[0]["url"]).query == ""


def test_vendor_params_forwarded_verbatim(mock_get: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.REST_API,
        endpoint="https://apicarto.example.org/api/cadastre/parcelle",
        params={"code_insee": "44109", "_limit": 50, "timeout": 5},
    )
    RestGeoJsonFetcher().fetch(access)

    query = _query_of(mock_get[0]["url"])
    assert query["code_insee"] == "44109"
    assert query["_limit"] == "50"
    # 'timeout' is a reserved key — consumed, not forwarded.
    assert "timeout" not in query
    assert mock_get[0]["timeout"] == 5.0


def test_endpoint_with_existing_query_appends_with_ampersand(
    mock_get: list,
) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.REST_API,
        endpoint="https://apicarto.example.org/api/parcelle?source=bdparcellaire",
        params={"code_insee": "44109"},
    )
    RestGeoJsonFetcher().fetch(access)
    query = _query_of(mock_get[0]["url"])
    assert query == {"source": "bdparcellaire", "code_insee": "44109"}


def test_non_featurecollection_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gispulse.adapters.rest.rest_fetcher._get_geojson",
        lambda url, timeout: {"type": "Feature", "geometry": None},
    )
    access = AccessSpec(
        protocol=AccessProtocol.REST_API,
        endpoint="https://apicarto.example.org/api/parcelle",
        params={},
    )
    with pytest.raises(ValueError, match="FeatureCollection"):
        RestGeoJsonFetcher().fetch(access)


def test_empty_featurecollection_yields_empty_gdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "gispulse.adapters.rest.rest_fetcher._get_geojson",
        lambda url, timeout: {"type": "FeatureCollection", "features": []},
    )
    access = AccessSpec(
        protocol=AccessProtocol.REST_API,
        endpoint="https://apicarto.example.org/api/parcelle",
        params={},
    )
    result = RestGeoJsonFetcher().fetch(access)
    assert result.payload is Payload.VECTOR
    assert len(result.data) == 0
    assert result.metadata["feature_count"] == 0


# ---------------------------------------------------------------------------
# Registration + dispatch
# ---------------------------------------------------------------------------


def test_register_rest_fetcher_into_fresh_registry() -> None:
    reg = ProtocolRegistry()
    with pytest.raises(ProtocolNotSupported):
        reg.get_fetcher(AccessProtocol.REST_API)

    register_rest_geojson_fetcher(reg)
    assert isinstance(reg.get_fetcher(AccessProtocol.REST_API), RestGeoJsonFetcher)


def test_register_rest_fetcher_is_idempotent() -> None:
    reg = ProtocolRegistry()
    register_rest_geojson_fetcher(reg)
    register_rest_geojson_fetcher(reg)  # second call must not raise
    assert isinstance(reg.get_fetcher(AccessProtocol.REST_API), RestGeoJsonFetcher)


def test_rest_fetcher_self_registered_in_global_protocols() -> None:
    """Importing the module wired the fetcher into the shared registry."""
    from gispulse.core.sources import PROTOCOLS

    assert isinstance(
        PROTOCOLS.get_fetcher(AccessProtocol.REST_API), RestGeoJsonFetcher
    )


def test_dispatch_fetch_routes_to_rest_fetcher(mock_get: list) -> None:
    """End-to-end: a REST AccessSpec dispatches through the registry."""
    reg = ProtocolRegistry()
    register_rest_geojson_fetcher(reg)
    access = AccessSpec(
        protocol=AccessProtocol.REST_API,
        endpoint="https://apicarto.ign.fr/api/cadastre/parcelle",
        params={"code_insee": "44109"},
    )
    result = reg.dispatch_fetch(access, mode=FetchMode.MATERIALIZE)
    assert result.payload is Payload.VECTOR
    assert len(mock_get) == 1
