"""Unit tests for the unified data loader (DP0)."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
)
from gispulse.core.sources import ProtocolRegistry
from gispulse.persistence.loader import (
    LazyDataset,
    load,
    resolve_source,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def points_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": ["a", "b", "c"]},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
        crs="EPSG:4326",
    )


class _StubFetcher:
    """A minimal structural Fetcher returning a canned SourceResult."""

    def __init__(self, protocol: AccessProtocol, result: SourceResult) -> None:
        self.protocol = protocol
        self._result = result
        self.calls: list[tuple[AccessSpec, object, FetchMode]] = []

    def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
        self.calls.append((access, extent, mode))
        return self._result


# ---------------------------------------------------------------------------
# resolve_source
# ---------------------------------------------------------------------------


def test_resolve_local_path():
    assert resolve_source("data/x.geojson") == Path("data/x.geojson")
    assert resolve_source(Path("/abs/x.gpkg")) == Path("/abs/x.gpkg")


def test_resolve_windows_drive_stays_path():
    # Single-char scheme ("c") must not be mistaken for a protocol.
    assert resolve_source("C:\\data\\x.shp") == Path("C:\\data\\x.shp")


@pytest.mark.parametrize(
    "uri",
    ["s3://bucket/dir/data.parquet", "https://host/data.fgb", "s3://bucket/folder"],
)
def test_resolve_remote_table_by_extension(uri):
    spec = resolve_source(uri)
    assert isinstance(spec, AccessSpec)
    assert spec.protocol is AccessProtocol.REMOTE_TABLE
    assert spec.endpoint == uri


def test_resolve_http_non_table_is_download():
    spec = resolve_source("https://host/api/export.geojson")
    assert isinstance(spec, AccessSpec)
    assert spec.protocol is AccessProtocol.DOWNLOAD


def test_resolve_named_protocol_uri():
    spec = resolve_source("wfs://geo.example.org/wfs?typename=roads")
    assert isinstance(spec, AccessSpec)
    assert spec.protocol is AccessProtocol.WFS
    assert spec.endpoint == "https://geo.example.org/wfs?typename=roads"


def test_resolve_ogc_features_underscore_normalised():
    spec = resolve_source("ogc_features://host/collections/x")
    assert spec.protocol is AccessProtocol.OGC_FEATURES


def test_resolve_dict_descriptor():
    spec = resolve_source(
        {
            "protocol": "wfs",
            "endpoint": "https://host/wfs",
            "params": {"typename": "t"},
            "format": "geojson",
        }
    )
    assert isinstance(spec, AccessSpec)
    assert spec.protocol is AccessProtocol.WFS
    assert spec.params == {"typename": "t"}
    assert spec.format == "geojson"


def test_resolve_accessspec_passthrough():
    spec = AccessSpec(protocol=AccessProtocol.STAC, endpoint="https://h/stac")
    assert resolve_source(spec) is spec


# ---------------------------------------------------------------------------
# load — local files
# ---------------------------------------------------------------------------


def test_load_geojson_roundtrip(tmp_path, points_gdf):
    path = tmp_path / "pts.geojson"
    points_gdf.to_file(path, driver="GeoJSON")

    out = load(str(path))
    assert isinstance(out, gpd.GeoDataFrame)
    assert len(out) == 3
    assert list(out["name"]) == ["a", "b", "c"]


def test_load_geoparquet_roundtrip(tmp_path, points_gdf):
    path = tmp_path / "pts.parquet"
    points_gdf.to_parquet(path)

    out = load(str(path))
    assert isinstance(out, gpd.GeoDataFrame)
    assert len(out) == 3
    assert out.crs is not None


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(str(tmp_path / "nope.gpkg"))


def test_load_lazy_ignored_for_file(tmp_path, points_gdf):
    path = tmp_path / "pts.parquet"
    points_gdf.to_parquet(path)
    # lazy on a local file is a no-op: returns the eager GeoDataFrame.
    out = load(str(path), lazy=True)
    assert isinstance(out, gpd.GeoDataFrame)


# ---------------------------------------------------------------------------
# load — SourceResult normalisation
# ---------------------------------------------------------------------------


def test_load_sourceresult_with_geodataframe(points_gdf):
    result = SourceResult(payload=Payload.VECTOR, data=points_gdf, crs="EPSG:4326")
    out = load(result)
    assert out is points_gdf


def test_load_sourceresult_with_path(tmp_path, points_gdf):
    path = tmp_path / "pts.geojson"
    points_gdf.to_file(path, driver="GeoJSON")
    result = SourceResult(payload=Payload.VECTOR, data=str(path))
    out = load(result)
    assert isinstance(out, gpd.GeoDataFrame)
    assert len(out) == 3


def test_load_sourceresult_sets_crs_when_missing(points_gdf):
    bare = points_gdf.set_crs(None, allow_override=True)
    result = SourceResult(payload=Payload.VECTOR, data=bare, crs="EPSG:2154")
    out = load(result)
    assert out.crs is not None
    assert "2154" in str(out.crs)


# ---------------------------------------------------------------------------
# load — remote via a stub fetcher registry
# ---------------------------------------------------------------------------


def test_load_remote_materialize_returns_gdf(points_gdf, monkeypatch):
    monkeypatch.setenv("GISPULSE_FETCH_ALLOW_PRIVATE", "1")
    reg = ProtocolRegistry()
    result = SourceResult(payload=Payload.VECTOR, data=points_gdf)
    stub = _StubFetcher(AccessProtocol.OGC_FEATURES, result)
    reg.register(stub)

    out = load(
        {"protocol": "ogc-features", "endpoint": "https://host/collections/x"},
        registry=reg,
        bbox=(0, 0, 1, 1),
    )
    assert isinstance(out, gpd.GeoDataFrame)
    # bbox is forwarded to the fetcher as extent.
    assert stub.calls[0][1] == (0, 0, 1, 1)
    assert stub.calls[0][2] is FetchMode.MATERIALIZE


def test_load_remote_path_result_reads_file(tmp_path, points_gdf):
    path = tmp_path / "mat.geojson"
    points_gdf.to_file(path, driver="GeoJSON")
    reg = ProtocolRegistry()
    result = SourceResult(payload=Payload.VECTOR, data=str(path))
    # endpoint is a local path → SSRF guard skips it.
    stub = _StubFetcher(AccessProtocol.DOWNLOAD, result)
    reg.register(stub)

    out = load(
        AccessSpec(protocol=AccessProtocol.DOWNLOAD, endpoint=str(path)),
        registry=reg,
    )
    assert isinstance(out, gpd.GeoDataFrame)
    assert len(out) == 3


def test_load_lazy_returns_handle():
    reg = ProtocolRegistry()
    result = SourceResult(
        payload=Payload.VECTOR,
        mode=FetchMode.REFERENCE,
        data=None,
        crs="EPSG:4326",
        metadata={"duckdb_scan": "read_parquet('s3://b/x.parquet')"},
    )
    stub = _StubFetcher(AccessProtocol.REMOTE_TABLE, result)
    reg.register(stub)

    handle = load("s3://b/x.parquet", registry=reg, lazy=True)
    assert isinstance(handle, LazyDataset)
    assert handle.scan_sql == "read_parquet('s3://b/x.parquet')"
    assert handle.crs == "EPSG:4326"
    assert stub.calls[0][2] is FetchMode.REFERENCE


def test_load_lazy_falls_back_when_no_scan(points_gdf, monkeypatch):
    monkeypatch.setenv("GISPULSE_FETCH_ALLOW_PRIVATE", "1")
    # A protocol that cannot produce a lazy scan still yields data.
    reg = ProtocolRegistry()
    result = SourceResult(payload=Payload.VECTOR, data=points_gdf)
    stub = _StubFetcher(AccessProtocol.OGC_FEATURES, result)
    reg.register(stub)

    out = load(
        {"protocol": "ogc-features", "endpoint": "https://host/x"},
        registry=reg,
        lazy=True,
    )
    assert isinstance(out, gpd.GeoDataFrame)
