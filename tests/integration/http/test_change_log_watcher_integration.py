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


def test_watcher_skipped_for_non_gpkg_backend(tmp_path, monkeypatch) -> None:
    """When the active engine is not GPKG, the watcher must not start."""
    monkeypatch.setenv("GISPULSE_ENGINE", "duckdb")
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.setenv("GISPULSE_API_KEYS", "")
    monkeypatch.setenv("GISPULSE_CORS_ORIGINS", "http://test")

    from gispulse.adapters.http.app import create_app

    app = create_app()
    with TestClient(app):
        assert getattr(app.state, "change_log_watcher", None) is None
