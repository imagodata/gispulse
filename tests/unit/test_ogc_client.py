"""Tests for the high-level OGC client (story T1, issue #267).

The transport itself (``adapters/ogc/wfs_client``) already has its own
fixture-based suite; here we only validate the data-pack-facing surface:
argument normalisation, protocol dispatch, network-error translation.
"""

from __future__ import annotations

from typing import Any

import pytest

from gispulse.core.fetchers import ogc_client


class _FakeGDF:
    """Stand-in for a GeoDataFrame — len() must work, nothing else."""

    def __init__(self, n: int = 3) -> None:
        self.n = n

    def __len__(self) -> int:
        return self.n


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_empty_endpoint_rejected() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        ogc_client.fetch_features(endpoint="", typename="x")


def test_empty_typename_rejected() -> None:
    with pytest.raises(ValueError, match="typename"):
        ogc_client.fetch_features(endpoint="http://x", typename="")


def test_unknown_protocol_rejected() -> None:
    with pytest.raises(ValueError, match="protocol"):
        ogc_client.fetch_features(
            endpoint="http://x", typename="y", protocol="rest"  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Protocol dispatch — same contract for WFS and OGC API Features
# ---------------------------------------------------------------------------


def test_wfs_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """``protocol="wfs"`` calls fetch_wfs, not fetch_ogc_api_features."""
    seen: dict[str, Any] = {}

    def _fake_wfs(cfg, bbox=None):
        seen["called"] = "wfs"
        seen["cfg"] = cfg
        seen["bbox"] = bbox
        return _FakeGDF()

    def _fake_oapif(cfg, bbox=None):
        seen["called"] = "oapif"
        return _FakeGDF()

    from gispulse.adapters.ogc import wfs_client

    monkeypatch.setattr(wfs_client, "fetch_wfs", _fake_wfs)
    monkeypatch.setattr(wfs_client, "fetch_ogc_api_features", _fake_oapif)

    gdf = ogc_client.fetch_features(
        endpoint="https://geo.example/wfs",
        typename="ns:layer",
        protocol="wfs",
        bbox=(1.0, 2.0, 3.0, 4.0),
        crs="EPSG:2154",
    )
    assert isinstance(gdf, _FakeGDF)
    assert seen["called"] == "wfs"
    cfg = seen["cfg"]
    assert cfg.source_type == "wfs"
    assert cfg.url == "https://geo.example/wfs"
    assert cfg.layer_name == "ns:layer"
    assert cfg.crs == "EPSG:2154"
    assert seen["bbox"] == (1.0, 2.0, 3.0, 4.0)


def test_oapif_dispatch_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``protocol`` argument defaults to OGC API Features."""
    seen: dict[str, Any] = {}

    def _fake_wfs(cfg, bbox=None):
        seen["called"] = "wfs"
        return _FakeGDF()

    def _fake_oapif(cfg, bbox=None):
        seen["called"] = "oapif"
        seen["cfg"] = cfg
        return _FakeGDF()

    from gispulse.adapters.ogc import wfs_client

    monkeypatch.setattr(wfs_client, "fetch_wfs", _fake_wfs)
    monkeypatch.setattr(wfs_client, "fetch_ogc_api_features", _fake_oapif)

    ogc_client.fetch_features(
        endpoint="https://geo.example",
        typename="land_use",
    )
    assert seen["called"] == "oapif"
    assert seen["cfg"].source_type == "ogc_api_features"


def test_query_params_flow_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _fake_oapif(cfg, bbox=None):
        seen["cfg"] = cfg
        return _FakeGDF()

    from gispulse.adapters.ogc import wfs_client

    monkeypatch.setattr(wfs_client, "fetch_ogc_api_features", _fake_oapif)

    ogc_client.fetch_features(
        endpoint="https://geo.example",
        typename="land_use",
        query={"filter": "type='A'", "limit": "200"},
        max_features=500,
    )
    cfg = seen["cfg"]
    assert cfg.params == {"filter": "type='A'", "limit": "200"}
    assert cfg.max_features == 500


# ---------------------------------------------------------------------------
# Network error translation — single typed surface, no httpx leak
# ---------------------------------------------------------------------------


class _ConnectError(Exception):
    """Mimic httpx.ConnectError without importing httpx in the test."""

    # Name matters: ``_is_unreachable`` matches by class name across MRO.

    def __init__(self, msg: str = "connect refused") -> None:
        super().__init__(msg)


# Reset the qualname so the MRO check sees ``ConnectError``.
_ConnectError.__name__ = "ConnectError"


def test_unreachable_endpoint_raises_typed_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(cfg, bbox=None):
        raise _ConnectError()

    from gispulse.adapters.ogc import wfs_client

    monkeypatch.setattr(wfs_client, "fetch_ogc_api_features", _boom)

    with pytest.raises(ogc_client.OGCEndpointUnreachable) as excinfo:
        ogc_client.fetch_features(
            endpoint="https://does-not-exist.invalid",
            typename="x",
        )
    # Original exception preserved as __cause__ so callers can drill in.
    assert isinstance(excinfo.value.__cause__, _ConnectError)


def test_unknown_transport_error_wrapped_as_ogcclient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(cfg, bbox=None):
        raise RuntimeError("parser exploded")

    from gispulse.adapters.ogc import wfs_client

    monkeypatch.setattr(wfs_client, "fetch_ogc_api_features", _boom)

    with pytest.raises(ogc_client.OGCClientError) as excinfo:
        ogc_client.fetch_features(
            endpoint="https://geo.example", typename="x"
        )
    assert "parser exploded" in str(excinfo.value)
    # Not classified as "unreachable" — it's a content-level failure.
    assert not isinstance(excinfo.value, ogc_client.OGCEndpointUnreachable)


def test_stdlib_timeout_classified_as_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stdlib ``TimeoutError`` is unreachable too — covers retry-less callers."""

    def _boom(cfg, bbox=None):
        raise TimeoutError("read timeout")

    from gispulse.adapters.ogc import wfs_client

    monkeypatch.setattr(wfs_client, "fetch_ogc_api_features", _boom)

    with pytest.raises(ogc_client.OGCEndpointUnreachable):
        ogc_client.fetch_features(
            endpoint="https://geo.example", typename="x"
        )


def test_value_errors_in_transport_are_not_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad arguments raised by the transport surface as OGCClientError, not unreachable."""

    def _boom(cfg, bbox=None):
        raise ValueError("invalid bbox")

    from gispulse.adapters.ogc import wfs_client

    monkeypatch.setattr(wfs_client, "fetch_ogc_api_features", _boom)

    with pytest.raises(ogc_client.OGCClientError) as excinfo:
        ogc_client.fetch_features(
            endpoint="https://geo.example", typename="x"
        )
    assert not isinstance(excinfo.value, ogc_client.OGCEndpointUnreachable)
