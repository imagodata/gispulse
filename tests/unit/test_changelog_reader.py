"""Tests for ``persistence.changelog_reader`` — read primitives shared
between the CLI's ``gispulse track tail/list`` and the HTTP endpoints
introduced by issue #93 (``GET /datasets/{id}/changelog`` + ``/stats``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gispulse.persistence.changelog_reader import (
    ChangelogReaderError,
    changelog_stats,
    list_pending_changes,
    next_since_id,
)
from gispulse.persistence.gpkg_schema import bootstrap_gpkg_project, install_change_tracking


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tracked_gpkg(tmp_path: Path) -> Path:
    """Bootstrap a GPKG with two tracked layers + a few seeded rows."""
    path = tmp_path / "tracked.gpkg"
    conn = sqlite3.connect(str(path), isolation_level=None)
    bootstrap_gpkg_project(conn)
    conn.execute(
        'CREATE TABLE "parcels" (fid INTEGER PRIMARY KEY, name TEXT)'
    )
    conn.execute(
        'CREATE TABLE "buildings" (fid INTEGER PRIMARY KEY, height REAL)'
    )
    conn.commit()
    install_change_tracking(conn, "parcels")
    install_change_tracking(conn, "buildings")
    # Seed the change-log via real INSERT/UPDATE/DELETE so the trigger
    # populates row_pk + changed_at.
    conn.execute('INSERT INTO "parcels"(fid, name) VALUES (1, "alpha")')
    conn.execute('INSERT INTO "parcels"(fid, name) VALUES (2, "beta")')
    conn.execute('UPDATE "parcels" SET name="gamma" WHERE fid=1')
    conn.execute('INSERT INTO "buildings"(fid, height) VALUES (1, 12.5)')
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def conn(tracked_gpkg: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tracked_gpkg), isolation_level=None)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


# ---------------------------------------------------------------------------
# list_pending_changes
# ---------------------------------------------------------------------------


class TestListPendingChanges:
    def test_returns_all_unprocessed_rows_default(self, conn) -> None:
        rows = list_pending_changes(conn)
        # Seeding inserted 2+1+1 = 4 changes (3 parcels ops + 1 building).
        assert len(rows) == 4
        # Ordered by id ASC.
        ids = [r["id"] for r in rows]
        assert ids == sorted(ids)
        # Schema columns present.
        for r in rows:
            assert set(r.keys()) >= {
                "id",
                "table_name",
                "operation",
                "row_pk",
                "changed_at",
                "geom_changed",
            }

    def test_filter_by_layer(self, conn) -> None:
        rows = list_pending_changes(conn, layer="parcels")
        assert all(r["table_name"] == "parcels" for r in rows)
        assert len(rows) == 3

    def test_filter_by_op_case_insensitive(self, conn) -> None:
        rows_upper = list_pending_changes(conn, op="INSERT")
        rows_lower = list_pending_changes(conn, op="insert")
        assert {r["id"] for r in rows_upper} == {r["id"] for r in rows_lower}
        assert all(r["operation"] == "INSERT" for r in rows_upper)

    def test_invalid_op_rejected(self, conn) -> None:
        with pytest.raises(ValueError, match="op must be"):
            list_pending_changes(conn, op="UPSERT")

    def test_since_id_pagination(self, conn) -> None:
        page1 = list_pending_changes(conn, limit=2)
        assert len(page1) == 2
        cursor = next_since_id(page1)
        page2 = list_pending_changes(conn, since_id=cursor, limit=10)
        # No overlap, ordered.
        assert all(r["id"] > cursor for r in page2)

    def test_limit_bounds(self, conn) -> None:
        with pytest.raises(ValueError, match="limit must be"):
            list_pending_changes(conn, limit=0)
        with pytest.raises(ValueError, match="limit must be"):
            list_pending_changes(conn, limit=501)

    def test_missing_changelog_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.gpkg"
        # Plain SQLite file — no _gispulse_change_log table.
        c = sqlite3.connect(str(path), isolation_level=None)
        try:
            with pytest.raises(ChangelogReaderError):
                list_pending_changes(c)
        finally:
            c.close()


class TestNextSinceId:
    def test_max_id_when_rows_present(self) -> None:
        rows = [{"id": 1}, {"id": 5}, {"id": 3}]
        assert next_since_id(rows) == 5

    def test_fallback_when_empty(self) -> None:
        assert next_since_id([], fallback=42) == 42

    def test_fallback_default_zero(self) -> None:
        assert next_since_id([]) == 0


# ---------------------------------------------------------------------------
# changelog_stats
# ---------------------------------------------------------------------------


class TestChangelogStats:
    def test_per_layer_aggregates(self, conn) -> None:
        stats = changelog_stats(conn)
        assert stats["total_pending"] == 4
        assert stats["total_processed"] == 0
        assert {b["layer"] for b in stats["by_layer"]} == {"parcels", "buildings"}
        parcels = next(b for b in stats["by_layer"] if b["layer"] == "parcels")
        assert parcels["pending"] == 3
        assert parcels["by_op"]["INSERT"] == 2
        assert parcels["by_op"]["UPDATE"] == 1
        assert parcels["by_op"]["DELETE"] == 0

    def test_processed_rows_counted_separately(self, conn) -> None:
        # Mark two parcels rows processed.
        conn.execute(
            "UPDATE _gispulse_change_log SET processed = 1 "
            "WHERE id IN (SELECT id FROM _gispulse_change_log "
            "             WHERE table_name='parcels' LIMIT 2)"
        )
        stats = changelog_stats(conn)
        assert stats["total_pending"] == 2  # 1 parcels + 1 building
        assert stats["total_processed"] == 2
        parcels = next(b for b in stats["by_layer"] if b["layer"] == "parcels")
        assert parcels["pending"] == 1
        assert parcels["processed"] == 2

    def test_missing_changelog_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.gpkg"
        c = sqlite3.connect(str(path), isolation_level=None)
        try:
            with pytest.raises(ChangelogReaderError):
                changelog_stats(c)
        finally:
            c.close()

    def test_alphabetical_layer_order(self, conn) -> None:
        stats = changelog_stats(conn)
        names = [b["layer"] for b in stats["by_layer"]]
        assert names == sorted(names)
