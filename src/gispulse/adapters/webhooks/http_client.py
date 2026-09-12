"""SSRF-safe HTTP webhook client for outbound trigger actions.

Used by :class:`gispulse.adapters.esb.action_dispatcher.ActionDispatcher`
to deliver ``ActionType.WEBHOOK`` events. Built on top of ``httpx`` (already
a dependency of the ``[api]`` extra).

Security guarantees
-------------------
- Only ``http://`` and ``https://`` schemes are accepted.
- The destination host is resolved and every returned address is checked
  against an SSRF blocklist (RFC1918 private ranges, loopback, link-local,
  multicast, reserved). ``allow_private_ips=True`` opt-in is required to
  reach internal infrastructure (CI, dev fixtures).
- Optional HMAC-SHA256 signature is added in the ``X-GISPulse-Signature``
  header when ``GISPULSE_WEBHOOK_SIGNING_SECRET`` is set in the environment.

Retries
-------
- Idempotent: only ``POST`` retries on 5xx + connect/read timeout (max 2
  retries by default, exponential 1 s / 3 s). 4xx is *not* retried.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import socket
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from gispulse.core.logging import get_logger

log = get_logger(__name__)


_SIGNING_SECRET_ENV = "GISPULSE_WEBHOOK_SIGNING_SECRET"
_SIGNATURE_HEADER = "X-GISPulse-Signature"
_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 3.0)


class WebhookSecurityError(RuntimeError):
    """Raised when a webhook target violates the SSRF policy."""


class WebhookDeliveryError(RuntimeError):
    """Raised when a webhook POST fails after exhausting retries."""


class HttpWebhookClient:
    """Send JSON payloads to user-configured webhook URLs.

    Args:
        timeout:           Per-attempt HTTP timeout in seconds (connect+read).
        max_retries:       Number of retries on 5xx / network failures.
                           Capped at ``len(_RETRY_BACKOFF_SECONDS)``.
        allow_private_ips: When *False* (default), reject any URL whose host
                           resolves to a private/loopback/link-local address.
        signing_secret:    Optional HMAC-SHA256 secret. If *None*, falls
                           back to ``GISPULSE_WEBHOOK_SIGNING_SECRET``.
        client:            Optional pre-built ``httpx.Client`` (DI hook for
                           tests using ``respx`` or a mock transport).
    """

    def __init__(
        self,
        *,
        timeout: float = 5.0,
        max_retries: int = 2,
        allow_private_ips: bool = False,
        signing_secret: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._timeout = float(timeout)
        self._max_retries = min(int(max_retries), len(_RETRY_BACKOFF_SECONDS))
        self._allow_private_ips = bool(allow_private_ips)
        self._signing_secret = signing_secret if signing_secret is not None else os.environ.get(
            _SIGNING_SECRET_ENV
        )
        self._client = client

    # ------------------------------------------------------------------
    # Public dispatcher entry point
    # ------------------------------------------------------------------

    def post(self, url: str, payload: dict[str, Any]) -> None:
        """Deliver *payload* as JSON to *url*. SSRF-checked + retried.

        Designed to be assigned to
        :class:`ActionDispatcher`'s ``webhook_client`` attribute::

            dispatcher = ActionDispatcher(
                webhook_client=HttpWebhookClient().post,
                ...
            )

        Raises:
            WebhookSecurityError: URL fails the SSRF / scheme policy.
            WebhookDeliveryError: 5xx or network error persists after retries.
        """
        pinned_ip = self._validate_target(url)
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        signature = self._sign(body)
        if signature is not None:
            headers[_SIGNATURE_HEADER] = signature

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._send(url, body, headers, pinned_ip)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                last_exc = exc
                self._sleep_for_retry(attempt)
                continue

            status = response.status_code
            if 200 <= status < 300:
                log.info(
                    "webhook_delivered",
                    url=_redact(url),
                    status=status,
                    attempt=attempt + 1,
                )
                return
            if 400 <= status < 500:
                log.warning(
                    "webhook_4xx_no_retry",
                    url=_redact(url),
                    status=status,
                )
                raise WebhookDeliveryError(
                    f"Webhook target rejected payload with HTTP {status}"
                )
            # 5xx: retry
            last_exc = WebhookDeliveryError(
                f"Webhook target returned HTTP {status}"
            )
            self._sleep_for_retry(attempt)

        log.error("webhook_delivery_failed", url=_redact(url), error=str(last_exc))
        raise WebhookDeliveryError(
            f"Webhook delivery to {_redact(url)} failed after "
            f"{self._max_retries + 1} attempts: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _send(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        pinned_ip: str | None = None,
    ) -> httpx.Response:
        # When a client is injected (tests / DI) we honour the original URL so
        # the supplied transport sees the request as configured.
        if self._client is not None:
            return self._client.post(url, content=body, headers=headers, timeout=self._timeout)

        # No validated IP to pin to (allow_private_ips, or DNS-literal
        # fallback): fall back to letting httpx resolve the hostname itself.
        if pinned_ip is None:
            with httpx.Client(timeout=self._timeout) as client:
                return client.post(url, content=body, headers=headers)

        # SSRF / DNS-rebinding hardening: connect to the *exact* IP that was
        # validated against the blocklist, eliminating the TOCTOU window where
        # a hostname could re-resolve to a blocked address (e.g. the cloud
        # metadata endpoint 169.254.169.254) between validation and connect.
        return self._post_pinned(url, body, headers, pinned_ip)

    def _post_pinned(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        pinned_ip: str,
    ) -> httpx.Response:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Rebuild the URL with the validated IP as the connect target. Brackets
        # are required for IPv6 literals in the authority component.
        host_literal = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        netloc = f"{host_literal}:{parsed.port}" if parsed.port else host_literal
        pinned_url = parsed._replace(netloc=netloc).geturl()

        # Preserve virtual-host routing: the server must still see the original
        # Host. urlparse keeps userinfo in netloc, but parsed.hostname strips
        # it, so the Host header is free of credentials.
        send_headers = dict(headers)
        send_headers["Host"] = (
            f"{hostname}:{parsed.port}" if parsed.port else hostname
        )

        # For TLS, route SNI + certificate verification through the original
        # hostname (httpcore uses ``sni_hostname`` for ``server_hostname``),
        # so the cert is still validated against the hostname, not the IP.
        extensions = (
            {"sni_hostname": hostname} if parsed.scheme == "https" else None
        )

        with httpx.Client(timeout=self._timeout) as client:
            request = client.build_request(
                "POST", pinned_url, content=body, headers=send_headers
            )
            if extensions is not None:
                request.extensions = {**request.extensions, **extensions}
            return client.send(request)

    def _validate_target(self, url: str) -> str | None:
        """Validate *url* against the SSRF policy.

        Returns the single resolved IP address that the connection should be
        pinned to (closing the DNS-rebinding TOCTOU window), or ``None`` when
        pinning does not apply (``allow_private_ips`` opt-in, or DNS resolution
        failed and was deferred to httpx).
        """
        if not isinstance(url, str) or not url:
            raise WebhookSecurityError("Webhook URL is empty.")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise WebhookSecurityError(
                f"Unsupported scheme '{parsed.scheme}'. Use http or https."
            )
        if not parsed.hostname:
            raise WebhookSecurityError(f"URL '{url}' has no hostname.")
        if self._allow_private_ips:
            return None
        resolved = _resolve_all(parsed.hostname)
        for addr in resolved:
            if _is_blocked_address(addr):
                raise WebhookSecurityError(
                    f"Webhook target '{parsed.hostname}' resolves to blocked "
                    f"address '{addr}' (RFC1918/loopback/link-local/reserved)."
                )
        # Pin to the first validated address. _resolve_all returns the literal
        # hostname (not an IP) when DNS failed; in that case there is nothing
        # to pin and we defer connection to httpx.
        first = resolved[0] if resolved else None
        if first is None:
            return None
        try:
            ipaddress.ip_address(first)
        except ValueError:
            return None
        return first

    def _sign(self, body: bytes) -> str | None:
        if not self._signing_secret:
            return None
        digest = hmac.new(
            self._signing_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={digest}"

    def _sleep_for_retry(self, attempt: int) -> None:
        if attempt >= self._max_retries:
            return
        delay = _RETRY_BACKOFF_SECONDS[attempt]
        time.sleep(delay)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_all(hostname: str) -> list[str]:
    """Return every IP (v4+v6) ``hostname`` resolves to, or [hostname] on fail."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # If DNS fails entirely, treat as a literal — let httpx raise later.
        return [hostname]
    return list({info[4][0] for info in infos})


def _is_blocked_address(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        # Hostname literal (DNS failed): refuse to bypass the check.
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _redact(url: str) -> str:
    """Strip query string + userinfo for safer log lines."""
    parsed = urlparse(url)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return f"{parsed.scheme}://{netloc}{parsed.path}"
