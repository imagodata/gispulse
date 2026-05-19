"""Unit tests for A14 (#240) — worldwide ``revision()`` + watcher wiring.

Zero network: the live HTTP ``HEAD`` probe is exercised through a stubbed
``httpx.head`` and a no-op SSRF guard, so CI never leaves the box.
"""

from __future__ import annotations

import pytest

from gispulse.persistence.source_watcher import SourceWatcherRegistry
from gispulse.plugins import worldwide_source as ws
from gispulse.plugins.worldwide_source import (
    WorldwideCatalogSource,
    register_worldwide_watches,
)


# -- helpers -----------------------------------------------------------------


class _FakeHeaders:
    """A case-insensitive header mapping like ``httpx.Headers``."""

    def __init__(self, data: dict[str, str]) -> None:
        self._data = {k.lower(): v for k, v in data.items()}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key.lower(), default)


class _FakeResponse:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = _FakeHeaders(headers)


@pytest.fixture
def _no_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the SSRF guard — the probe URLs are synthetic."""
    monkeypatch.setattr("gispulse.core.ssrf.guard_outbound_url", lambda *a, **k: None)


# -- revision(): versioned entries return the declared token -----------------


def test_revision_returns_declared_token_for_versioned_entry() -> None:
    src = WorldwideCatalogSource()
    # overture-buildings ships a static millésime token in the catalogue.
    assert src.revision("overture-buildings") == "2025-09-24.0"


def test_revision_probes_live_entry(
    monkeypatch: pytest.MonkeyPatch, _no_ssrf: None
) -> None:
    """A null-token (live) entry triggers an HTTP HEAD probe."""
    import httpx

    monkeypatch.setattr(
        httpx, "head", lambda *a, **k: _FakeResponse({"ETag": '"abc123"'})
    )
    src = WorldwideCatalogSource()
    # ign-bdtopo-batiment has revision_token: null in the catalogue.
    assert src.revision("ign-bdtopo-batiment") == 'etag:"abc123"'


# -- _probe_revision ---------------------------------------------------------


def test_probe_revision_skips_non_http_scheme() -> None:
    # s3:// endpoints are not HEAD-probeable — no network, returns None.
    assert ws._probe_revision("s3://bucket/key/*") is None


def test_probe_revision_prefers_etag(
    monkeypatch: pytest.MonkeyPatch, _no_ssrf: None
) -> None:
    import httpx

    monkeypatch.setattr(
        httpx,
        "head",
        lambda *a, **k: _FakeResponse(
            {"ETag": '"v9"', "Last-Modified": "yesterday"}
        ),
    )
    assert ws._probe_revision("https://example.test/data") == 'etag:"v9"'


def test_probe_revision_falls_back_to_last_modified(
    monkeypatch: pytest.MonkeyPatch, _no_ssrf: None
) -> None:
    import httpx

    monkeypatch.setattr(
        httpx,
        "head",
        lambda *a, **k: _FakeResponse({"Last-Modified": "Tue, 01 Apr 2025"}),
    )
    assert (
        ws._probe_revision("https://example.test/data")
        == "last-modified:Tue, 01 Apr 2025"
    )


def test_probe_revision_returns_none_on_network_error(
    monkeypatch: pytest.MonkeyPatch, _no_ssrf: None
) -> None:
    import httpx

    def _boom(*_a: object, **_k: object) -> object:
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(httpx, "head", _boom)
    assert ws._probe_revision("https://example.test/data") is None


def test_probe_revision_returns_none_when_no_validator_header(
    monkeypatch: pytest.MonkeyPatch, _no_ssrf: None
) -> None:
    import httpx

    monkeypatch.setattr(httpx, "head", lambda *a, **k: _FakeResponse({}))
    assert ws._probe_revision("https://example.test/data") is None


# -- register_worldwide_watches ----------------------------------------------


def test_register_worldwide_watches_files_static_entries() -> None:
    src = WorldwideCatalogSource()
    static = [e.id for e in src.entries() if e.revision_token]
    watcher = SourceWatcherRegistry()
    keys = register_worldwide_watches(watcher, src, entry_ids=static)
    assert keys == [f"worldwide:{eid}" for eid in static]
    assert watcher.list_watched() == sorted(keys)


def test_register_worldwide_watches_entry_ids_filter() -> None:
    src = WorldwideCatalogSource()
    watcher = SourceWatcherRegistry()
    keys = register_worldwide_watches(
        watcher, src, entry_ids=["overture-buildings"]
    )
    assert keys == ["worldwide:overture-buildings"]


def test_watcher_fires_when_worldwide_revision_changes() -> None:
    """A14 acceptance — a moved source makes the watcher emit a change."""

    class _MovingSource:
        name = "worldwide"

        def __init__(self) -> None:
            self._rev = "rev-1"

        def entries(self):  # noqa: ANN202 - test stub
            return []

        def revision(self, entry_id: str) -> str:
            return self._rev

    src = _MovingSource()
    watcher = SourceWatcherRegistry()
    watcher.register(src, "overture-buildings", interval_s=3600)
    assert watcher.poll() == []  # baseline — no change

    src._rev = "rev-2"  # the remote source moved
    changes = watcher.poll()
    assert len(changes) == 1
    assert changes[0]["revision"] == "rev-2"
    assert changes[0]["previous"] == "rev-1"
