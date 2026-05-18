"""Pytest fixtures for the v1.9.0 core-fetcher tests (#229-#232).

The :class:`~gispulse.core.fetchers.base.LazyFetcher` SSRF guard (#199)
does a real DNS resolution. CI must do **zero network**, so the tests
patch :func:`gispulse.core.ssrf.resolve_all` to map every test host to a
fixed *public* IP — the rest of the guard logic (``is_blocked_address``)
then runs for real. Loopback / private literals such as ``127.0.0.1``
are passed straight through (no DNS needed) so the SSRF-rejection tests
still exercise the genuine block path.
"""

from __future__ import annotations

import pytest

#: A globally-routable public IP. is_blocked_address() rejects private /
#: loopback / link-local / *reserved* ranges — RFC-5737 documentation IPs
#: (203.0.113.x) are flagged reserved, so a genuinely public address is
#: needed for the test hosts to pass the SSRF guard.
_PUBLIC_TEST_IP = "8.8.8.8"


@pytest.fixture
def offline_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every non-literal test host to a public IP — no network.

    A host that is already an IP literal (e.g. ``127.0.0.1``) is returned
    verbatim, so the SSRF guard still blocks loopback/private addresses.
    """

    def _fake_resolve(hostname: str) -> list[str]:
        # An IP literal: hand it back so is_blocked_address judges it.
        parts = hostname.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            return [hostname]
        return [_PUBLIC_TEST_IP]

    import gispulse.core.ssrf as ssrf

    monkeypatch.setattr(ssrf, "resolve_all", _fake_resolve)
