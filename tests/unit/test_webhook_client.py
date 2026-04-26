"""Tests for gispulse.adapters.webhooks.HttpWebhookClient.

Covers the SSRF policy, HMAC signing, retry semantics and the structured
WebhookSecurityError / WebhookDeliveryError raised on failure.

Network is fully mocked via ``httpx.MockTransport`` so the test suite
remains hermetic — no DNS, no sockets, no listening ports.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Iterator

import httpx
import pytest

from gispulse.adapters.webhooks import (
    HttpWebhookClient,
    WebhookDeliveryError,
    WebhookSecurityError,
)
from gispulse.adapters.webhooks import http_client as _module


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _client(transport: httpx.MockTransport, **kwargs) -> HttpWebhookClient:
    """Build a client whose underlying httpx.Client uses *transport*."""
    return HttpWebhookClient(
        client=httpx.Client(transport=transport),
        # Resolve any hostname to a public IP so SSRF check passes
        # (overridable per test).
        allow_private_ips=kwargs.pop("allow_private_ips", True),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """Capture sleep durations without slowing tests down."""
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(_module.time, "sleep", sleeps.append)
    yield sleeps


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestSuccessfulDelivery:
    def test_2xx_succeeds_first_attempt(self):
        received: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            received.append(request)
            return httpx.Response(200, json={"ok": True})

        c = _client(httpx.MockTransport(handler))
        c.post("https://api.example.com/hook", {"event_type": "trigger_fired"})

        assert len(received) == 1
        body = json.loads(received[0].content)
        assert body == {"event_type": "trigger_fired"}
        assert received[0].headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# Retry semantics
# ---------------------------------------------------------------------------


class TestRetries:
    def test_5xx_retried_then_recovers(self, _no_real_sleep: list[float]):
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(503 if len(attempts) < 2 else 200)

        c = _client(httpx.MockTransport(handler))
        c.post("https://api.example.com/hook", {"x": 1})

        assert len(attempts) == 2
        assert _no_real_sleep == [1.0]  # one back-off

    def test_5xx_persistent_raises_delivery_error(self, _no_real_sleep: list[float]):
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(500)

        c = _client(httpx.MockTransport(handler), max_retries=2)
        with pytest.raises(WebhookDeliveryError):
            c.post("https://api.example.com/hook", {"x": 1})

        assert len(attempts) == 3  # 1 initial + 2 retries
        assert _no_real_sleep == [1.0, 3.0]

    def test_4xx_not_retried(self, _no_real_sleep: list[float]):
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(400)

        c = _client(httpx.MockTransport(handler))
        with pytest.raises(WebhookDeliveryError):
            c.post("https://api.example.com/hook", {"x": 1})

        assert len(attempts) == 1
        assert _no_real_sleep == []  # no back-off on 4xx

    def test_connect_timeout_retried(self, _no_real_sleep: list[float]):
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 2:
                raise httpx.ConnectTimeout("simulated")
            return httpx.Response(200)

        c = _client(httpx.MockTransport(handler))
        c.post("https://api.example.com/hook", {"x": 1})

        assert len(attempts) == 2


# ---------------------------------------------------------------------------
# SSRF policy
# ---------------------------------------------------------------------------


class TestSSRFPolicy:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com/hook",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "",
        ],
    )
    def test_unsupported_scheme_or_empty_rejected(self, url: str):
        c = HttpWebhookClient(allow_private_ips=False)
        with pytest.raises(WebhookSecurityError):
            c.post(url, {"x": 1})

    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",      # loopback
            "10.0.0.1",       # RFC1918 A
            "192.168.1.42",   # RFC1918 C
            "172.16.0.1",     # RFC1918 B
            "169.254.169.254",  # link-local (cloud metadata!)
            "0.0.0.0",        # unspecified
        ],
    )
    def test_blocked_addresses_rejected(self, host: str):
        c = HttpWebhookClient(allow_private_ips=False)
        with pytest.raises(WebhookSecurityError, match="blocked"):
            c.post(f"http://{host}/hook", {"x": 1})

    def test_allow_private_ips_opt_in_bypasses_blocklist(self):
        # Use MockTransport so we don't actually hit 127.0.0.1
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        c = HttpWebhookClient(
            allow_private_ips=True,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        c.post("http://127.0.0.1:9999/hook", {"x": 1})  # no raise


# ---------------------------------------------------------------------------
# HMAC signing
# ---------------------------------------------------------------------------


class TestHmacSigning:
    def test_signs_when_secret_provided(self):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200)

        secret = "super-secret"
        c = _client(httpx.MockTransport(handler), signing_secret=secret)
        c.post("https://api.example.com/hook", {"x": 1})

        sig = captured[0].headers.get("X-GISPulse-Signature")
        assert sig is not None and sig.startswith("sha256=")

        body = captured[0].content
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert sig == f"sha256={expected}"

    def test_no_signature_when_secret_absent(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("GISPULSE_WEBHOOK_SIGNING_SECRET", raising=False)
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200)

        c = _client(httpx.MockTransport(handler))
        c.post("https://api.example.com/hook", {"x": 1})

        assert "X-GISPulse-Signature" not in captured[0].headers

    def test_env_var_fallback(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GISPULSE_WEBHOOK_SIGNING_SECRET", "env-secret")
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200)

        c = _client(httpx.MockTransport(handler))
        c.post("https://api.example.com/hook", {"x": 1})

        assert captured[0].headers["X-GISPulse-Signature"].startswith("sha256=")
