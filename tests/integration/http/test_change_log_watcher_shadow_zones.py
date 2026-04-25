"""
Beta — Lot 2 integration shadow zones (WS fanout, auth gating, payload audit).

Marco's integration covers ONE WS client + ONE INSERT. These tests probe:

  * 5 concurrent WS clients on the same hub — do they all receive 50
    events without loss / out-of-order?
  * Is /ws/events open without auth when GISPULSE_API_KEYS is empty?
    (Marco flagged this; we confirm it.)
  * Does the payload ever leak row values? Is there a debug switch?
  * What happens at restart with un-acked rows in _gispulse_change_log?
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def gpkg_app(tmp_path, monkeypatch):
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
    monkeypatch.setenv("GISPULSE_API_KEYS", "")
    monkeypatch.setenv("GISPULSE_CORS_ORIGINS", "http://test")

    from gispulse.adapters.http.app import create_app

    app = create_app()
    with TestClient(app) as client:
        engine = app.state.spatial_engine
        conn = engine._get_conn()  # type: ignore[attr-defined]
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "parcels" '
            "(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, secret TEXT)"
        )
        conn.commit()
        engine.enable_change_tracking("parcels")
        yield client, gpkg_path


def _drain_dml(ws, expected: int, timeout: float = 5.0) -> list[dict]:
    """Pull dml.changed payloads until we have *expected* of them or timeout."""
    deadline = time.monotonic() + timeout
    payloads: list[dict] = []
    while time.monotonic() < deadline and len(payloads) < expected:
        msg = ws.receive_text()
        try:
            p = json.loads(msg)
        except Exception:
            continue
        if p.get("type") == "ping":
            continue
        if p.get("type") == "dml.changed":
            payloads.append(p)
    return payloads


# ---------------------------------------------------------------------------
# 1. Multi-WS fanout — all clients must receive all events
# ---------------------------------------------------------------------------


class TestMultiWebSocketFanout:
    def test_five_clients_receive_50_events_each(self, gpkg_app) -> None:
        """Open 5 WS clients, push 50 INSERTs from an external connection,
        every client must receive all 50 dml.changed events.

        This validates the EventHub fan-out under modest concurrency.
        TestClient runs WS sequentially in the same loop, so this exercises
        the asyncio queue per subscriber, not network-level concurrency.
        """
        client, gpkg_path = gpkg_app

        ws1 = client.websocket_connect("/ws/events").__enter__()
        ws2 = client.websocket_connect("/ws/events").__enter__()
        ws3 = client.websocket_connect("/ws/events").__enter__()
        ws4 = client.websocket_connect("/ws/events").__enter__()
        ws5 = client.websocket_connect("/ws/events").__enter__()
        try:
            # External writer — 50 INSERTs.
            ext = sqlite3.connect(str(gpkg_path))
            try:
                for i in range(50):
                    ext.execute(
                        'INSERT INTO "parcels"(name, secret) VALUES (?, ?)',
                        (f"name-{i}", f"SHOULD_NOT_LEAK_{i}"),
                    )
                ext.commit()
            finally:
                ext.close()

            # Each client should drain 50 events. Generous timeout (10s)
            # because the watcher polls every 200ms and batch=100, so 50
            # rows fit in 1–2 ticks.
            r1 = _drain_dml(ws1, 50, timeout=10.0)
            r2 = _drain_dml(ws2, 50, timeout=10.0)
            r3 = _drain_dml(ws3, 50, timeout=10.0)
            r4 = _drain_dml(ws4, 50, timeout=10.0)
            r5 = _drain_dml(ws5, 50, timeout=10.0)
        finally:
            for w in (ws1, ws2, ws3, ws4, ws5):
                try:
                    w.__exit__(None, None, None)
                except Exception:
                    pass

        for idx, r in enumerate((r1, r2, r3, r4, r5), start=1):
            assert len(r) == 50, (
                f"client {idx} received {len(r)}/50 events — FAN-OUT LEAK"
            )

        # change_ids must be the same set across all clients (ordering not
        # strictly guaranteed, but should match in practice).
        ids_per_client = [
            sorted(p["data"]["change_id"] for p in r)
            for r in (r1, r2, r3, r4, r5)
        ]
        assert all(ids == ids_per_client[0] for ids in ids_per_client[1:]), (
            "Different clients saw different change_ids — fan-out is broken"
        )


# ---------------------------------------------------------------------------
# 2. Auth gating on /ws/events
# ---------------------------------------------------------------------------


class TestWebSocketAuth:
    def test_ws_events_open_when_api_keys_empty(self, gpkg_app) -> None:
        """When GISPULSE_API_KEYS is empty (dev / Community default), any
        anonymous client can connect and receive every dml.changed event
        from every layer in the engine GPKG.

        This is what Marco flagged. We just confirm it's true today, with
        zero auth, no token, no header.
        """
        client, gpkg_path = gpkg_app

        with client.websocket_connect("/ws/events") as ws:
            ext = sqlite3.connect(str(gpkg_path))
            try:
                ext.execute(
                    'INSERT INTO "parcels"(name) VALUES (?)', ("anon_can_see_me",)
                )
                ext.commit()
            finally:
                ext.close()

            payloads = _drain_dml(ws, 1, timeout=3.0)
            assert len(payloads) == 1
            # ACTION: document /ws/events as REQUIRES auth in any deployment
            # exposed past loopback. Or fail-closed when api_keys is empty
            # in production env (cfg.api.env == "production").

    def test_ws_events_rejects_invalid_token_when_keys_set(
        self, tmp_path, monkeypatch
    ) -> None:
        """When GISPULSE_API_KEYS is set, an invalid ?token= must yield
        a close(4401). Confirm to make sure auth path still works.
        """
        gpkg_path = tmp_path / "project.gpkg"
        db_path = tmp_path / "gispulse.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        monkeypatch.setenv("GISPULSE_ENGINE", "gpkg")
        monkeypatch.setenv("GISPULSE_GPKG_PATH", str(gpkg_path))
        monkeypatch.setenv("GISPULSE_DB_PATH", str(db_path))
        monkeypatch.setenv("GISPULSE_STORAGE", "memory")
        monkeypatch.setenv("GISPULSE_TIER", "community")
        monkeypatch.setenv("GISPULSE_API_KEYS", "valid-key-1,valid-key-2")
        monkeypatch.setenv("GISPULSE_CORS_ORIGINS", "http://test")

        from starlette.websockets import WebSocketDisconnect

        from gispulse.adapters.http.app import create_app

        app = create_app()
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(
                    "/ws/events?token=WRONG_KEY"
                ) as ws:
                    ws.receive_text()
            assert exc_info.value.code == 4401


# ---------------------------------------------------------------------------
# 3. Payload audit — no row values leak
# ---------------------------------------------------------------------------


class TestPayloadRedaction:
    def test_secret_column_value_never_appears_in_ws_payload(
        self, gpkg_app
    ) -> None:
        """The fixture creates a ``secret`` column. Insert a recognisable
        marker value and grep the entire WS message stream for it. If
        anything leaks (now or after a future ``?include_values=true``
        addition), this catches it.
        """
        client, gpkg_path = gpkg_app
        marker = "MARKER_SHOULD_NEVER_LEAK_42"

        with client.websocket_connect("/ws/events") as ws:
            ext = sqlite3.connect(str(gpkg_path))
            try:
                ext.execute(
                    'INSERT INTO "parcels"(name, secret) VALUES (?, ?)',
                    ("alpha", marker),
                )
                ext.commit()
            finally:
                ext.close()

            payloads = _drain_dml(ws, 1, timeout=3.0)

        assert len(payloads) == 1
        full_text = json.dumps(payloads)
        assert marker not in full_text, (
            f"DATA LEAK: column value {marker!r} appeared in WS payload: "
            f"{full_text}"
        )
        # Also assert allowed keys exactly. dataset_id added in Lot 2 v2 fix M1
        # to disambiguate multi-GPKG events; no leak risk since it's a UUID/
        # known identifier, not a row value.
        data = payloads[0]["data"]
        assert set(data.keys()) <= {"dataset_id", "table", "op", "fid", "change_id", "ts"}, (
            f"Unexpected keys in dml.changed payload: {set(data.keys())}"
        )

    def test_no_include_values_query_param_unlocks_verbose_payload(
        self, gpkg_app
    ) -> None:
        """Defensive check: ensure no hidden ``?include_values=true`` style
        switch exists on /ws/events that would dump row values. We do this
        by trying common query-param names and asserting the payload still
        omits the marker.
        """
        client, gpkg_path = gpkg_app
        marker = "MARKER_DEBUG_42"

        suspicious = [
            "/ws/events?include_values=true",
            "/ws/events?verbose=1",
            "/ws/events?debug=true",
            "/ws/events?raw=1",
        ]

        for url in suspicious:
            with client.websocket_connect(url) as ws:
                ext = sqlite3.connect(str(gpkg_path))
                try:
                    ext.execute(
                        'INSERT INTO "parcels"(name, secret) VALUES (?, ?)',
                        ("a", marker),
                    )
                    ext.commit()
                finally:
                    ext.close()

                payloads = _drain_dml(ws, 1, timeout=3.0)
                full_text = json.dumps(payloads)
                assert marker not in full_text, (
                    f"VERBOSE LEAK via {url}: payload contained {marker}"
                )


# ---------------------------------------------------------------------------
# 4. Restart / recovery — un-acked rows replayed?
# ---------------------------------------------------------------------------


class TestRestartReplay:
    def test_unacked_changes_replay_after_restart(
        self, tmp_path, monkeypatch
    ) -> None:
        """Simulate a crash: shove rows into _gispulse_change_log directly
        (bypassing triggers since processed=0), then start the app and
        confirm the watcher picks them up.

        This is the "unprocessed backlog at boot" scenario. Good news: the
        watcher polls and acks regardless of whether they came from triggers
        or were left over from a prior crash. Confirm.
        """
        gpkg_path = tmp_path / "project.gpkg"
        db_path = tmp_path / "gispulse.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        monkeypatch.setenv("GISPULSE_ENGINE", "gpkg")
        monkeypatch.setenv("GISPULSE_GPKG_PATH", str(gpkg_path))
        monkeypatch.setenv("GISPULSE_DB_PATH", str(db_path))
        monkeypatch.setenv("GISPULSE_STORAGE", "memory")
        monkeypatch.setenv("GISPULSE_TIER", "community")
        monkeypatch.setenv("GISPULSE_API_KEYS", "")
        monkeypatch.setenv("GISPULSE_CORS_ORIGINS", "http://test")

        from gispulse.adapters.http.app import create_app

        # First boot — bootstrap & inject un-acked rows, then shut down.
        app1 = create_app()
        with TestClient(app1) as c1:
            engine = app1.state.spatial_engine
            conn = engine._get_conn()  # type: ignore[attr-defined]
            conn.execute(
                'CREATE TABLE IF NOT EXISTS "parcels" '
                "(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
            )
            # Inject pretend-crashed entries.
            conn.executemany(
                "INSERT INTO _gispulse_change_log("
                "table_name, operation, row_pk, processed) VALUES (?, ?, ?, 0)",
                [
                    ("parcels", "INSERT", "100"),
                    ("parcels", "INSERT", "101"),
                    ("parcels", "UPDATE", "100"),
                ],
            )
            conn.commit()
            # Don't read from /ws/events here; let the watcher ack them
            # mid-test.

        # Second boot — same GPKG, expect the watcher to ack any leftovers
        # very fast OR to surface them on /ws/events.
        app2 = create_app()
        with TestClient(app2) as c2:
            with c2.websocket_connect("/ws/events") as ws:
                payloads = _drain_dml(ws, 3, timeout=3.0)

            engine2 = app2.state.spatial_engine
            conn2 = engine2._get_conn()  # type: ignore[attr-defined]
            unacked = conn2.execute(
                "SELECT COUNT(*) FROM _gispulse_change_log WHERE processed = 0"
            ).fetchone()[0]

        assert len(payloads) == 3, (
            f"Restart replay: expected 3 events, got {len(payloads)}. "
            "Either the watcher didn't run or events were dropped."
        )
        assert unacked == 0, (
            f"After replay, {unacked} rows still unacked — "
            "mark_changes_processed not converging."
        )
