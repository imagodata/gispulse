"""Tests for v2 change-tracking payload (#7).

Covers:
    * ``new_values`` / ``old_values`` JSON populated by INSERT/UPDATE/DELETE
      triggers.
    * ``geom_changed`` flag (1 when geometry actually changed, 0 otherwise).
    * SQLi guards extended to attribute column names.
    * v1 → v2 migration via :func:`bootstrap_gpkg_project`.
    * Backward-compat: ``install_change_tracking`` works on a layer with no
      geometry column (geom_changed always 0).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from persistence.gpkg_schema import (
    _build_change_triggers,
    _migrate_v1_to_v2,
    bootstrap_gpkg_project,
    install_change_tracking,
)


# ---------------------------------------------------------------------------
# DDL builder unit tests
# ---------------------------------------------------------------------------


def test_build_change_triggers_with_columns_emits_json_object() -> None:
    sqls = _build_change_triggers(
        "parcels", "fid", columns=["status", "owner"], geom_col="geom"
    )
    assert len(sqls) == 3
    insert_sql, update_sql, delete_sql = sqls
    # Every payload must reference json_object on the appropriate columns.
    assert "json_object('status', NEW.\"status\", 'owner', NEW.\"owner\")" in insert_sql
    assert "json_object('status', NEW.\"status\", 'owner', NEW.\"owner\")" in update_sql
    assert "json_object('status', OLD.\"status\", 'owner', OLD.\"owner\")" in update_sql
    assert "json_object('status', OLD.\"status\", 'owner', OLD.\"owner\")" in delete_sql
    # geom_changed expressions
    assert '(NEW."geom" IS NOT NULL)' in insert_sql
    assert '(NEW."geom" IS NOT OLD."geom")' in update_sql
    assert '(OLD."geom" IS NOT NULL)' in delete_sql


def test_build_change_triggers_no_geom_column_emits_zero() -> None:
    sqls = _build_change_triggers(
        "lookup", "id", columns=["label"], geom_col=None
    )
    for sql in sqls:
        # Without a geom column, the flag is a hard zero.
        assert " 0)" in sql or ", 0);" in sql


def test_build_change_triggers_empty_columns_emits_empty_json() -> None:
    sqls = _build_change_triggers("audit", "id", columns=[], geom_col=None)
    for sql in sqls:
        assert "json_object()" in sql


def test_build_change_triggers_rejects_quoted_column() -> None:
    with pytest.raises(ValueError):
        _build_change_triggers(
            "parcels", "fid", columns=["status\"; DROP TABLE x; --"], geom_col=None
        )


def test_build_change_triggers_rejects_quoted_geom() -> None:
    with pytest.raises(ValueError):
        _build_change_triggers(
            "parcels", "fid", columns=["status"], geom_col="geom\"; --"
        )


# ---------------------------------------------------------------------------
# End-to-end DML round-trip
# ---------------------------------------------------------------------------


@pytest.fixture()
def spatial_gpkg(tmp_path: Path) -> Path:
    """Build a GPKG with a parcels(fid, name, status, geom) layer registered
    in gpkg_geometry_columns so install_change_tracking can auto-detect."""
    path = tmp_path / "spatial.gpkg"
    conn = sqlite3.connect(str(path), isolation_level=None)
    bootstrap_gpkg_project(conn)
    conn.execute(
        'CREATE TABLE "parcels" '
        "(fid INTEGER PRIMARY KEY, name TEXT, status TEXT, geom BLOB)"
    )
    conn.execute(
        "INSERT INTO gpkg_contents(table_name,data_type,identifier) "
        "VALUES('parcels','features','parcels')"
    )
    conn.execute(
        "INSERT INTO gpkg_geometry_columns(table_name, column_name, "
        "geometry_type_name, srs_id) VALUES('parcels', 'geom', 'POLYGON', 4326)"
    )
    install_change_tracking(conn, "parcels")
    conn.close()
    return path


def _changelog(path: Path) -> list[dict]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT operation, row_pk, new_values, old_values, geom_changed "
        "FROM _gispulse_change_log ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_insert_populates_new_values_and_geom_changed(spatial_gpkg: Path) -> None:
    conn = sqlite3.connect(str(spatial_gpkg))
    conn.execute(
        "INSERT INTO parcels(fid, name, status, geom) VALUES (1, 'a', 'draft', x'0102')"
    )
    conn.commit()
    conn.close()

    rows = _changelog(spatial_gpkg)
    assert len(rows) == 1
    r = rows[0]
    assert r["operation"] == "INSERT"
    assert json.loads(r["new_values"]) == {"name": "a", "status": "draft"}
    assert r["old_values"] is None
    assert r["geom_changed"] == 1


def test_update_attribute_only_marks_geom_unchanged(spatial_gpkg: Path) -> None:
    conn = sqlite3.connect(str(spatial_gpkg))
    conn.execute(
        "INSERT INTO parcels(fid, name, status, geom) VALUES (1, 'a', 'draft', x'0102')"
    )
    conn.execute("UPDATE parcels SET status='published' WHERE fid=1")
    conn.commit()
    conn.close()

    rows = _changelog(spatial_gpkg)
    upd = next(r for r in rows if r["operation"] == "UPDATE")
    assert json.loads(upd["new_values"])["status"] == "published"
    assert json.loads(upd["old_values"])["status"] == "draft"
    assert upd["geom_changed"] == 0  # ← key assertion


def test_update_geom_only_marks_geom_changed(spatial_gpkg: Path) -> None:
    conn = sqlite3.connect(str(spatial_gpkg))
    conn.execute(
        "INSERT INTO parcels(fid, name, status, geom) VALUES (1, 'a', 'draft', x'0102')"
    )
    conn.execute("UPDATE parcels SET geom=x'0304' WHERE fid=1")
    conn.commit()
    conn.close()

    rows = _changelog(spatial_gpkg)
    upd = next(r for r in rows if r["operation"] == "UPDATE")
    assert upd["geom_changed"] == 1


def test_delete_populates_old_values(spatial_gpkg: Path) -> None:
    conn = sqlite3.connect(str(spatial_gpkg))
    conn.execute(
        "INSERT INTO parcels(fid, name, status, geom) VALUES (1, 'a', 'draft', x'0102')"
    )
    conn.execute("DELETE FROM parcels WHERE fid=1")
    conn.commit()
    conn.close()

    rows = _changelog(spatial_gpkg)
    deletion = next(r for r in rows if r["operation"] == "DELETE")
    assert json.loads(deletion["old_values"]) == {"name": "a", "status": "draft"}
    assert deletion["new_values"] is None
    assert deletion["geom_changed"] == 1


# ---------------------------------------------------------------------------
# Non-spatial layer (no geom column)
# ---------------------------------------------------------------------------


def test_install_on_non_spatial_layer_geom_changed_zero(tmp_path: Path) -> None:
    path = tmp_path / "lookup.gpkg"
    conn = sqlite3.connect(str(path), isolation_level=None)
    bootstrap_gpkg_project(conn)
    conn.execute('CREATE TABLE "lookup" (id INTEGER PRIMARY KEY, label TEXT)')
    install_change_tracking(conn, "lookup", pk_col="id")
    conn.execute("INSERT INTO lookup(id, label) VALUES (1, 'foo')")
    conn.commit()
    conn.close()

    rows = _changelog(path)
    assert rows[0]["geom_changed"] == 0
    assert json.loads(rows[0]["new_values"]) == {"label": "foo"}


# ---------------------------------------------------------------------------
# v1 → v2 migration
# ---------------------------------------------------------------------------


def test_migrate_v1_adds_geom_changed_column(tmp_path: Path) -> None:
    path = tmp_path / "v1.gpkg"
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA application_id = 1196444487")
    conn.execute(
        "CREATE TABLE _gispulse_change_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "table_name TEXT NOT NULL, operation TEXT NOT NULL, "
        "row_pk TEXT, old_values TEXT, new_values TEXT, "
        "changed_at TEXT DEFAULT (datetime('now')), processed INTEGER DEFAULT 0)"
    )
    cols_before = {
        r[1] for r in conn.execute("PRAGMA table_info(_gispulse_change_log)")
    }
    assert "geom_changed" not in cols_before

    changed = _migrate_v1_to_v2(conn)
    assert changed is True

    cols_after = {
        r[1] for r in conn.execute("PRAGMA table_info(_gispulse_change_log)")
    }
    assert "geom_changed" in cols_after

    # Idempotent on second call
    assert _migrate_v1_to_v2(conn) is False
    conn.close()


def test_migrate_no_table_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "fresh.gpkg"
    conn = sqlite3.connect(str(path))
    assert _migrate_v1_to_v2(conn) is False  # no _gispulse_change_log yet
    conn.close()


def test_bootstrap_upgrades_v1_gpkg_in_place(tmp_path: Path) -> None:
    path = tmp_path / "upgraded.gpkg"
    # Build v1 shape first
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA application_id = 1196444487")
    conn.execute(
        "CREATE TABLE _gispulse_change_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT, operation TEXT, "
        "row_pk TEXT, old_values TEXT, new_values TEXT, "
        "changed_at TEXT, processed INTEGER DEFAULT 0)"
    )
    conn.close()

    # Bootstrap should add the column and bump schema_version to 2.
    conn = sqlite3.connect(str(path), isolation_level=None)
    bootstrap_gpkg_project(conn)
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(_gispulse_change_log)")
    }
    assert "geom_changed" in cols
    sv = conn.execute(
        "SELECT value FROM _gispulse_kv WHERE key='schema_version'"
    ).fetchone()
    assert sv[0] == "2"
    conn.close()
