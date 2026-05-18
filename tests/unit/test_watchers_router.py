"""Tests for /watchers — closes P0-3 of the parity audit (issue #95).

The watcher daemon already auto-starts when ``POST /datasets/{id}/enable_tracking``
registers a dataset (see errata 2026-05-03 in PARITY_P0_SPEC). What this
router exposes is the **observability dashboard**: GET /watchers and
GET /watchers/{dataset_id}.

Validates:
1. Empty registry returns ``{count: 0, items: []}`` — the route works
   even when no watcher is running (otherwise the portal /runtime page
   would show a spinner forever on a clean install).
2. After ``register()``, the listed watcher exposes counters,
   poll_interval, layers, and gpkg_path.
3. ``GET /watchers/{unknown}`` returns 404 with a hint pointing at
   enable_tracking — operators must not have to dig through logs to
   know why their dataset isn't there.
4. The portal-mode response wraps :class:`WatcherStats` accurately
   (no missing fields, no extra fields that would silently regress
   the schema).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from gispulse.persistence.gpkg_schema import bootstrap_gpkg_project


def _make_gpkg(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        bootstrap_gpkg_project(conn)
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "parcels" '
            "(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def portal_client(monkeypatch):
    """Portal mode = auth disabled, endpoint reachable without admin role.

    Uses TestClient as a context manager so the ASGI lifespan fires —
    that's what installs ``app.state.watcher_registry``. Without the
    context manager, the registry attribute is missing and every
    endpoint returns 503 (which is correct, but useless to test the
    happy path)."""
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    from gispulse.adapters.http.rate_limit import limiter

    limiter.enabled = False
    app = create_app(mode="portal")
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# GET /watchers — list
# ---------------------------------------------------------------------------


def test_list_empty_registry(portal_client: TestClient) -> None:
    """Fresh app: registry exists but is empty → 200 with count 0."""
    resp = portal_client.get("/watchers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["items"] == []


def test_list_returns_registered_watcher(
    portal_client: TestClient, tmp_path: Path
) -> None:
    """Register one watcher → it shows up with full stats payload."""
    path = tmp_path / "ds.gpkg"
    _make_gpkg(path)

    registry = portal_client.app.state.watcher_registry
    registry.register("ds-1", path, layers=["parcels"])
    try:
        resp = portal_client.get("/watchers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        item = body["items"][0]
        assert item["dataset_id"] == "ds-1"
        assert item["running"] is True
        assert item["layers"] == ["parcels"]
        assert item["gpkg_path"] == str(path)
        # All schema fields must round-trip — guards against silent
        # regression if WatcherStats sheds a field on a refactor.
        for field in (
            "tick_count",
            "rows_processed",
            "fire_count",
            "error_count",
            "last_tick_at",
            "last_fire_at",
            "last_error_at",
            "last_error_msg",
            "poll_interval",
            "batch_limit",
            "bulk_threshold",
            "bulk_eval",
            "started_at",
        ):
            assert field in item
    finally:
        registry.unregister("ds-1")


# ---------------------------------------------------------------------------
# GET /watchers/{dataset_id} — detail
# ---------------------------------------------------------------------------


def test_detail_returns_watcher_stats(
    portal_client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "ds.gpkg"
    _make_gpkg(path)

    registry = portal_client.app.state.watcher_registry
    registry.register("ds-1", path, layers=["parcels"])
    try:
        resp = portal_client.get("/watchers/ds-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dataset_id"] == "ds-1"
        assert body["running"] is True
        assert body["layers"] == ["parcels"]
    finally:
        registry.unregister("ds-1")


def test_detail_404_for_unknown_dataset(portal_client: TestClient) -> None:
    """Unknown dataset → 404 with the hint that callers should call
    enable_tracking first. The hint matters: a viewer who lands on
    /runtime shouldn't have to grep code to know the next step."""
    resp = portal_client.get("/watchers/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    message = body.get("error", {}).get("message") or body.get("detail", "")
    assert "does-not-exist" in message
    assert "enable_tracking" in message


# ---------------------------------------------------------------------------
# Registry not initialised — defensive 503
# ---------------------------------------------------------------------------


def test_503_when_registry_missing(monkeypatch) -> None:
    """Force the registry to be absent → both endpoints return 503. We
    do not want to leak a 500 / AttributeError to the operator: 503
    says "server-side prerequisite missing", which is actionable
    (restart, check logs) rather than confusing."""
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    from gispulse.adapters.http.rate_limit import limiter

    limiter.enabled = False
    app = create_app(mode="portal")
    with TestClient(app) as client:
        # Lifespan installs the registry; clear it post-startup to
        # mimic a degraded runtime where the registry was lost.
        app.state.watcher_registry = None
        list_resp = client.get("/watchers")
        detail_resp = client.get("/watchers/anything")
    assert list_resp.status_code == 503
    assert detail_resp.status_code == 503
