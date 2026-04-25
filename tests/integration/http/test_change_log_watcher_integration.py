"""
Integration test for the Lot 2 wiring: GPKG SQLite triggers →
``ChangeLogWatcher`` → ``EventHub`` → ``/ws/events``.

Strategy
--------
We can't easily exercise the full ``upload → engine.enable_change_tracking
→ DML on uploaded file → watcher → WS`` chain inside one TestClient because
the upload endpoint stores the file on disk separately from the engine's
project GPKG. So we test the wire that actually matters: the watcher
running against ``app.state.spatial_engine`` picks up writes made via
SQLite directly and broadcasts ``dml.changed`` events that land on
``/ws/events``.

This validates:
  * The lifespan started the watcher when ``GISPULSE_ENGINE=gpkg``.
  * The watcher pulls rows from ``_gispulse_change_log``.
  * Events reach the WebSocket.
  * The payload is redacted (no row values leaked).
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def gpkg_app_client(tmp_path, monkeypatch):
    """Spin up a full-mode GISPulse app backed by a temp GPKG."""
    gpkg_path = tmp_path / "project.gpkg"
    db_path = tmp_path / "gispulse.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("GISPULSE_ENGINE", "gpkg")
    monkeypatch.setenv("GISPULSE_GPKG_PATH", str(gpkg_path))
    monkeypatch.setenv("GISPULSE_DB_PATH", str(db_path))
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.delenv("GISPULSE_API_KEYS", raising=False)
    monkeypatch.setenv("GISPULSE_API_KEYS", "")
    monkeypatch.setenv("GISPULSE_CORS_ORIGINS", "http://test")

    from gispulse.adapters.http.app import create_app

    app = create_app()
    with TestClient(app) as client:
        # Create a tracked layer in the project GPKG and install triggers.
        engine = app.state.spatial_engine
        conn = engine._get_conn()  # type: ignore[attr-defined]
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "parcels" '
            '(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)'
        )
        conn.commit()
        engine.enable_change_tracking("parcels")
        yield client, gpkg_path


def _drain_until(ws, predicate, timeout: float = 2.0):
    """Receive messages until *predicate* returns True or timeout fires."""
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        # TestClient WS doesn't expose receive with timeout — use the loop's
        # ``receive_text`` which is blocking, so we drive it via tight ticks.
        # In practice, the watcher polls every 0.2 s by default.
        msg = ws.receive_text()
        seen.append(msg)
        try:
            payload = json.loads(msg)
        except Exception:
            payload = {"type": ""}
        if payload.get("type") == "ping":
            continue
        if predicate(payload):
            return payload, seen
    raise AssertionError(f"predicate never matched. messages={seen}")


def test_lifespan_starts_change_log_watcher(gpkg_app_client) -> None:
    client, _gpkg_path = gpkg_app_client
    watcher = client.app.state.change_log_watcher
    assert watcher is not None
    assert watcher.is_running()


def test_dml_change_arrives_on_websocket(gpkg_app_client) -> None:
    client, gpkg_path = gpkg_app_client

    # Connect WS first so we don't miss the broadcast.
    with client.websocket_connect("/ws/events") as ws:
        # External writer: open a fresh SQLite connection to the same GPKG
        # and INSERT — this fires the AFTER INSERT trigger and writes to
        # _gispulse_change_log.
        ext = sqlite3.connect(str(gpkg_path))
        try:
            ext.execute('INSERT INTO "parcels"(name) VALUES (?)', ("alpha",))
            ext.commit()
        finally:
            ext.close()

        payload, _ = _drain_until(
            ws,
            lambda p: p.get("type") == "dml.changed",
            timeout=3.0,
        )

    data = payload["data"]
    # Multi-tenant contract: project engine is registered with the
    # synthetic id ``"__project__"`` (see app.py lifespan).
    assert data["dataset_id"] == "__project__"
    assert data["table"] == "parcels"
    assert data["op"] == "INSERT"
    assert data["fid"] is not None
    assert "change_id" in data
    assert "ts" in data

    # Security: no row values were leaked over the wire.
    forbidden = {"new_values", "old_values", "name", "values"}
    assert not (forbidden & set(data.keys()))


def test_watcher_skipped_for_unsupported_backend(tmp_path, monkeypatch) -> None:
    """When the active engine has no change-log surface, no watcher starts.

    Lot 3 widened the lifespan guard from ``backend == "gpkg"`` to
    ``backend in ("gpkg", "duckdb")`` because both ship a
    ``get_pending_changes`` surface. Backends without that surface
    (postgis, hybrid, or any third-party engine plugin) must NOT spawn
    a watcher — the polling loop would raise on every tick.

    We can't use a real built-in here:
      - ``postgis``/``hybrid`` need a live DSN at lifespan start;
      - ``gpkg``/``duckdb`` are now both supported.

    So we register a fake ``memory`` backend that returns a
    ``SpatialEngine`` lacking ``get_pending_changes``. The lifespan's
    structural ``hasattr`` guard is the contract this test pins.
    """
    monkeypatch.setenv("GISPULSE_ENGINE", "memory")
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.setenv("GISPULSE_API_KEYS", "")
    monkeypatch.setenv("GISPULSE_CORS_ORIGINS", "http://test")

    # Register the fake backend. The factory is a no-op engine: open()
    # / close() succeed but it deliberately omits the change-log
    # surface so the lifespan guard skips watcher creation. We also
    # skip tier gating by piggy-backing on an unknown name (community
    # tier rejects only postgis/hybrid).
    from persistence import engine_factory as ef

    class _NoTrackingEngine:
        backend_name = "memory"

        def open(self) -> None:  # noqa: D401
            return None

        def close(self) -> None:  # noqa: D401
            return None

        # Intentionally no get_pending_changes / mark_changes_processed.

    def _factory(*, dsn=None, duckdb_path=":memory:", **_kw):
        return _NoTrackingEngine()

    monkeypatch.setitem(ef._BACKENDS, "memory", _factory)

    from gispulse.adapters.http.app import create_app

    app = create_app()
    with TestClient(app):
        # Back-compat sentinel + multi-tenant registry both empty.
        assert getattr(app.state, "change_log_watcher", None) is None
        registry = getattr(app.state, "watcher_registry", None)
        assert registry is not None
        assert "__project__" not in registry.list_registered()
