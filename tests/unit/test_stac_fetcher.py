"""Tests for the STAC fetcher absorbed into the ProtocolRegistry (#192)."""

from __future__ import annotations

import pytest

from gispulse.core.plugin_model import AccessProtocol, AccessSpec, FetchMode, Payload
from gispulse.core.sources import ProtocolNotSupported, ProtocolRegistry
from gispulse.adapters.stac.stac_fetcher import (
    StacFetcher,
    _bbox_from_extent,
    register_stac_fetcher,
)

_ITEMS = [
    {
        "id": "scene-1",
        "assets": {
            "B04": {"href": "https://stac.example.org/scene-1/B04.tif"},
            "visual": {"href": "https://stac.example.org/scene-1/visual.tif"},
        },
    },
    {
        "id": "scene-2",
        "assets": {"B04": {"href": "https://stac.example.org/scene-2/B04.tif"}},
    },
]


@pytest.fixture
def mock_stac(monkeypatch: pytest.MonkeyPatch) -> list:
    """Patch STACClient; return [init_kwargs, search_kwargs] call records."""
    calls: list = []

    class _FakeClient:
        def __init__(self, url: str, timeout: int = 30) -> None:
            calls.append({"url": url, "timeout": timeout})

        def search(self, *, bbox, datetime, collections, limit=10, query=None):
            calls.append(
                {
                    "bbox": bbox,
                    "datetime": datetime,
                    "collections": collections,
                    "limit": limit,
                    "query": query,
                }
            )
            return list(_ITEMS)

    monkeypatch.setattr(
        "gispulse.catalog.providers.stac_client.STACClient", _FakeClient
    )
    return calls


# ---------------------------------------------------------------------------
# _bbox_from_extent
# ---------------------------------------------------------------------------


def test_bbox_from_extent_valid() -> None:
    assert _bbox_from_extent((1, 2, 3, 4)) == [1.0, 2.0, 3.0, 4.0]


@pytest.mark.parametrize("bad", [None, (1, 2, 3), (1, 2, 3, 4, 5), "nope", 42])
def test_bbox_from_extent_rejects_non_bbox(bad: object) -> None:
    assert _bbox_from_extent(bad) is None


# ---------------------------------------------------------------------------
# StacFetcher.fetch
# ---------------------------------------------------------------------------


def test_fetcher_declares_stac_protocol() -> None:
    assert StacFetcher.protocol is AccessProtocol.STAC


def test_fetch_returns_reference_result(mock_stac: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.STAC,
        endpoint="https://stac.example.org/v1",
        params={"collection": "sentinel-2-l2a"},
    )
    result = StacFetcher().fetch(access)

    assert result.payload is Payload.RASTER
    # A STAC search resolves to references — never a materialised raster.
    assert result.mode is FetchMode.REFERENCE
    assert result.reference == "https://stac.example.org/scene-1/B04.tif"
    assert result.metadata["item_count"] == 2
    assert len(result.metadata["asset_hrefs"]) == 2


def test_fetch_records_requested_mode(mock_stac: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.STAC,
        endpoint="https://stac.example.org/v1",
        params={"collection": "sentinel-2-l2a"},
    )
    result = StacFetcher().fetch(access, mode=FetchMode.MATERIALIZE)
    # The result stays REFERENCE, but the caller's intent is kept visible.
    assert result.mode is FetchMode.REFERENCE
    assert result.metadata["requested_mode"] == "materialize"


def test_fetch_without_collection_raises() -> None:
    access = AccessSpec(
        protocol=AccessProtocol.STAC,
        endpoint="https://stac.example.org/v1",
        params={},
    )
    with pytest.raises(ValueError, match="must declare a 'collection'"):
        StacFetcher().fetch(access)


def test_collections_list_is_forwarded(mock_stac: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.STAC,
        endpoint="https://stac.example.org/v1",
        params={"collections": ["landsat-c2-l2", "sentinel-2-l2a"]},
    )
    StacFetcher().fetch(access)
    assert mock_stac[1]["collections"] == ["landsat-c2-l2", "sentinel-2-l2a"]


def test_preferred_asset_selects_href(mock_stac: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.STAC,
        endpoint="https://stac.example.org/v1",
        params={"collection": "sentinel-2-l2a", "asset": "visual"},
    )
    result = StacFetcher().fetch(access)
    # scene-1 has 'visual'; scene-2 has only 'B04' — it falls back.
    assert result.reference == "https://stac.example.org/scene-1/visual.tif"
    assert result.metadata["asset_hrefs"] == [
        "https://stac.example.org/scene-1/visual.tif",
        "https://stac.example.org/scene-2/B04.tif",
    ]


def test_extent_becomes_search_bbox(mock_stac: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.STAC,
        endpoint="https://stac.example.org/v1",
        params={"collection": "sentinel-2-l2a"},
    )
    StacFetcher().fetch(access, extent=(0, 0, 10, 10))
    assert mock_stac[1]["bbox"] == [0.0, 0.0, 10.0, 10.0]


def test_default_world_bbox_without_extent(mock_stac: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.STAC,
        endpoint="https://stac.example.org/v1",
        params={"collection": "sentinel-2-l2a"},
    )
    StacFetcher().fetch(access)
    assert mock_stac[1]["bbox"] == [-180.0, -90.0, 180.0, 90.0]


def test_timeout_param_reaches_client(mock_stac: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.STAC,
        endpoint="https://stac.example.org/v1",
        params={"collection": "sentinel-2-l2a", "timeout": 8},
    )
    StacFetcher().fetch(access)
    assert mock_stac[0]["timeout"] == 8


# ---------------------------------------------------------------------------
# Registration + dispatch
# ---------------------------------------------------------------------------


def test_register_stac_fetcher_into_fresh_registry() -> None:
    reg = ProtocolRegistry()
    with pytest.raises(ProtocolNotSupported):
        reg.get_fetcher(AccessProtocol.STAC)

    register_stac_fetcher(reg)
    assert isinstance(reg.get_fetcher(AccessProtocol.STAC), StacFetcher)


def test_register_stac_fetcher_is_idempotent() -> None:
    reg = ProtocolRegistry()
    register_stac_fetcher(reg)
    register_stac_fetcher(reg)  # second call must not raise on a taken slot
    assert isinstance(reg.get_fetcher(AccessProtocol.STAC), StacFetcher)


def test_stac_fetcher_self_registered_in_global_protocols() -> None:
    """Importing the module wired the fetcher into the shared registry."""
    from gispulse.core.sources import PROTOCOLS

    assert isinstance(PROTOCOLS.get_fetcher(AccessProtocol.STAC), StacFetcher)


def test_dispatch_fetch_routes_to_stac_fetcher(mock_stac: list) -> None:
    """End-to-end: a STAC AccessSpec dispatches through the registry."""
    reg = ProtocolRegistry()
    register_stac_fetcher(reg)
    access = AccessSpec(
        protocol=AccessProtocol.STAC,
        endpoint="https://earth-search.aws.element84.com/v1",
        params={"collection": "sentinel-2-l2a"},
    )
    result = reg.dispatch_fetch(access, mode=FetchMode.MATERIALIZE)
    assert result.payload is Payload.RASTER
    assert len(mock_stac) == 2  # one __init__, one search
