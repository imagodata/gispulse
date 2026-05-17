"""Tests for the SSRF guard on outbound fetches (issue #199)."""

from __future__ import annotations

import pytest

from core.ssrf import SSRFError, guard_outbound_url, is_blocked_address


# ---------------------------------------------------------------------------
# is_blocked_address
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "addr",
    ["10.0.0.1", "192.168.1.1", "127.0.0.1", "169.254.169.254", "::1", "not-an-ip"],
)
def test_blocked_addresses(addr: str) -> None:
    assert is_blocked_address(addr) is True


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
def test_public_addresses_allowed(addr: str) -> None:
    assert is_blocked_address(addr) is False


# ---------------------------------------------------------------------------
# guard_outbound_url — literal-IP URLs need no DNS
# ---------------------------------------------------------------------------


def test_guard_allows_public_ip() -> None:
    guard_outbound_url("https://8.8.8.8/wfs")  # no raise


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/wfs",
        "http://192.168.0.5/data",
        "http://127.0.0.1:8000/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    ],
)
def test_guard_blocks_internal_targets(url: str) -> None:
    with pytest.raises(SSRFError, match="blocked address"):
        guard_outbound_url(url)


def test_guard_skips_non_http_endpoints() -> None:
    """Local file paths / file:// URIs are not network targets."""
    guard_outbound_url("/var/data/layer.gpkg")
    guard_outbound_url("file:///var/data/layer.gpkg")
    guard_outbound_url(None)
    guard_outbound_url("")


def test_guard_allows_private_with_explicit_flag() -> None:
    guard_outbound_url("http://10.0.0.1/wfs", allow_private=True)  # no raise


def test_guard_respects_env_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GISPULSE_FETCH_ALLOW_PRIVATE", "1")
    guard_outbound_url("http://10.0.0.1/wfs")  # no raise


def test_guard_rejects_url_without_hostname() -> None:
    with pytest.raises(SSRFError, match="no hostname"):
        guard_outbound_url("http://")


# ---------------------------------------------------------------------------
# Integration — ProtocolRegistry.dispatch_fetch is guarded
# ---------------------------------------------------------------------------


def test_dispatch_fetch_blocks_private_endpoint() -> None:
    from core.plugin_model import AccessProtocol, AccessSpec, FetchMode, Payload, SourceResult
    from core.sources import ProtocolRegistry

    called: list = []

    class FakeWFS:
        protocol = AccessProtocol.WFS

        def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
            called.append(access)
            return SourceResult(payload=Payload.VECTOR, mode=mode, data=None)

    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    access = AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint="http://169.254.169.254/wfs",
        params={},
        format="application/json",
    )
    with pytest.raises(SSRFError):
        reg.dispatch_fetch(access)
    assert called == []  # fetcher never reached


def test_dispatch_fetch_allows_public_endpoint() -> None:
    from core.plugin_model import AccessProtocol, AccessSpec, FetchMode, Payload, SourceResult
    from core.sources import ProtocolRegistry

    class FakeWFS:
        protocol = AccessProtocol.WFS

        def fetch(self, access, *, extent=None, mode=FetchMode.MATERIALIZE):
            return SourceResult(payload=Payload.VECTOR, mode=mode, data="ok")

    reg = ProtocolRegistry()
    reg.register(FakeWFS())
    access = AccessSpec(
        protocol=AccessProtocol.WFS,
        endpoint="https://8.8.8.8/wfs",
        params={},
        format="application/json",
    )
    assert reg.dispatch_fetch(access).data == "ok"
