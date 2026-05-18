"""Unit tests for A5 (#231) — ``STACFetcher`` (the v1.9.0 core adapter).

Distinct from ``test_stac_fetcher.py``, which covers the older #192
``adapters.stac`` fetcher. Zero network: ``STACClient`` is monkey-patched
with a recording fake.
"""

from __future__ import annotations

import pytest

from gispulse.core.fetchers import DUCKDB_SCAN_KEY
from gispulse.core.fetchers.stac import STACFetcher
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

_ITEMS = [
    {
        "id": "scene-1",
        "assets": {
            "B04": {"href": "https://stac.example.org/scene-1/B04.tif"},
            "visual": {"href": "https://stac.example.org/scene-1/visual.tif"},
        },
    }
]


def _access(endpoint: str, **params: object) -> AccessSpec:
    return AccessSpec(
        protocol=AccessProtocol.STAC, endpoint=endpoint, params=params
    )


@pytest.fixture
def fake_stac(monkeypatch: pytest.MonkeyPatch) -> list:
    """Patch STACClient — returns the recorded search/download call list."""
    calls: list = []

    class _FakeClient:
        def __init__(self, url: str, timeout: int = 30) -> None:
            calls.append({"op": "init", "url": url})

        def search(self, *, bbox, datetime, collections, limit=10, query=None):
            calls.append(
                {
                    "op": "search",
                    "bbox": bbox,
                    "datetime": datetime,
                    "collections": collections,
                    "limit": limit,
                    "query": query,
                }
            )
            return _ITEMS

        def download_asset(self, item, asset_key, output_dir, overwrite=False):
            calls.append(
                {"op": "download", "asset": asset_key, "dir": output_dir}
            )
            return f"{output_dir}/{asset_key}.tif"

    import gispulse.catalog.providers.stac_client as stac_client

    monkeypatch.setattr(stac_client, "STACClient", _FakeClient)
    return calls


# -- contract ---------------------------------------------------------------


def test_protocol_and_payload() -> None:
    assert STACFetcher.protocol is AccessProtocol.STAC
    assert STACFetcher.payload is Payload.RASTER


# -- lazy reference ---------------------------------------------------------


def test_reference_yields_cog_asset_href(fake_stac: list) -> None:
    result = STACFetcher().virtual_table(
        _access("https://stac.example.org", collections=["sentinel-2"])
    )
    # The COG href is the load-bearing REFERENCE value.
    assert result.metadata[DUCKDB_SCAN_KEY] == (
        "https://stac.example.org/scene-1/visual.tif"
    )
    assert result.mode is FetchMode.REFERENCE
    assert result.payload is Payload.RASTER


def test_reference_pushdown_bbox_into_stac_search(fake_stac: list) -> None:
    STACFetcher().virtual_table(
        _access("https://stac.example.org", collections=["sentinel-2"]),
        extent=(1.0, 2.0, 3.0, 4.0),
    )
    search = next(c for c in fake_stac if c["op"] == "search")
    # STAC pushdown: the bbox is handed straight to the catalog search.
    assert search["bbox"] == [1.0, 2.0, 3.0, 4.0]


def test_reference_no_extent_searches_worldwide(fake_stac: list) -> None:
    STACFetcher().virtual_table(
        _access("https://stac.example.org", collections=["sentinel-2"])
    )
    search = next(c for c in fake_stac if c["op"] == "search")
    assert search["bbox"] == [-180.0, -90.0, 180.0, 90.0]


def test_reference_custom_asset_key(fake_stac: list) -> None:
    result = STACFetcher().virtual_table(
        _access(
            "https://stac.example.org",
            collections=["sentinel-2"],
            asset="B04",
        )
    )
    assert result.metadata[DUCKDB_SCAN_KEY].endswith("/B04.tif")


def test_missing_collections_raises(fake_stac: list) -> None:
    with pytest.raises(ValueError, match="collections"):
        STACFetcher().virtual_table(_access("https://stac.example.org"))


# -- materialise ------------------------------------------------------------


def test_fetch_materialize_downloads_asset(fake_stac: list) -> None:
    result = STACFetcher().fetch(
        _access(
            "https://stac.example.org",
            collections=["sentinel-2"],
            asset="visual",
            output_dir="/tmp/stac-out",
        ),
        mode=FetchMode.MATERIALIZE,
    )
    assert result.mode is FetchMode.MATERIALIZE
    assert result.data == "/tmp/stac-out/visual.tif"
    download = next(c for c in fake_stac if c["op"] == "download")
    assert download["asset"] == "visual"


# -- SSRF guard -------------------------------------------------------------


@pytest.mark.parametrize("mode", [FetchMode.REFERENCE, FetchMode.MATERIALIZE])
def test_fetch_rejects_private_address(mode: FetchMode) -> None:
    with pytest.raises(SSRFError):
        STACFetcher().fetch(
            _access("http://127.0.0.1/stac", collections=["x"]), mode=mode
        )
