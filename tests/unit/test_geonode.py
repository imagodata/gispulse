"""Unit tests for the GeoNode provider (DP1)."""

from __future__ import annotations

import geopandas as gpd
import httpx
import pytest
from shapely.geometry import Point

from gispulse.core.plugin_model import AccessProtocol
from gispulse.persistence.geonode import (
    GEONODES,
    GeoNode,
    GeoNodeRegistry,
    parse_geonode_uri,
    publish,
    read_access,
)
from gispulse.persistence.loader import resolve_source


@pytest.fixture
def points_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": ["a", "b"]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )


@pytest.fixture(autouse=True)
def _clean_default_registry():
    GEONODES.clear()
    yield
    GEONODES.clear()


# ---------------------------------------------------------------------------
# GeoNode helpers
# ---------------------------------------------------------------------------


def test_endpoints():
    n = GeoNode(name="p", host="https://geo.example.org/")
    assert n.wfs_endpoint() == "https://geo.example.org/geoserver/ows"
    assert n.upload_endpoint() == "https://geo.example.org/api/v2/uploads"


def test_typename_qualified_vs_default():
    n = GeoNode(name="p", host="https://h", workspace="geonode")
    assert n.typename("roads") == "geonode:roads"
    assert n.typename("other:roads") == "other:roads"


def test_read_access_spec():
    n = GeoNode(name="p", host="https://h", crs="EPSG:2154", auth="tok")
    spec = n.read_access("roads")
    assert spec.protocol is AccessProtocol.OGC_FEATURES
    assert spec.endpoint == "https://h/geoserver/ows"
    assert spec.params["collection"] == "geonode:roads"
    assert spec.params["source_type"] == "wfs"
    assert spec.params["crs"] == "EPSG:2154"
    assert spec.params["auth"] == "tok"


# ---------------------------------------------------------------------------
# parse_geonode_uri
# ---------------------------------------------------------------------------


def test_parse_uri_ok():
    assert parse_geonode_uri("geonode://prod/roads") == ("prod", "roads")


@pytest.mark.parametrize("uri", ["geonode://prod", "geonode://prod/", "wfs://h/x"])
def test_parse_uri_invalid(uri):
    with pytest.raises(ValueError):
        parse_geonode_uri(uri)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_register_and_resolve_alias():
    reg = GeoNodeRegistry()
    reg.register(GeoNode(name="prod", host="https://prod.example.org"))
    assert reg.resolve("prod").host == "https://prod.example.org"


def test_resolve_bare_host_defaults():
    reg = GeoNodeRegistry()
    node = reg.resolve("geo.example.org")
    assert node.host == "https://geo.example.org"
    assert node.wfs_endpoint() == "https://geo.example.org/geoserver/ows"


def test_register_duplicate_raises():
    reg = GeoNodeRegistry()
    reg.register(GeoNode(name="p", host="https://a"))
    with pytest.raises(ValueError):
        reg.register(GeoNode(name="p", host="https://b"))
    reg.register(GeoNode(name="p", host="https://b"), override=True)
    assert reg.resolve("p").host == "https://b"


def test_env_declarations(monkeypatch):
    monkeypatch.setenv(
        "GISPULSE_GEONODE",
        '{"prod": {"host": "https://prod.org", "workspace": "ws"}}',
    )
    reg = GeoNodeRegistry()
    assert reg.get("prod").typename("x") == "ws:x"


def test_env_invalid_json_ignored(monkeypatch):
    monkeypatch.setenv("GISPULSE_GEONODE", "nope{")
    reg = GeoNodeRegistry()
    assert reg.names() == []


# ---------------------------------------------------------------------------
# read_access / resolve_source integration
# ---------------------------------------------------------------------------


def test_read_access_uri():
    GEONODES.register(GeoNode(name="prod", host="https://prod.org"))
    spec = read_access("geonode://prod/roads")
    assert spec.endpoint == "https://prod.org/geoserver/ows"
    assert spec.params["collection"] == "geonode:roads"


def test_resolve_source_geonode():
    spec = resolve_source("geonode://geo.example.org/parcels")
    assert spec.protocol is AccessProtocol.OGC_FEATURES
    assert spec.endpoint == "https://geo.example.org/geoserver/ows"
    assert spec.params["collection"] == "geonode:parcels"


# ---------------------------------------------------------------------------
# publish (write) — httpx mocked
# ---------------------------------------------------------------------------


def test_publish_uploads_geopackage(points_gdf, monkeypatch):
    captured: dict = {}

    class _Resp:
        status_code = 201

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": 42, "name": "roads"}

    def _fake_post(url, *, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        # The file handle is live here — read a byte to prove it is a real gpkg.
        name, fh, mime = files["base_file"]
        captured["filename"] = name
        captured["mime"] = mime
        captured["head"] = fh.read(4)
        return _Resp()

    monkeypatch.setattr(httpx, "post", _fake_post)

    reg = GeoNodeRegistry()
    reg.register(GeoNode(name="prod", host="https://prod.org"))

    out = publish(points_gdf, "geonode://prod/roads", auth="tok", registry=reg)

    assert out == {"id": 42, "name": "roads"}
    assert captured["url"] == "https://prod.org/api/v2/uploads"
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["data"]["dataset_title"] == "roads"
    assert captured["data"]["overwrite_existing_layer"] == "true"
    assert captured["filename"] == "roads.gpkg"
    # GeoPackage files begin with the SQLite magic header.
    assert captured["head"] == b"SQLi"


def test_publish_raises_on_http_error(points_gdf, monkeypatch):
    class _Resp:
        status_code = 500

        def raise_for_status(self):
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _Resp()
    )
    reg = GeoNodeRegistry()
    reg.register(GeoNode(name="prod", host="https://prod.org"))
    with pytest.raises(httpx.HTTPStatusError):
        publish(points_gdf, "geonode://prod/roads", registry=reg)
