"""Unit tests for ChangeRecord / ChangeSet / FiredTrigger (P-5 #73)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from gispulse.core.models import (
    ChangeOperation,
    ChangeRecord,
    ChangeSet,
    FiredTrigger,
)


# ---------------------------------------------------------------------------
# ChangeOperation
# ---------------------------------------------------------------------------


class TestChangeOperation:
    def test_values(self):
        assert ChangeOperation.INSERT.value == "INSERT"
        assert ChangeOperation.UPDATE.value == "UPDATE"
        assert ChangeOperation.DELETE.value == "DELETE"

    def test_str_enum(self):
        assert ChangeOperation.INSERT == "INSERT"


# ---------------------------------------------------------------------------
# ChangeRecord
# ---------------------------------------------------------------------------


class TestChangeRecord:
    def test_defaults(self):
        rec = ChangeRecord()
        assert isinstance(rec.id, UUID)
        assert rec.session_id == ""
        assert rec.table_name == ""
        assert rec.feature_id is None
        assert rec.operation == ChangeOperation.INSERT
        assert rec.old_values == {}
        assert rec.new_values == {}
        assert rec.old_geom_wkt is None
        assert rec.new_geom_wkt is None
        assert isinstance(rec.recorded_at, datetime)

    def test_insert_record(self):
        rec = ChangeRecord(
            session_id="session_abc123",
            table_name="session_abc123.parcelles",
            feature_id="42",
            operation=ChangeOperation.INSERT,
            new_values={"code_iris": "75014A", "surface_m2": 1200.5},
            new_geom_wkt="POLYGON((2.3 48.8, 2.4 48.8, 2.4 48.9, 2.3 48.8))",
        )
        assert rec.session_id == "session_abc123"
        assert rec.operation == ChangeOperation.INSERT
        assert rec.old_values == {}
        assert rec.new_values["surface_m2"] == 1200.5
        assert rec.old_geom_wkt is None
        assert rec.new_geom_wkt is not None

    def test_update_record(self):
        rec = ChangeRecord(
            session_id="session_abc123",
            table_name="session_abc123.parcelles",
            feature_id="42",
            operation=ChangeOperation.UPDATE,
            old_values={"surface_m2": 1000.0},
            new_values={"surface_m2": 1200.5},
        )
        assert rec.operation == ChangeOperation.UPDATE
        assert rec.old_values["surface_m2"] == 1000.0
        assert rec.new_values["surface_m2"] == 1200.5

    def test_delete_record(self):
        rec = ChangeRecord(
            session_id="session_abc123",
            table_name="session_abc123.parcelles",
            feature_id="42",
            operation=ChangeOperation.DELETE,
            old_values={"code_iris": "75014A"},
        )
        assert rec.operation == ChangeOperation.DELETE
        assert rec.new_values == {}

    def test_unique_ids(self):
        r1 = ChangeRecord()
        r2 = ChangeRecord()
        assert r1.id != r2.id


# ---------------------------------------------------------------------------
# ChangeSet
# ---------------------------------------------------------------------------


class TestChangeSet:
    def test_defaults(self):
        cs = ChangeSet()
        assert isinstance(cs.id, UUID)
        assert cs.session_id == ""
        assert cs.source_client is None
        assert cs.records == []
        assert isinstance(cs.created_at, datetime)
        assert cs.committed_at is None

    def test_add_records(self):
        r1 = ChangeRecord(operation=ChangeOperation.INSERT, feature_id="1")
        r2 = ChangeRecord(operation=ChangeOperation.UPDATE, feature_id="2")
        cs = ChangeSet(
            session_id="session_abc123",
            source_client="qgis",
            records=[r1, r2],
        )
        assert len(cs.records) == 2
        assert cs.records[0].feature_id == "1"
        assert cs.source_client == "qgis"

    def test_commit(self):
        cs = ChangeSet()
        assert cs.committed_at is None
        now = datetime.now(timezone.utc)
        cs.committed_at = now
        assert cs.committed_at == now

    def test_source_clients(self):
        for client in ("qgis", "arcgis", "portal", "cli"):
            cs = ChangeSet(source_client=client)
            assert cs.source_client == client

    def test_unique_ids(self):
        cs1 = ChangeSet()
        cs2 = ChangeSet()
        assert cs1.id != cs2.id

    def test_records_independent(self):
        """Each ChangeSet has its own records list (no shared mutable default)."""
        cs1 = ChangeSet()
        cs2 = ChangeSet()
        cs1.records.append(ChangeRecord())
        assert len(cs2.records) == 0


# ---------------------------------------------------------------------------
# FiredTrigger
# ---------------------------------------------------------------------------


class TestFiredTrigger:
    def test_defaults(self):
        ft = FiredTrigger()
        assert isinstance(ft.id, UUID)
        assert isinstance(ft.trigger_id, UUID)
        assert ft.change_record_id is None
        assert ft.changeset_id is None
        assert ft.matched is False
        assert ft.actions_dispatched == []
        assert ft.eval_time_ms == 0.0
        assert ft.result_summary == {}
        assert isinstance(ft.fired_at, datetime)

    def test_matched_trigger(self):
        rec = ChangeRecord(feature_id="99")
        cs = ChangeSet()
        ft = FiredTrigger(
            change_record_id=rec.id,
            changeset_id=cs.id,
            matched=True,
            actions_dispatched=["notify", "set_field"],
            eval_time_ms=12.3,
            result_summary={"new_statut": "zone_sensible"},
        )
        assert ft.matched is True
        assert "notify" in ft.actions_dispatched
        assert ft.eval_time_ms == 12.3
        assert ft.result_summary["new_statut"] == "zone_sensible"
        assert ft.change_record_id == rec.id
        assert ft.changeset_id == cs.id

    def test_unmatched_trigger(self):
        ft = FiredTrigger(matched=False, eval_time_ms=0.8)
        assert ft.matched is False
        assert ft.actions_dispatched == []

    def test_unique_ids(self):
        ft1 = FiredTrigger()
        ft2 = FiredTrigger()
        assert ft1.id != ft2.id

    def test_actions_independent(self):
        """Each FiredTrigger has its own actions list."""
        ft1 = FiredTrigger()
        ft2 = FiredTrigger()
        ft1.actions_dispatched.append("notify")
        assert len(ft2.actions_dispatched) == 0
