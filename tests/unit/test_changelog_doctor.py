"""Tests for ``persistence.changelog_doctor`` — health-check + auto-fix
extracted from ``gispulse track doctor`` and shared with the new HTTP
endpoint ``POST /datasets/{id}/changelog/doctor`` (issue #93)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gispulse.persistence.changelog_doctor import (
    GPKG_APP_ID,
    STATUS_FAIL,
    STATUS_FIXED,
    STATUS_OK,
    STATUS_WARN,
    health_score,
    run_doctor,
)
from gispulse.persistence.gpkg_schema import bootstrap_gpkg_project, install_change_tracking


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def healthy_gpkg(tmp_path: Path) -> Path:
    path = tmp_path / "healthy.gpkg"
    conn = sqlite3.connect(str(path), isolation_level=None)
    bootstrap_gpkg_project(conn)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(
        'CREATE TABLE "parcels" (fid INTEGER PRIMARY KEY, name TEXT)'
    )
    conn.execute(
        "INSERT INTO gpkg_contents(table_name, data_type, identifier) "
        "VALUES('parcels', 'features', 'parcels')"
    )
    conn.commit()
    install_change_tracking(conn, "parcels")
    conn.close()
    return path


def _open(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(path), isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# run_doctor — happy path
# ---------------------------------------------------------------------------


class TestRunDoctorHappyPath:
    def test_healthy_gpkg_returns_ok(self, healthy_gpkg: Path) -> None:
        with _open(healthy_gpkg) as conn:
            result = run_doctor(conn)
        assert result["ok"] is True
        assert result["status"] == STATUS_OK
        assert result["errors"] == 0
        assert result["repaired"] == []
        # Every check should be non-fail.
        assert all(c["status"] != STATUS_FAIL for c in result["checks"])

    def test_application_id_check(self, healthy_gpkg: Path) -> None:
        with _open(healthy_gpkg) as conn:
            result = run_doctor(conn)
        app_check = next(c for c in result["checks"] if c["check"] == "application_id")
        assert app_check["status"] == STATUS_OK
        assert str(GPKG_APP_ID) in app_check["detail"]

    def test_changelog_table_present(self, healthy_gpkg: Path) -> None:
        with _open(healthy_gpkg) as conn:
            result = run_doctor(conn)
        cl = next(c for c in result["checks"] if c["check"] == "changelog_table")
        assert cl["status"] == STATUS_OK


# ---------------------------------------------------------------------------
# run_doctor — failure modes
# ---------------------------------------------------------------------------


class TestRunDoctorFailureModes:
    def test_missing_application_id_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "noappid.gpkg"
        c = sqlite3.connect(str(path), isolation_level=None)
        # Plain SQLite — no PRAGMA application_id, no _gispulse_change_log
        c.row_factory = sqlite3.Row
        try:
            result = run_doctor(c)
        finally:
            c.close()
        assert result["ok"] is False
        app = next(c for c in result["checks"] if c["check"] == "application_id")
        assert app["status"] == STATUS_FAIL
        cl = next(c for c in result["checks"] if c["check"] == "changelog_table")
        assert cl["status"] == STATUS_FAIL

    def test_missing_trigger_without_autofix_fails(
        self, healthy_gpkg: Path
    ) -> None:
        # Drop the UPDATE trigger to simulate partial install.
        conn = _open(healthy_gpkg)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE '\\_gispulse\\_trg\\_parcels\\_update' ESCAPE '\\'"
        )
        trg_row = cursor.fetchone()
        if trg_row:
            conn.execute(f'DROP TRIGGER "{trg_row[0]}"')
        conn.commit()
        try:
            result = run_doctor(conn, auto_fix=False)
        finally:
            conn.close()
        assert result["ok"] is False
        trig_fails = [
            entry
            for entry in result["checks"]
            if entry["check"] == "triggers" and entry["status"] == STATUS_FAIL
        ]
        assert len(trig_fails) == 1
        assert "update" in trig_fails[0]["missing"]

    def test_missing_trigger_with_autofix_repairs(
        self, healthy_gpkg: Path
    ) -> None:
        conn = _open(healthy_gpkg)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE '\\_gispulse\\_trg\\_parcels\\_update' ESCAPE '\\'"
        )
        trg_row = cursor.fetchone()
        if trg_row:
            conn.execute(f'DROP TRIGGER "{trg_row[0]}"')
        conn.commit()
        try:
            result = run_doctor(conn, auto_fix=True)
        finally:
            conn.close()
        assert result["ok"] is True
        assert "parcels" in result["repaired"]
        # The triggers check should now report ``fixed``.
        fixed = [entry for entry in result["checks"] if entry["status"] == STATUS_FIXED]
        assert len(fixed) >= 1


# ---------------------------------------------------------------------------
# run_doctor — warning paths (don't flip ok=False)
# ---------------------------------------------------------------------------


class TestRunDoctorWarnings:
    def test_non_wal_journal_warns(self, tmp_path: Path) -> None:
        path = tmp_path / "rollback.gpkg"
        conn = sqlite3.connect(str(path), isolation_level=None)
        bootstrap_gpkg_project(conn)
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute(
            'CREATE TABLE "parcels" (fid INTEGER PRIMARY KEY, name TEXT)'
        )
        conn.execute(
            "INSERT INTO gpkg_contents(table_name, data_type, identifier) "
            "VALUES('parcels', 'features', 'parcels')"
        )
        conn.commit()
        install_change_tracking(conn, "parcels")
        try:
            result = run_doctor(conn)
        finally:
            conn.close()
        journal = next(
            c for c in result["checks"] if c["check"] == "journal_mode"
        )
        assert journal["status"] == STATUS_WARN
        # Warnings don't fail the doctor.
        assert result["ok"] is True

    def test_low_busy_timeout_warns(self, tmp_path: Path) -> None:
        path = tmp_path / "busy.gpkg"
        conn = sqlite3.connect(str(path), isolation_level=None)
        bootstrap_gpkg_project(conn)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 100")
        conn.execute(
            'CREATE TABLE "parcels" (fid INTEGER PRIMARY KEY, name TEXT)'
        )
        conn.execute(
            "INSERT INTO gpkg_contents(table_name, data_type, identifier) "
            "VALUES('parcels', 'features', 'parcels')"
        )
        conn.commit()
        install_change_tracking(conn, "parcels")
        try:
            result = run_doctor(conn)
        finally:
            conn.close()
        bt = next(c for c in result["checks"] if c["check"] == "busy_timeout")
        assert bt["status"] == STATUS_WARN
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# health_score
# ---------------------------------------------------------------------------


class TestHealthScore:
    def test_all_ok_is_100(self) -> None:
        checks = [
            {"check": "a", "status": STATUS_OK},
            {"check": "b", "status": STATUS_OK},
        ]
        assert health_score(checks) == 100

    def test_one_fail_minus_25(self) -> None:
        checks = [
            {"check": "a", "status": STATUS_OK},
            {"check": "b", "status": STATUS_FAIL},
        ]
        assert health_score(checks) == 75

    def test_one_warn_minus_5(self) -> None:
        checks = [{"check": "a", "status": STATUS_WARN}]
        assert health_score(checks) == 95

    def test_floors_at_zero(self) -> None:
        checks = [{"check": f"{i}", "status": STATUS_FAIL} for i in range(10)]
        assert health_score(checks) == 0

    def test_fixed_is_neutral(self) -> None:
        checks = [{"check": "a", "status": STATUS_FIXED}]
        assert health_score(checks) == 100

    def test_empty_is_100(self) -> None:
        assert health_score([]) == 100
