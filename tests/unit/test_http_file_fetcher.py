"""Unit tests for A6 (#232) — ``HttpFileFetcher``.

Zero network: the lazy path is pure SQL/URL string-building; the
materialise path's only network actor (``httpx.stream``) is monkey-
patched with a recording fake.
"""

from __future__ import annotations

import pytest

from gispulse.core.fetchers import DUCKDB_SCAN_KEY
from gispulse.core.fetchers.http_file import HttpFileFetcher
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
)
from gispulse.core.ssrf import SSRFError

# offline_ssrf (tests/unit/conftest.py) keeps the SSRF guard's DNS
# resolution off the network — CI does zero network.
pytestmark = pytest.mark.usefixtures("offline_ssrf")


def _access(endpoint: str, **params: object) -> AccessSpec:
    return AccessSpec(
        protocol=AccessProtocol.DOWNLOAD, endpoint=endpoint, params=params
    )


# -- contract ---------------------------------------------------------------


def test_protocol_and_payload() -> None:
    assert HttpFileFetcher.protocol is AccessProtocol.DOWNLOAD
    assert HttpFileFetcher.payload is Payload.VECTOR


# -- lazy scan: spatial files ----------------------------------------------


def test_reference_scan_geojson_via_vsicurl_st_read() -> None:
    result = HttpFileFetcher().virtual_table(
        _access("https://host.example.org/cities.geojson")
    )
    assert result.metadata[DUCKDB_SCAN_KEY] == (
        "ST_Read('/vsicurl/https://host.example.org/cities.geojson')"
    )
    assert result.mode is FetchMode.REFERENCE


def test_reference_scan_gpkg_with_layer() -> None:
    result = HttpFileFetcher().virtual_table(
        _access("https://host.example.org/data.gpkg", layer="parcels")
    )
    scan = result.metadata[DUCKDB_SCAN_KEY]
    assert "/vsicurl/https://host.example.org/data.gpkg" in scan
    assert "layer='parcels'" in scan


def test_reference_scan_pushdown_bbox_for_spatial_file() -> None:
    result = HttpFileFetcher().virtual_table(
        _access("https://host.example.org/cities.geojson"),
        extent=(1.0, 2.0, 3.0, 4.0),
    )
    scan = result.metadata[DUCKDB_SCAN_KEY]
    assert scan.startswith("(SELECT * FROM ST_Read(")
    assert "ST_Intersects(geom, ST_MakeEnvelope(1.0, 2.0, 3.0, 4.0))" in scan


# -- lazy scan: point CSV ---------------------------------------------------


def test_reference_scan_csv_builds_st_point_geometry() -> None:
    result = HttpFileFetcher().virtual_table(
        _access(
            "https://host.example.org/sites.csv", lat="lat", lon="lng"
        )
    )
    scan = result.metadata[DUCKDB_SCAN_KEY]
    assert 'read_csv_auto(' in scan
    assert 'ST_Point("lng", "lat") AS geometry' in scan


def test_reference_scan_csv_default_lat_lon_columns() -> None:
    result = HttpFileFetcher().virtual_table(
        _access("https://host.example.org/sites.csv")
    )
    assert 'ST_Point("longitude", "latitude")' in result.metadata[DUCKDB_SCAN_KEY]


def test_reference_scan_csv_pushdown_bbox() -> None:
    result = HttpFileFetcher().virtual_table(
        _access("https://host.example.org/sites.csv"),
        extent=(0.0, 0.0, 5.0, 5.0),
    )
    scan = result.metadata[DUCKDB_SCAN_KEY]
    assert "ST_Intersects(" in scan
    assert "ST_MakeEnvelope(0.0, 0.0, 5.0, 5.0)" in scan


def test_reference_scan_local_path_not_vsicurl_wrapped() -> None:
    result = HttpFileFetcher().virtual_table(_access("/tmp/local.geojson"))
    assert result.metadata[DUCKDB_SCAN_KEY] == "ST_Read('/tmp/local.geojson')"


# -- mode dispatch ----------------------------------------------------------


def test_fetch_materialize_streams_file_to_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield b"chunk-1"
            yield b"chunk-2"

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _fake_stream(method: str, url: str, **kw: object) -> _FakeResponse:
        assert method == "GET"
        return _FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "stream", _fake_stream)

    dest = str(tmp_path / "out.geojson")  # type: ignore[operator]
    result = HttpFileFetcher().fetch(
        _access("https://host.example.org/cities.geojson", local_path=dest),
        mode=FetchMode.MATERIALIZE,
    )
    assert result.mode is FetchMode.MATERIALIZE
    assert result.data == dest
    with open(dest, "rb") as fh:
        assert fh.read() == b"chunk-1chunk-2"


# -- SSRF guard -------------------------------------------------------------


@pytest.mark.parametrize("mode", [FetchMode.REFERENCE, FetchMode.MATERIALIZE])
def test_fetch_rejects_private_address(mode: FetchMode) -> None:
    with pytest.raises(SSRFError):
        HttpFileFetcher().fetch(
            _access("http://127.0.0.1/data.geojson"), mode=mode
        )
