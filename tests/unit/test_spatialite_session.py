"""
Tests unitaires — SpatiaLiteSession (#91) et SessionProvisioner backend param.

Pas de PostGIS requis. Utilise SQLite en mémoire.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from gispulse.core.models import (
    ChangeOperation,
    EphemeralSession,
    SessionBackend,
    Trigger,
)
from gispulse.persistence.session_provisioner import SessionProvisioner
from gispulse.persistence.spatialite_session import SpatiaLiteSession, _build_sqlite_triggers


# ---------------------------------------------------------------------------
# _build_sqlite_triggers
# ---------------------------------------------------------------------------

class TestBuildSqliteTriggers:
    def test_returns_three_triggers(self):
        sqls = _build_sqlite_triggers("parcels")
        assert len(sqls) == 3

    def test_trigger_names(self):
        sqls = _build_sqlite_triggers("parcels")
        assert any("insert" in s for s in sqls)
        assert any("update" in s for s in sqls)
        assert any("delete" in s for s in sqls)

    def test_references_table(self):
        sqls = _build_sqlite_triggers("my_table")
        for sql in sqls:
            assert "my_table" in sql

    def test_inserts_into_change_log(self):
        sqls = _build_sqlite_triggers("parcels")
        for sql in sqls:
            assert "_change_log" in sql


# ---------------------------------------------------------------------------
# SpatiaLiteSession — lifecycle
# ---------------------------------------------------------------------------

class TestSpatiaLiteSessionLifecycle:
    def test_open_creates_change_log(self):
        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        tables = session.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [r[0] for r in tables]
        assert "_change_log" in table_names
        session.close()

    def test_conn_raises_when_not_open(self):
        session = SpatiaLiteSession()
        with pytest.raises(RuntimeError, match="not open"):
            _ = session.conn

    def test_close_clears_conn(self):
        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        session.close()
        with pytest.raises(RuntimeError):
            _ = session.conn

    def test_double_open_is_safe(self):
        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        # Replace conn with new one — second open should work
        session.open()
        session.close()

    def test_close_without_open_is_safe(self):
        session = SpatiaLiteSession(db_path=":memory:")
        session.close()  # should not raise


# ---------------------------------------------------------------------------
# SpatiaLiteSession — change log
# ---------------------------------------------------------------------------

class TestSpatiaLiteChangeLog:
    def setup_method(self):
        self.session = SpatiaLiteSession(db_path=":memory:")
        self.session.open()
        # Create a minimal table
        self.session.conn.execute(
            "CREATE TABLE parcels (id INTEGER PRIMARY KEY, name TEXT)"
        )
        for sql in _build_sqlite_triggers("parcels"):
            self.session.conn.execute(sql)
        self.session.conn.commit()

    def teardown_method(self):
        self.session.close()

    def test_insert_logged(self):
        self.session.conn.execute("INSERT INTO parcels(name) VALUES ('A')")
        self.session.conn.commit()
        row = self.session.conn.execute(
            "SELECT operation FROM _change_log WHERE table_name='parcels'"
        ).fetchone()
        assert row is not None
        assert row[0] == "INSERT"

    def test_update_logged(self):
        self.session.conn.execute("INSERT INTO parcels(name) VALUES ('A')")
        self.session.conn.commit()
        self.session.conn.execute("UPDATE parcels SET name='B' WHERE name='A'")
        self.session.conn.commit()
        ops = [
            r[0] for r in self.session.conn.execute(
                "SELECT operation FROM _change_log WHERE table_name='parcels'"
            ).fetchall()
        ]
        assert "UPDATE" in ops

    def test_delete_logged(self):
        self.session.conn.execute("INSERT INTO parcels(name) VALUES ('A')")
        self.session.conn.commit()
        self.session.conn.execute("DELETE FROM parcels WHERE name='A'")
        self.session.conn.commit()
        ops = [
            r[0] for r in self.session.conn.execute(
                "SELECT operation FROM _change_log WHERE table_name='parcels'"
            ).fetchall()
        ]
        assert "DELETE" in ops

    def test_initial_processed_is_zero(self):
        self.session.conn.execute("INSERT INTO parcels(name) VALUES ('A')")
        self.session.conn.commit()
        row = self.session.conn.execute(
            "SELECT processed FROM _change_log"
        ).fetchone()
        assert row[0] == 0


# ---------------------------------------------------------------------------
# SpatiaLiteSession — _process_pending_changes
# ---------------------------------------------------------------------------

class TestProcessPendingChanges:
    def setup_method(self):
        from gispulse.rules.trigger_evaluator import TriggerEvaluator
        self.session = SpatiaLiteSession(db_path=":memory:")
        self.session.open()
        self.session.conn.execute(
            "CREATE TABLE roads (id INTEGER PRIMARY KEY, name TEXT)"
        )
        for sql in _build_sqlite_triggers("roads"):
            self.session.conn.execute(sql)
        self.session.conn.commit()
        self.session._evaluator = TriggerEvaluator()

    def teardown_method(self):
        self.session.close()

    def _make_trigger(self, table: str = "roads") -> Trigger:
        return Trigger(
            name="test_trigger",
            rule_id="r1",
            conditions={"table": table, "operation": "INSERT"},
            actions=[],
        )

    def test_no_changes_returns_empty(self):
        fired = self.session._process_pending_changes("sess_1")
        assert fired == []

    def test_matching_trigger_fires(self):
        trigger = self._make_trigger("roads")
        self.session._triggers = [trigger]
        self.session.conn.execute("INSERT INTO roads(name) VALUES ('rue A')")
        self.session.conn.commit()
        fired = self.session._process_pending_changes("sess_1")
        assert len(fired) == 1
        assert fired[0].matched is True

    def test_non_matching_trigger_no_match(self):
        trigger = self._make_trigger("other_table")
        self.session._triggers = [trigger]
        self.session.conn.execute("INSERT INTO roads(name) VALUES ('rue B')")
        self.session.conn.commit()
        fired = self.session._process_pending_changes("sess_1")
        # Evaluator creates FiredTrigger with matched=False, not empty list
        assert all(not f.matched for f in fired)

    def test_processed_flag_set(self):
        self.session._triggers = [self._make_trigger("roads")]
        self.session.conn.execute("INSERT INTO roads(name) VALUES ('rue C')")
        self.session.conn.commit()
        self.session._process_pending_changes("sess_1")
        count = self.session.conn.execute(
            "SELECT COUNT(*) FROM _change_log WHERE processed = 1"
        ).fetchone()[0]
        assert count == 1

    def test_second_call_does_not_reprocess(self):
        self.session._triggers = [self._make_trigger("roads")]
        self.session.conn.execute("INSERT INTO roads(name) VALUES ('rue D')")
        self.session.conn.commit()
        self.session._process_pending_changes("sess_1")
        fired2 = self.session._process_pending_changes("sess_1")
        assert fired2 == []

    def test_fired_triggers_accumulated(self):
        trigger = self._make_trigger("roads")
        self.session._triggers = [trigger]
        self.session.conn.execute("INSERT INTO roads(name) VALUES ('rue E')")
        self.session.conn.commit()
        self.session._process_pending_changes("sess_1")
        assert len(self.session.fired_triggers) >= 1

    def test_clear_fired(self):
        trigger = self._make_trigger("roads")
        self.session._triggers = [trigger]
        self.session.conn.execute("INSERT INTO roads(name) VALUES ('rue F')")
        self.session.conn.commit()
        self.session._process_pending_changes("sess_1")
        self.session.clear_fired()
        assert self.session.fired_triggers == []

    def test_session_id_in_change_record(self):
        """Le session_id est bien injecté dans le ChangeRecord évalué."""
        captured: list = []

        class CapturingEvaluator:
            def evaluate(self, record, triggers, depth=1):
                captured.append(record.session_id)
                return []

        self.session._evaluator = CapturingEvaluator()
        self.session._triggers = [self._make_trigger("roads")]
        self.session.conn.execute("INSERT INTO roads(name) VALUES ('rue G')")
        self.session.conn.commit()
        self.session._process_pending_changes("my_session")
        assert captured == ["my_session"]

    def test_operation_mapped_correctly(self):
        """L'opération SQLite est mappée vers ChangeOperation."""
        captured: list = []

        class CapturingEvaluator:
            def evaluate(self, record, triggers, depth=1):
                captured.append(record.operation)
                return []

        self.session._evaluator = CapturingEvaluator()
        self.session._triggers = [self._make_trigger("roads")]
        self.session.conn.execute("INSERT INTO roads(name) VALUES ('rue H')")
        self.session.conn.commit()
        self.session._process_pending_changes("sess_1")
        assert captured[0] == ChangeOperation.INSERT


# ---------------------------------------------------------------------------
# SpatiaLiteSession — polling thread
# ---------------------------------------------------------------------------

class TestPolling:
    def test_start_stop_polling(self):
        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        session.conn.execute("CREATE TABLE t (id INTEGER)")
        session.conn.commit()
        session.start_polling(triggers=[], interval=0.05)
        assert session._polling is True
        session.stop_polling()
        assert session._polling is False
        session.close()

    def test_polling_not_started_twice(self):
        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        session.start_polling(triggers=[], interval=0.05)
        thread1 = session._poll_thread
        session.start_polling(triggers=[], interval=0.05)  # no-op
        assert session._poll_thread is thread1
        session.stop_polling()
        session.close()

    def test_polling_processes_changes(self):
        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        session.conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, v TEXT)")
        for sql in _build_sqlite_triggers("items"):
            session.conn.execute(sql)
        session.conn.commit()

        trigger = Trigger(
            name="watch_items",
            rule_id="r1",
            conditions={"table": "items", "operation": "INSERT"},
            actions=[],
        )
        from gispulse.rules.trigger_evaluator import TriggerEvaluator
        evaluator = TriggerEvaluator()
        session.start_polling(triggers=[trigger], interval=0.02, evaluator=evaluator)

        session.conn.execute("INSERT INTO items(v) VALUES ('x')")
        session.conn.commit()

        time.sleep(0.15)  # laisse le polling tourner
        session.stop_polling()

        assert any(f.matched for f in session.fired_triggers)
        session.close()


# ---------------------------------------------------------------------------
# SpatiaLiteSession — GPKG I/O (avec fichiers temporaires)
# ---------------------------------------------------------------------------

class TestGpkgIO:
    def test_load_gpkg_creates_table(self):
        import geopandas as gpd
        from shapely.geometry import Point

        gdf = gpd.GeoDataFrame(
            {"name": ["A", "B"]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        )
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as f:
            gpkg_path = f.name
        gdf.to_file(gpkg_path, driver="GPKG", layer="points")

        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        table_name = session.load_gpkg(gpkg_path, layer="points")
        assert table_name == "points"
        assert "points" in session._tables

        # Table créée dans SQLite
        tables = [
            r[0] for r in session.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "points" in tables

        # Triggers créés
        triggers = [
            r[0] for r in session.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        ]
        assert any("points" in t for t in triggers)

        session.close()
        Path(gpkg_path).unlink(missing_ok=True)

    def test_load_gpkg_registers_triggers(self):
        import geopandas as gpd
        from shapely.geometry import Point

        gdf = gpd.GeoDataFrame(
            {"id": [1]},
            geometry=[Point(0, 0)],
            crs="EPSG:4326",
        )
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as f:
            gpkg_path = f.name
        gdf.to_file(gpkg_path, driver="GPKG", layer="layer1")

        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        session.load_gpkg(gpkg_path, layer="layer1")

        trigger_names = [
            r[0] for r in session.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        ]
        assert any("insert" in t for t in trigger_names)
        assert any("update" in t for t in trigger_names)
        assert any("delete" in t for t in trigger_names)

        session.close()
        Path(gpkg_path).unlink(missing_ok=True)

    def test_commit_to_gpkg_raises_without_table(self):
        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        with pytest.raises(ValueError, match="No table loaded"):
            session.commit_to_gpkg("/tmp/out.gpkg")
        session.close()

    def test_commit_to_gpkg_raises_without_open(self):
        session = SpatiaLiteSession(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not open"):
            session.commit_to_gpkg("/tmp/out.gpkg")

    def test_load_raises_without_open(self):
        session = SpatiaLiteSession(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not open"):
            session.load_gpkg("/tmp/dummy.gpkg")

    def test_roundtrip_gpkg(self):
        import geopandas as gpd
        from shapely.geometry import Point

        gdf = gpd.GeoDataFrame(
            {"name": ["Paris", "Lyon"]},
            geometry=[Point(2.35, 48.85), Point(4.83, 45.75)],
            crs="EPSG:4326",
        )
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as f:
            in_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as f:
            out_path = f.name
        gdf.to_file(in_path, driver="GPKG", layer="cities")

        session = SpatiaLiteSession(db_path=":memory:")
        session.open()
        session.load_gpkg(in_path, layer="cities")
        session.commit_to_gpkg(out_path, layer="cities")
        session.close()

        result = gpd.read_file(out_path, layer="cities")
        assert len(result) == 2
        assert set(result["name"]) == {"Paris", "Lyon"}

        Path(in_path).unlink(missing_ok=True)
        Path(out_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# SessionProvisioner — backend param
# ---------------------------------------------------------------------------

class TestSessionProvisionerBackend:
    def test_default_auto_without_dsn_is_spatialite(self):
        prov = SessionProvisioner(base_dsn="")
        session = prov.create_session()
        assert session.backend == SessionBackend.SPATIALITE

    def test_default_auto_with_dsn_is_postgis(self):
        prov = SessionProvisioner(base_dsn="postgresql://user:pw@localhost/db")
        session = prov.create_session()
        assert session.backend == SessionBackend.POSTGIS

    def test_explicit_spatialite(self):
        prov = SessionProvisioner(base_dsn="postgresql://user:pw@localhost/db")
        session = prov.create_session(backend="spatialite")
        assert session.backend == SessionBackend.SPATIALITE

    def test_explicit_postgis(self):
        prov = SessionProvisioner(base_dsn="")
        session = prov.create_session(backend="postgis")
        assert session.backend == SessionBackend.POSTGIS

    def test_invalid_backend_raises(self):
        prov = SessionProvisioner()
        with pytest.raises(ValueError):
            prov.create_session(backend="oracle")

    def test_spatialite_session_db_path_stored(self):
        prov = SessionProvisioner()
        session = prov.create_session(backend="spatialite", db_path="/tmp/sess.db")
        assert session.db_path == "/tmp/sess.db"

    def test_spatialite_session_no_pg_dsn(self):
        prov = SessionProvisioner(base_dsn="postgresql://x:y@h/db")
        session = prov.create_session(backend="spatialite")
        assert session.pg_dsn is None

    def test_postgis_session_has_pg_dsn(self):
        prov = SessionProvisioner(base_dsn="postgresql://x:y@h/db")
        session = prov.create_session(backend="postgis")
        assert session.pg_dsn is not None

    def test_session_registered_in_provisioner(self):
        prov = SessionProvisioner()
        session = prov.create_session(backend="spatialite")
        assert prov.get(str(session.id)) is session

    def test_session_backend_enum_value(self):
        prov = SessionProvisioner()
        session = prov.create_session(backend=SessionBackend.SPATIALITE)
        assert session.backend == SessionBackend.SPATIALITE


# ---------------------------------------------------------------------------
# SessionBackend enum (models)
# ---------------------------------------------------------------------------

class TestSessionBackendEnum:
    def test_values(self):
        assert SessionBackend.POSTGIS == "postgis"
        assert SessionBackend.SPATIALITE == "spatialite"

    def test_from_string(self):
        assert SessionBackend("postgis") == SessionBackend.POSTGIS
        assert SessionBackend("spatialite") == SessionBackend.SPATIALITE

    def test_ephemeral_session_default_backend(self):
        session = EphemeralSession()
        assert session.backend == SessionBackend.POSTGIS

    def test_ephemeral_session_spatialite_backend(self):
        session = EphemeralSession(backend=SessionBackend.SPATIALITE, db_path=":memory:")
        assert session.backend == SessionBackend.SPATIALITE
        assert session.db_path == ":memory:"
