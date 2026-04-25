"""Unit tests for :class:`DuckDBSpatialEngine` (Lot 3).

The adapter wraps :class:`DuckDBSession` with a GPKG-shaped change-log
surface so the existing :class:`ChangeLogWatcher` polls DuckDB exactly
the same way it polls a GPKG. These tests exercise the change-log
contract in isolation — the integration with FastAPI lifespan and
``/ws/events`` lives in ``tests/integration/http/``.
"""
from __future__ import annotations

import duckdb
import pytest

from persistence.duckdb_engine_adapter import DuckDBSpatialEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """Open a fresh in-memory adapter and seed a ``parcels`` test layer."""
    eng = DuckDBSpatialEngine(database=":memory:")
    eng.open()
    # Bootstrap a layer the way the pipeline runtime would. We do this
    # via the underlying conn (DDL is not DML, so it doesn't pollute
    # the change log).
    eng.conn.execute(
        "CREATE TABLE parcels (id INTEGER, name TEXT, area DOUBLE)"
    )
    yield eng
    eng.close()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_open_creates_change_log(self, engine):
        # The detector wires _change_log on open(). Verify the table is
        # there and starts empty.
        rows = engine.conn.execute(
            "SELECT COUNT(*) FROM _change_log"
        ).fetchall()
        assert rows[0][0] == 0

    def test_path_property_returns_database_string(self):
        eng = DuckDBSpatialEngine(database=":memory:")
        # path is a Path-like used by WatcherRegistry's identity check —
        # for in-memory mode we still return Path(":memory:") so equality
        # checks remain well-defined.
        from pathlib import Path

        assert eng.path == Path(":memory:")

    def test_backend_name_is_duckdb(self, engine):
        # Tier gating and /health branch on backend_name; do not change.
        assert engine.backend_name == "duckdb"

    def test_get_pending_changes_before_open_raises(self):
        eng = DuckDBSpatialEngine(database=":memory:")
        with pytest.raises(RuntimeError):
            eng.get_pending_changes()


# ---------------------------------------------------------------------------
# Change-log surface
# ---------------------------------------------------------------------------


class TestChangeLogSurface:
    def test_get_pending_changes_returns_empty_initially(self, engine):
        # Right after open() and before any DML, the watcher should see
        # zero rows — it must not retry indefinitely on a phantom backlog.
        assert engine.get_pending_changes() == []

    def test_execute_insert_logs_change(self, engine):
        engine.execute("INSERT INTO parcels VALUES (1, 'A', 100.0)")
        rows = engine.get_pending_changes()
        assert len(rows) == 1
        row = rows[0]
        # Schema parity with GeoPackageEngine.get_pending_changes — the
        # watcher reads exactly these keys.
        assert row["table_name"] == "parcels"
        assert row["operation"] == "INSERT"
        assert row["processed"] == 0
        assert "id" in row
        assert "changed_at" in row

    def test_execute_update_logs_change(self, engine):
        engine.conn.execute("INSERT INTO parcels VALUES (1, 'A', 100.0)")
        engine.execute("UPDATE parcels SET area = 999 WHERE id = 1")
        rows = engine.get_pending_changes()
        # The seed INSERT was via raw conn (bypasses detector), so only
        # the UPDATE should land on the change log.
        assert len(rows) == 1
        assert rows[0]["operation"] == "UPDATE"

    def test_execute_delete_logs_change(self, engine):
        engine.conn.execute("INSERT INTO parcels VALUES (1, 'A', 100.0)")
        engine.execute("DELETE FROM parcels WHERE id = 1")
        rows = engine.get_pending_changes()
        assert len(rows) == 1
        assert rows[0]["operation"] == "DELETE"

    def test_execute_select_does_not_log(self, engine):
        engine.conn.execute("INSERT INTO parcels VALUES (1, 'A', 100.0)")
        engine.execute("SELECT * FROM parcels")
        # SELECT is not DML — the change log stays empty.
        assert engine.get_pending_changes() == []

    def test_get_pending_changes_respects_limit(self, engine):
        for i in range(5):
            engine.execute(
                f"INSERT INTO parcels VALUES ({i}, 'x', 1.0)"
            )
        rows = engine.get_pending_changes(limit=3)
        assert len(rows) == 3

    def test_mark_changes_processed_updates_processed_flag(self, engine):
        engine.execute("INSERT INTO parcels VALUES (1, 'A', 100.0)")
        engine.execute("INSERT INTO parcels VALUES (2, 'B', 200.0)")
        rows = engine.get_pending_changes()
        assert len(rows) == 2

        # Ack only the first row.
        first_id = rows[0]["id"]
        n = engine.mark_changes_processed(first_id)
        assert n == 1

        remaining = engine.get_pending_changes()
        assert len(remaining) == 1
        assert remaining[0]["id"] != first_id

    def test_mark_changes_processed_returns_zero_when_nothing_to_ack(
        self, engine
    ):
        # No rows pending ⇒ zero rows updated. The watcher relies on
        # this to avoid spurious "ack failed" warnings.
        n = engine.mark_changes_processed(999)
        assert n == 0


# ---------------------------------------------------------------------------
# Change-tracking lifecycle (per-layer enable/disable)
# ---------------------------------------------------------------------------


class TestEnableChangeTracking:
    def test_enable_change_tracking_is_noop_for_existing_layer(
        self, engine, caplog
    ):
        # Detection is global; calling enable_change_tracking on an
        # existing layer must succeed silently. No exception, no warning.
        import logging

        caplog.set_level(logging.WARNING)
        engine.enable_change_tracking("parcels")

        # No "unknown layer" warning should fire.
        unknown_warnings = [
            r for r in caplog.records
            if "unknown_layer" in r.getMessage()
        ]
        assert unknown_warnings == []

    def test_enable_change_tracking_warns_on_unknown_layer(
        self, engine, caplog
    ):
        import logging

        caplog.set_level(logging.WARNING)
        engine.enable_change_tracking("does_not_exist")

        # A warning must be emitted so the caller can detect typos in
        # the layer name. We don't raise (matches GPKG behaviour for
        # validation-soft cases).
        warnings = [
            r for r in caplog.records
            if "unknown_layer" in r.getMessage()
        ]
        assert len(warnings) == 1

    def test_disable_change_tracking_is_noop(self, engine):
        # Symmetric with enable_change_tracking — does not raise even
        # for unknown layers.
        engine.disable_change_tracking("parcels")
        engine.disable_change_tracking("does_not_exist")


# ---------------------------------------------------------------------------
# External-write limitation (documented behaviour)
# ---------------------------------------------------------------------------


class TestExternalConnectionLimitation:
    def test_external_duckdb_connect_bypasses_detection(self, tmp_path):
        """A second DuckDB connection bypasses the adapter's DML proxy.

        This documents the Lot 3 limitation explicitly: detection is
        application-level, not DB-level. If a third-party tool (or
        another part of GISPulse holding its own ``duckdb.connect``)
        writes to the database, those writes are invisible to the
        change log.

        We assert the **expected** behaviour (not a xfail) so a future
        refactor that accidentally fixes detection-via-second-connection
        forces us to update the docs and the WS limitation note.

        Note: DuckDB enforces single-writer-at-a-time on a file, so we
        close the engine handle before opening the external one and
        reopen afterwards.
        """
        db_path = tmp_path / "shared.duckdb"
        eng = DuckDBSpatialEngine(database=str(db_path))
        eng.open()
        try:
            eng.conn.execute(
                "CREATE TABLE parcels (id INTEGER, name TEXT)"
            )
            eng.conn.execute("CHECKPOINT")
            eng.close()

            # External tool opens its own connection on the same file.
            ext = duckdb.connect(str(db_path))
            try:
                ext.execute(
                    "INSERT INTO parcels VALUES (42, 'external')"
                )
            finally:
                ext.close()

            # Reopen the adapter and verify: external write is real
            # (the row exists) but the change log is empty.
            eng.open()
            count = eng.conn.execute(
                "SELECT COUNT(*) FROM parcels"
            ).fetchall()
            assert count[0][0] == 1
            assert eng.get_pending_changes() == []
        finally:
            try:
                eng.close()
            except Exception:
                pass
