"""Tests for DuckDB change detector — application-level change detection."""
from __future__ import annotations

import duckdb
import pytest

from gispulse.core.models import ChangeOperation, Trigger, TriggerEvent, TriggerType
from gispulse.persistence.duckdb_change_detector import DuckDBChangeDetector


@pytest.fixture
def conn():
    """In-memory DuckDB connection with a test table."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE parcels (id INTEGER, name TEXT, area DOUBLE)")
    c.execute("INSERT INTO parcels VALUES (1, 'A', 100), (2, 'B', 200)")
    return c


@pytest.fixture
def detector(conn):
    return DuckDBChangeDetector(conn)


@pytest.fixture
def insert_trigger():
    return Trigger(
        name="on_insert",
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={"table": "parcels", "operation": "INSERT"},
        enabled=True,
    )


@pytest.fixture
def update_trigger():
    return Trigger(
        name="on_update",
        event=TriggerEvent.DATA_CHANGED,
        trigger_type=TriggerType.DML,
        conditions={"table": "parcels", "operation": "UPDATE"},
        enabled=True,
    )


class TestChangeDetection:
    def test_change_log_created(self, detector, conn):
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        assert "_change_log" in tables

    def test_insert_detected(self, detector):
        detector.execute("INSERT INTO parcels VALUES (3, 'C', 300)")
        fired = detector._process_pending_changes("test")
        assert len(detector.change_records) == 1
        assert detector.change_records[0].operation == ChangeOperation.INSERT
        assert detector.change_records[0].table_name == "parcels"

    def test_update_detected(self, detector):
        detector.execute("UPDATE parcels SET area = 999 WHERE id = 1")
        detector._process_pending_changes("test")
        assert len(detector.change_records) == 1
        assert detector.change_records[0].operation == ChangeOperation.UPDATE

    def test_delete_detected(self, detector):
        detector.execute("DELETE FROM parcels WHERE id = 1")
        detector._process_pending_changes("test")
        assert len(detector.change_records) == 1
        assert detector.change_records[0].operation == ChangeOperation.DELETE

    def test_select_not_logged(self, detector):
        detector.execute("SELECT * FROM parcels")
        detector._process_pending_changes("test")
        assert len(detector.change_records) == 0

    def test_ddl_not_logged(self, detector):
        detector.execute("CREATE TABLE tmp (x INT)")
        detector._process_pending_changes("test")
        assert len(detector.change_records) == 0

    def test_multiple_changes(self, detector):
        detector.execute("INSERT INTO parcels VALUES (3, 'C', 300)")
        detector.execute("UPDATE parcels SET area = 999 WHERE id = 1")
        detector.execute("DELETE FROM parcels WHERE id = 2")
        detector._process_pending_changes("test")
        assert len(detector.change_records) == 3

    def test_processed_not_reprocessed(self, detector):
        detector.execute("INSERT INTO parcels VALUES (3, 'C', 300)")
        detector._process_pending_changes("test")
        # Second call should not produce new records
        detector._process_pending_changes("test")
        assert len(detector.change_records) == 1


class TestTriggerEvaluation:
    def test_insert_fires_trigger(self, detector, insert_trigger):
        detector._triggers = [insert_trigger]
        detector.execute("INSERT INTO parcels VALUES (3, 'C', 300)")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1
        assert matched[0].result_summary["trigger_name"] == "on_insert"

    def test_update_fires_update_trigger(self, detector, update_trigger):
        detector._triggers = [update_trigger]
        detector.execute("UPDATE parcels SET area = 999 WHERE id = 1")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1
        assert matched[0].result_summary["trigger_name"] == "on_update"

    def test_insert_does_not_fire_update_trigger(self, detector, update_trigger):
        detector._triggers = [update_trigger]
        detector.execute("INSERT INTO parcels VALUES (3, 'C', 300)")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 0

    def test_multiple_triggers_evaluated(self, detector, insert_trigger, update_trigger):
        detector._triggers = [insert_trigger, update_trigger]
        detector.execute("INSERT INTO parcels VALUES (3, 'C', 300)")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 1  # Only insert trigger fires

    def test_disabled_trigger_not_evaluated(self, detector):
        disabled = Trigger(
            name="disabled",
            event=TriggerEvent.DATA_CHANGED,
            trigger_type=TriggerType.DML,
            conditions={"table": "parcels", "operation": "INSERT"},
            enabled=False,
        )
        detector._triggers = [disabled]
        detector.execute("INSERT INTO parcels VALUES (3, 'C', 300)")
        fired = detector._process_pending_changes("test")
        matched = [f for f in fired if f.matched]
        assert len(matched) == 0

    def test_fired_triggers_accumulated(self, detector, insert_trigger):
        detector._triggers = [insert_trigger]
        detector.execute("INSERT INTO parcels VALUES (3, 'C', 300)")
        detector._process_pending_changes("test")
        detector.execute("INSERT INTO parcels VALUES (4, 'D', 400)")
        detector._process_pending_changes("test")
        assert len(detector.fired_triggers) == 2

    def test_clear_fired(self, detector, insert_trigger):
        detector._triggers = [insert_trigger]
        detector.execute("INSERT INTO parcels VALUES (3, 'C', 300)")
        detector._process_pending_changes("test")
        detector.clear_fired()
        assert len(detector.fired_triggers) == 0

    def test_clear_change_records(self, detector, insert_trigger):
        detector._triggers = [insert_trigger]
        detector.execute("INSERT INTO parcels VALUES (3, 'C', 300)")
        detector._process_pending_changes("test")
        detector.clear_change_records()
        assert len(detector.change_records) == 0
