"""Tests for the WFS fetcher absorbed into the ProtocolRegistry (#192)."""

from __future__ import annotations

import pytest

from core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
)
from core.sources import ProtocolNotSupported, ProtocolRegistry
from gispulse.adapters.ogc.wfs_fetcher import (
    WfsFetcher,
    _bbox_from_extent,
    register_wfs_fetcher,
)


class _FakeGDF:
    """Stand-in GeoDataFrame — the fetcher only calls len() on it."""

    def __init__(self, n: int) -> None:
        self._n = n

    def __len__(self) -> int:
        return self._n


@pytest.fixture
def mock_fetch_wfs(monkeypatch: pytest.MonkeyPatch) -> list:
    """Patch wfs_client.fetch_wfs; return the list of (cfg, kwargs) calls."""
    calls: list = []

    def fake(cfg, *, bbox=None, cql_filter=None, **_kw):
        calls.append({"cfg": cfg, "bbox": bbox, "cql_filter": cql_filter})
        return _FakeGDF(7)

    monkeypatch.setattr(
        "gispulse.adapters.ogc.wfs_client.fetch_wfs", fake
    )
    return calls


# ---------------------------------------------------------------------------
# _bbox_from_extent
# ---------------------------------------------------------------------------


def test_bbox_from_extent_valid() -> None:
    assert _bbox_from_extent((1, 2, 3, 4)) == (1.0, 2.0, 3.0, 4.0)


@pytest.mark.parametrize("bad", [None, (1, 2, 3), (1, 2, 3, 4, 5), "nope", 42])
def test_bbox_from_extent_rejects_non_bbox(bad: object) -> None:
    assert _bbox_from_extent(bad) is None


# ---------------------------------------------------------------------------
# WfsFetcher.fetch
# ---------------------------------------------------------------------------


def test_fetcher_declares_wfs_protocol() -> None:
    assert WfsFetcher.protocol is AccessProtocol.WFS


def test_fetch_returns_vector_source_result(mock_fetch_wfs: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint="https://data.geopf.fr/wfs/ows",
        params={"typename": "CADASTRE:parcelle", "crs": "EPSG:2154"},
    )
    result = WfsFetcher().fetch(access)

    assert result.payload is Payload.VECTOR
    assert len(result.data) == 7
    assert result.crs == "EPSG:2154"
    assert result.metadata["feature_count"] == 7


def test_fetch_maps_access_to_ogc_config(mock_fetch_wfs: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint="https://wfs.example.org/ows",
        params={"typename": "ns:layer", "version": "1.1.0", "sortBy": "id"},
    )
    WfsFetcher().fetch(access, extent=(0, 0, 10, 10))

    call = mock_fetch_wfs[0]
    cfg = call["cfg"]
    assert cfg.url == "https://wfs.example.org/ows"
    assert cfg.layer_name == "ns:layer"
    assert cfg.version == "1.1.0"
    assert call["bbox"] == (0.0, 0.0, 10.0, 10.0)
    # Non-reserved params are forwarded verbatim as vendor params.
    assert cfg.params == {"sortBy": "id"}


def test_fetch_without_typename_raises(mock_fetch_wfs: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint="https://wfs.example.org/ows",
        params={},
    )
    with pytest.raises(ValueError, match="must declare a 'typename'"):
        WfsFetcher().fetch(access)


def test_fetch_forwards_cql_filter(mock_fetch_wfs: list) -> None:
    access = AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint="https://wfs.example.org/ows",
        params={"typename": "ns:layer", "cql_filter": "pop > 1000"},
    )
    WfsFetcher().fetch(access)
    assert mock_fetch_wfs[0]["cql_filter"] == "pop > 1000"


# ---------------------------------------------------------------------------
# Registration + dispatch
# ---------------------------------------------------------------------------


def test_register_wfs_fetcher_into_fresh_registry() -> None:
    reg = ProtocolRegistry()
    with pytest.raises(ProtocolNotSupported):
        reg.get_fetcher(AccessProtocol.WFS)

    register_wfs_fetcher(reg)
    assert isinstance(reg.get_fetcher(AccessProtocol.WFS), WfsFetcher)


def test_register_wfs_fetcher_is_idempotent() -> None:
    reg = ProtocolRegistry()
    register_wfs_fetcher(reg)
    register_wfs_fetcher(reg)  # second call must not raise on a taken slot
    assert isinstance(reg.get_fetcher(AccessProtocol.WFS), WfsFetcher)


def test_wfs_fetcher_self_registered_in_global_protocols() -> None:
    """Importing the module wired the fetcher into the shared registry."""
    from core.sources import PROTOCOLS

    assert isinstance(PROTOCOLS.get_fetcher(AccessProtocol.WFS), WfsFetcher)


def test_dispatch_fetch_routes_to_wfs_fetcher(mock_fetch_wfs: list) -> None:
    """End-to-end: a WFS AccessSpec dispatches through the registry."""
    reg = ProtocolRegistry()
    register_wfs_fetcher(reg)
    access = AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint="https://data.geopf.fr/wfs/ows",
        params={"typename": "CADASTRE:parcelle"},
    )
    result = reg.dispatch_fetch(access, mode=FetchMode.MATERIALIZE)
    assert result.payload is Payload.VECTOR
    assert len(mock_fetch_wfs) == 1
