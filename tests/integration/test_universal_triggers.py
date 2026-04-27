"""
Integration tests — Universal trigger engine across backends.

Tests the trigger detection → evaluation → fired trigger pipeline for:
- SpatiaLite: SQLite AFTER triggers + polling + TriggerEvaluator
- DuckDB: write proxy + change_log + polling + TriggerEvaluator

PostGIS tests are skipped without a live server (marked with postgis marker).
"""
from __future__ import annotations

import duckdb
import geopandas as gpd
import pytest
from shapely.geometry import box

from core.models import ChangeOperation, Trigger, TriggerEvent, TriggerType
from persistence.duckdb_change_detector import DuckDBChangeDetector
from persistence.spatialite_session import SpatiaLiteSession


# ---------------------------------------------------------------------------
# Shared trigger fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dml_insert_trigger():
    return Trigger(
        name="on_insert",
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={"table": "parcels", "operation": "INSERT"},
        enabled=True,
    )


@pytest.fixture
def dml_update_trigger():
    return Trigger(
        name="on_update",
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={"table": "parcels", "operation": "UPDATE"},
        enabled=True,
    )


@pytest.fixture
def dml_delete_trigger():
    return Trigger(
        name="on_delete",
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={"table": "parcels", "operation": "DELETE"},
        enabled=True,
    )


@pytest.fixture
def catch_all_trigger():
    return Trigger(
        name="catch_all",
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={},
        enabled=True,
    )


@pytest.fixture
def disabled_trigger():
    return Trigger(
        name="disabled",
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={"table": "parcels", "operation": "INSERT"},
        enabled=False,
    )


@pytest.fixture
def parcels_gpkg(tmp_path) -> str:
    gdf = gpd.GeoDataFrame(
        {"name": ["A", "B", "C"], "area": [100, 200, 300]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(2, 0, 3, 1)],
        crs="EPSG:4326",
    )
    path = str(tmp_path / "parcels.gpkg")
    gdf.to_file(path, layer="parcels", driver="GPKG")
    return path


# ===========================================================================
# SpatiaLite backend
# ===========================================================================


class TestSpatiaLiteTriggers:
    """SpatiaLite: change_log → polling → TriggerEvaluator."""

    @pytest.fixture
    def session(self, parcels_gpkg):
        s = SpatiaLiteSession(db_path=":memory:")
        s.open()
        s.load_gpkg(parcels_gpkg, layer="parcels")
        yield s
        s.close()

    def test_insert_detected(self, session, dml_insert_trigger):
        session._triggers = [dml_insert_trigger]
        session.conn.execute(
            "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
            ("D", 400, "POINT(0 0)"),
        )
        session.conn.commit()
        fired = session._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1
        assert matched[0].result_summary["trigger_name"] == "on_insert"

    def test_update_detected(self, session, dml_update_trigger):
        session._triggers = [dml_update_trigger]
        session.conn.execute("UPDATE parcels SET area = 999 WHERE name = 'A'")
        session.conn.commit()
        fired = session._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1

    def test_delete_detected(self, session, dml_delete_trigger):
        session._triggers = [dml_delete_trigger]
        session.conn.execute("DELETE FROM parcels WHERE name = 'A'")
        session.conn.commit()
        fired = session._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1

    def test_disabled_trigger_skipped(self, session, disabled_trigger):
        session._triggers = [disabled_trigger]
        session.conn.execute(
            "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
            ("E", 500, "POINT(0 0)"),
        )
        session.conn.commit()
        fired = session._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 0

    def test_catch_all_fires_on_any_op(self, session, catch_all_trigger):
        session._triggers = [catch_all_trigger]
        session.conn.execute(
            "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
            ("F", 600, "POINT(0 0)"),
        )
        session.conn.commit()
        fired = session._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1

    def test_multiple_triggers_same_event(
        self, session, dml_insert_trigger, catch_all_trigger, disabled_trigger
    ):
        session._triggers = [dml_insert_trigger, catch_all_trigger, disabled_trigger]
        session.conn.execute(
            "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
            ("G", 700, "POINT(0 0)"),
        )
        session.conn.commit()
        fired = session._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        names = {f.result_summary["trigger_name"] for f in matched}
        assert "on_insert" in names
        assert "catch_all" in names
        assert "disabled" not in names

    def test_processed_not_reprocessed(self, session, dml_insert_trigger):
        session._triggers = [dml_insert_trigger]
        session.conn.execute(
            "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
            ("H", 800, "POINT(0 0)"),
        )
        session.conn.commit()
        session._process_pending_changes("test")
        second = session._process_pending_changes("test")
        assert len(second) == 0

    def test_change_records_accumulated(self, session, dml_insert_trigger):
        session._triggers = [dml_insert_trigger]
        for i in range(3):
            session.conn.execute(
                "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
                (f"X{i}", i * 100, "POINT(0 0)"),
            )
        session.conn.commit()
        session._process_pending_changes("test")
        assert len(session.change_records) == 3


# ===========================================================================
# DuckDB backend
# ===========================================================================


class TestDuckDBTriggers:
    """DuckDB: write proxy + change_log → TriggerEvaluator."""

    @pytest.fixture
    def detector(self):
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE parcels (id INTEGER, name TEXT, area DOUBLE)")
        conn.execute("INSERT INTO parcels VALUES (1, 'A', 100), (2, 'B', 200), (3, 'C', 300)")
        return DuckDBChangeDetector(conn)

    def test_insert_detected(self, detector, dml_insert_trigger):
        detector._triggers = [dml_insert_trigger]
        detector.execute("INSERT INTO parcels VALUES (4, 'D', 400)")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1
        assert matched[0].result_summary["trigger_name"] == "on_insert"

    def test_update_detected(self, detector, dml_update_trigger):
        detector._triggers = [dml_update_trigger]
        detector.execute("UPDATE parcels SET area = 999 WHERE id = 1")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1

    def test_delete_detected(self, detector, dml_delete_trigger):
        detector._triggers = [dml_delete_trigger]
        detector.execute("DELETE FROM parcels WHERE id = 1")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1

    def test_disabled_trigger_skipped(self, detector, disabled_trigger):
        detector._triggers = [disabled_trigger]
        detector.execute("INSERT INTO parcels VALUES (4, 'D', 400)")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 0

    def test_catch_all_fires_on_any_op(self, detector, catch_all_trigger):
        detector._triggers = [catch_all_trigger]
        detector.execute("UPDATE parcels SET area = 1 WHERE id = 1")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1

    def test_multiple_triggers(
        self, detector, dml_insert_trigger, catch_all_trigger, disabled_trigger
    ):
        detector._triggers = [dml_insert_trigger, catch_all_trigger, disabled_trigger]
        detector.execute("INSERT INTO parcels VALUES (4, 'D', 400)")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        names = {f.result_summary["trigger_name"] for f in matched}
        assert "on_insert" in names
        assert "catch_all" in names
        assert "disabled" not in names

    def test_processed_not_reprocessed(self, detector, dml_insert_trigger):
        detector._triggers = [dml_insert_trigger]
        detector.execute("INSERT INTO parcels VALUES (4, 'D', 400)")
        detector._process_pending_changes("test")
        second = detector._process_pending_changes("test")
        assert len(second) == 0

    def test_select_not_logged(self, detector, catch_all_trigger):
        detector._triggers = [catch_all_trigger]
        detector.execute("SELECT * FROM parcels")
        fired = detector._process_pending_changes("test")
        assert len(fired) == 0

    def test_batch_changes(self, detector, catch_all_trigger):
        detector._triggers = [catch_all_trigger]
        detector.execute("INSERT INTO parcels VALUES (4, 'D', 400)")
        detector.execute("UPDATE parcels SET area = 1 WHERE id = 1")
        detector.execute("DELETE FROM parcels WHERE id = 2")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 3
        ops = [r.operation for r in detector.change_records]
        assert ChangeOperation.INSERT in ops
        assert ChangeOperation.UPDATE in ops
        assert ChangeOperation.DELETE in ops


# ===========================================================================
# Cross-backend consistency
# ===========================================================================


class TestCrossBackendConsistency:
    """Verify SpatiaLite and DuckDB produce equivalent trigger results."""

    def test_insert_fires_same_trigger_both_backends(
        self, parcels_gpkg, dml_insert_trigger
    ):
        # SpatiaLite
        s = SpatiaLiteSession(db_path=":memory:")
        s.open()
        s.load_gpkg(parcels_gpkg, layer="parcels")
        s._triggers = [dml_insert_trigger]
        s.conn.execute(
            "INSERT INTO parcels (name, area, geometry) VALUES (?, ?, ?)",
            ("Z", 999, "POINT(0 0)"),
        )
        s.conn.commit()
        sl_fired = s._process_pending_changes("test")
        s.close()

        # DuckDB
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE parcels (id INTEGER, name TEXT, area DOUBLE)")
        conn.execute("INSERT INTO parcels VALUES (1, 'A', 100)")
        d = DuckDBChangeDetector(conn)
        d._triggers = [dml_insert_trigger]
        d.execute("INSERT INTO parcels VALUES (2, 'Z', 999)")
        dk_fired = d._process_pending_changes("test")

        # Both should fire the same trigger
        sl_matched = [f for f in sl_fired if f.matched]
        dk_matched = [f for f in dk_fired if f.matched]
        assert len(sl_matched) == len(dk_matched) == 1
        assert sl_matched[0].result_summary["trigger_name"] == dk_matched[0].result_summary["trigger_name"]
