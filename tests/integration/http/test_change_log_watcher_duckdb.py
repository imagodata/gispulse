"""Lot 3 — DuckDB integration: lifespan watcher and ``/ws/events`` flow.

Mirrors :mod:`test_change_log_watcher_integration` but for the DuckDB
backend. The pipeline is::

    DuckDBSpatialEngine.execute(DML)
        → DuckDBChangeDetector logs row in _change_log
        → ChangeLogWatcher polls (lifespan-bound)
        → EventHub.broadcast("dml.changed", ...)
        → /ws/events subscriber receives the payload

Because DuckDB has no native triggers, only writes routed through the
engine's ``execute()`` proxy are captured. ``test_external_duckdb_write_does_not_arrive``
documents that limitation.
"""

from __future__ import annotations

import json
import time

import duckdb
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixture: full-mode app on a DuckDB project file
# ---------------------------------------------------------------------------


@pytest.fixture()
def duckdb_app_client(tmp_path, monkeypatch):
    """Spin up a full-mode app with ``GISPULSE_ENGINE=duckdb``.

    We force a file-backed DuckDB so the same DB can be reopened by an
    external connection in the limitation test below. The default
    factory uses ``:memory:`` (no per-connection isolation possible),
    so we monkey-patch the factory call site here for the test.
    """
    duckdb_path = tmp_path / "project.duckdb"
    db_path = tmp_path / "gispulse.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("GISPULSE_ENGINE", "duckdb")
    monkeypatch.setenv("GISPULSE_DB_PATH", str(db_path))
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.setenv("GISPULSE_API_KEYS", "")
    monkeypatch.setenv("GISPULSE_CORS_ORIGINS", "http://test")

    # Override the duckdb factory to point at our temp path. The
    # built-in factory hard-codes ``:memory:`` (Lot 3 keeps that as the
    # production default until config plumbing for duckdb_path lands),
    # so we patch it just for this fixture.
    from persistence import engine_factory as ef
    from persistence.duckdb_engine_adapter import DuckDBSpatialEngine

    def _patched_factory(*, dsn=None, duckdb_path=":memory:", **_kw):
        return DuckDBSpatialEngine(database=str(tmp_path / "project.duckdb"))

    monkeypatch.setitem(ef._BACKENDS, "duckdb", _patched_factory)

    from gispulse.adapters.http.app import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client, duckdb_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain_dml(ws, expected: int, timeout: float = 5.0) -> list[dict]:
    """Receive WS frames until *expected* dml.changed payloads land."""
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
# Tests
# ---------------------------------------------------------------------------


class TestLifespanWatcher:
    def test_lifespan_starts_watcher_on_duckdb_backend(
        self, duckdb_app_client
    ):
        """The lifespan registers ``__project__`` for DuckDB just like GPKG."""
        client, _ = duckdb_app_client
        registry = client.app.state.watcher_registry
        # Lot 3: the synthetic ``__project__`` slot must exist when the
        # backend is duckdb. Lot 2 v2 only populated it for gpkg — this
        # test guards the widening of that gate.
        assert "__project__" in registry.list_registered()
        # The lifespan also stashes the watcher on app.state for back-compat.
        assert client.app.state.change_log_watcher is not None

    def test_engine_is_duckdb_spatial_engine(self, duckdb_app_client):
        client, _ = duckdb_app_client
        from persistence.duckdb_engine_adapter import DuckDBSpatialEngine

        # The factory must hand back the adapter, not a bare DuckDBSession.
        assert isinstance(client.app.state.spatial_engine, DuckDBSpatialEngine)


class TestEndToEndDMLEvent:
    def test_dml_via_engine_arrives_on_websocket(self, duckdb_app_client):
        """A write through engine.execute() lands as a dml.changed event."""
        client, _ = duckdb_app_client
        engine = client.app.state.spatial_engine

        # Bootstrap a layer via raw conn (DDL is not DML — no event
        # expected here). Then write through the proxy.
        engine.conn.execute(
            "CREATE TABLE parcels (id INTEGER, name TEXT)"
        )

        with client.websocket_connect("/ws/events") as ws:
            engine.execute(
                "INSERT INTO parcels VALUES (1, 'alpha')"
            )

            payloads = _drain_dml(ws, expected=1, timeout=4.0)

        assert len(payloads) >= 1
        match = next(
            (p for p in payloads if p["data"].get("table") == "parcels"),
            None,
        )
        assert match is not None
        assert match["data"]["op"] == "INSERT"
        assert "change_id" in match["data"]
        # Multi-tenant contract: the project watcher tags every event
        # with the synthetic ``__project__`` dataset id (Lot 2 v2 contract).
        assert match["data"]["dataset_id"] == "__project__"


class TestExternalWriteLimitation:
    def test_external_duckdb_write_does_not_arrive(self, tmp_path):
        """Direct ``duckdb.connect()`` from outside the engine bypasses detection.

        This is the documented Lot 3 limitation. The watcher's contract
        only covers writes routed through the engine's ``execute()``
        proxy. We assert the negative behaviour explicitly so a future
        refactor that accidentally wires up DB-level capture forces us
        to revisit the docs and the WS docstring.

        Note: this test does not use the lifespan-bound app — DuckDB's
        single-writer lock prevents an external ``duckdb.connect()``
        while the lifespan engine still holds the file. We verify the
        adapter-level guarantee in isolation: only DML through
        ``engine.execute()`` reaches ``_change_log``.
        """
        from persistence.duckdb_engine_adapter import DuckDBSpatialEngine

        db_file = tmp_path / "external.duckdb"
        engine = DuckDBSpatialEngine(database=str(db_file))
        engine.open()
        try:
            engine.conn.execute(
                "CREATE TABLE parcels (id INTEGER, name TEXT)"
            )
            # CHECKPOINT and close so the external handle can grab the
            # writer lock — DuckDB only permits a single writer at a time.
            engine.conn.execute("CHECKPOINT")
            engine.close()

            ext = duckdb.connect(str(db_file))
            try:
                ext.execute(
                    "INSERT INTO parcels VALUES (99, 'sneaky')"
                )
            finally:
                ext.close()

            # Re-open and verify the change_log is empty: the external
            # write was real (the row exists) but the detector saw nothing.
            engine.open()
            count = engine.conn.execute(
                "SELECT COUNT(*) FROM parcels"
            ).fetchall()
            assert count[0][0] == 1
            assert engine.get_pending_changes() == [], (
                "External duckdb.connect() writes must not land in "
                "_change_log (Lot 3 documented limitation)."
            )
        finally:
            try:
                engine.close()
            except Exception:
                pass
