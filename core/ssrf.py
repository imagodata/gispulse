"""SSRF guard — block outbound requests to private / internal addresses.

Issue #199. The ETL ``FetcherRegistry`` (and any other outbound caller)
must not let a declared source — or a third-party marketplace plugin —
point a request at ``169.254.169.254``, ``localhost`` or an RFC1918
host. The webhook client already had this protection (issue #451); this
module is the shared, reusable form so the fetch path gets it too.

A blocked deployment that *does* have legitimate internal sources opts
back in with ``GISPULSE_FETCH_ALLOW_PRIVATE=1``.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

from core.logging import get_logger

log = get_logger(__name__)

# Env opt-in: when truthy, private/internal addresses are allowed. For
# deployments whose data sources legitimately live on an internal network.
_ALLOW_PRIVATE_ENV = "GISPULSE_FETCH_ALLOW_PRIVATE"

_TRUTHY = {"1", "true", "yes", "on"}


class SSRFError(RuntimeError):
    """Raised when a URL targets a blocked (private/internal) address."""


def _allow_private_default() -> bool:
    return os.environ.get(_ALLOW_PRIVATE_ENV, "").strip().lower() in _TRUTHY


def resolve_all(hostname: str) -> list[str]:
    """Return every IP (v4+v6) ``hostname`` resolves to.

    On DNS failure returns ``[hostname]`` verbatim — :func:`is_blocked_address`
    then refuses the unresolvable literal rather than letting it through.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return [hostname]
    return list({info[4][0] for info in infos})


def is_blocked_address(addr: str) -> bool:
    """True if ``addr`` is private / loopback / link-local / reserved.

    A non-IP string (DNS failed, leaving a hostname literal) is blocked —
    failing closed is the safe default for an SSRF guard.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def guard_outbound_url(url: str | None, *, allow_private: bool | None = None) -> None:
    """SSRF-check an outbound URL before a request is made.

    A no-op for a non-``http(s)`` endpoint — a local file path or a
    ``file://`` URI is not a network target and is left to the caller.
    An ``http(s)`` URL whose host resolves to a blocked address raises
    :class:`SSRFError`.

    Args:
        url:           The endpoint about to be requested.
        allow_private: Override the ``GISPULSE_FETCH_ALLOW_PRIVATE`` env
                       opt-in. ``None`` (default) reads the env.

    Raises:
        SSRFError: the URL resolves to a private / internal address.
    """
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return  # not an HTTP target — nothing to SSRF-check

    if allow_private is None:
        allow_private = _allow_private_default()
    if allow_private:
        return

    host = parsed.hostname
    if not host:
        raise SSRFError(f"URL {url!r} has no hostname")

    for addr in resolve_all(host):
        if is_blocked_address(addr):
            log.warning("ssrf_blocked", host=host, address=addr)
            raise SSRFError(
                f"host {host!r} resolves to blocked address {addr!r} "
                f"(private/loopback/link-local/reserved) — set "
                f"{_ALLOW_PRIVATE_ENV}=1 to allow internal sources"
            )


__all__ = [
    "SSRFError",
    "guard_outbound_url",
    "is_blocked_address",
    "resolve_all",
]
