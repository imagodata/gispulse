"""Tests for persistence.gpkg_schema — GPKG internal schema bootstrap + migration.

This module installs the _gispulse_* tables, registers them in
gpkg_extensions per OGC Annex F, creates the GPKG core tables, and
installs/uninstalls per-layer change-tracking triggers. Bugs here
corrupt the project file's OGC compliance so QGIS refuses to open it.
"""
from __future__ import annotations

import sqlite3

import pytest

from persistence.gpkg_schema import (
    EXTENSION_DEFINITION,
    EXTENSION_NAME,
    EXTENSION_SCOPE,
    INTERNAL_TABLES,
    MODEL_TABLE_MAPPING,
    _build_change_triggers,
    _ensure_gpkg_core_tables,
    _ensure_gpkg_extensions_table,
    _register_extension,
    _validate_identifier,
    bootstrap_gpkg_project,
    install_change_tracking,
    migrate_sqlite_to_gpkg,
    uninstall_change_tracking,
)


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "test.gpkg")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Module-level metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_extension_constants(self):
        assert EXTENSION_NAME == "gispulse"
        assert EXTENSION_DEFINITION.startswith("https://")
        assert EXTENSION_SCOPE == "read-write"

    def test_internal_tables_populated(self):
        assert isinstance(INTERNAL_TABLES, list)
        assert len(INTERNAL_TABLES) > 0
        # All internal tables must have the _gispulse_ prefix
        assert all(t.startswith("_gispulse_") for t in INTERNAL_TABLES)

    def test_model_table_mapping_populated(self):
        assert isinstance(MODEL_TABLE_MAPPING, dict)
        assert len(MODEL_TABLE_MAPPING) > 0


# ---------------------------------------------------------------------------
# Change trigger generation
# ---------------------------------------------------------------------------


class TestBuildChangeTriggers:
    def test_generates_three_triggers(self):
        triggers = _build_change_triggers("parcels")
        assert len(triggers) == 3

    def test_triggers_have_insert_update_delete(self):
        triggers = _build_change_triggers("parcels")
        ops = ["INSERT", "UPDATE", "DELETE"]
        for op in ops:
            assert any(f"AFTER {op}" in t for t in triggers)

    def test_triggers_reference_correct_pk_col(self):
        triggers = _build_change_triggers("mytable", pk_col="id")
        assert all(".id" in t for t in triggers)

    def test_default_pk_col_is_fid(self):
        triggers = _build_change_triggers("x")
        assert all(".fid" in t for t in triggers)


# ---------------------------------------------------------------------------
# _ensure_gpkg_extensions_table
# ---------------------------------------------------------------------------


class TestEnsureExtensionsTable:
    def test_creates_table(self, conn):
        _ensure_gpkg_extensions_table(conn)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gpkg_extensions'"
        )
        assert cur.fetchone() is not None

    def test_is_idempotent(self, conn):
        _ensure_gpkg_extensions_table(conn)
        _ensure_gpkg_extensions_table(conn)
        _ensure_gpkg_extensions_table(conn)


class TestRegisterExtension:
    def test_adds_row_to_extensions(self, conn):
        _ensure_gpkg_extensions_table(conn)
        _register_extension(conn, "_gispulse_rules")
        rows = conn.execute(
            "SELECT * FROM gpkg_extensions WHERE table_name='_gispulse_rules'"
        ).fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["extension_name"] == EXTENSION_NAME
        assert row["scope"] == EXTENSION_SCOPE
        assert row["definition"] == EXTENSION_DEFINITION

    def test_duplicate_registration_is_not_deduplicated_due_to_null_column(self, conn):
        """Known-quirk: _register_extension uses column_name=NULL. SQLite's
        UNIQUE constraint treats NULL != NULL, so INSERT OR IGNORE does NOT
        prevent duplicate rows for the same (table_name, extension_name)
        pair when column_name is NULL. Pin this behaviour so a future
        refactor that adds a deduplication path is a deliberate change."""
        _ensure_gpkg_extensions_table(conn)
        _register_extension(conn, "_gispulse_x")
        _register_extension(conn, "_gispulse_x")
        rows = conn.execute(
            "SELECT COUNT(*) as c FROM gpkg_extensions WHERE table_name='_gispulse_x'"
        ).fetchone()
        # bootstrap_gpkg_project happens to avoid this because it's only
        # called once at open() — but the primitive itself does duplicate
        assert rows["c"] == 2


# ---------------------------------------------------------------------------
# _ensure_gpkg_core_tables
# ---------------------------------------------------------------------------


class TestEnsureCoreTables:
    def test_sets_application_id(self, conn):
        _ensure_gpkg_core_tables(conn)
        # 1196444487 = 0x47504B47 = 'GPKG' magic bytes
        row = conn.execute("PRAGMA application_id").fetchone()
        assert row[0] == 1196444487

    def test_creates_gpkg_spatial_ref_sys(self, conn):
        _ensure_gpkg_core_tables(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gpkg_spatial_ref_sys'"
        ).fetchone()
        assert row is not None

    def test_seeds_required_crs_entries(self, conn):
        """OGC GPKG spec mandates -1 (undefined cartesian), 0 (undefined
        geographic), and 4326 (WGS 84) entries in gpkg_spatial_ref_sys."""
        _ensure_gpkg_core_tables(conn)
        row = conn.execute(
            "SELECT srs_id FROM gpkg_spatial_ref_sys ORDER BY srs_id"
        ).fetchall()
        ids = {r["srs_id"] for r in row}
        assert -1 in ids
        assert 0 in ids
        assert 4326 in ids

    def test_creates_gpkg_contents(self, conn):
        _ensure_gpkg_core_tables(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gpkg_contents'"
        ).fetchone()
        assert row is not None

    def test_creates_gpkg_geometry_columns(self, conn):
        _ensure_gpkg_core_tables(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gpkg_geometry_columns'"
        ).fetchone()
        assert row is not None

    def test_is_idempotent(self, conn):
        _ensure_gpkg_core_tables(conn)
        _ensure_gpkg_core_tables(conn)
        _ensure_gpkg_core_tables(conn)


# ---------------------------------------------------------------------------
# bootstrap_gpkg_project
# ---------------------------------------------------------------------------


class TestBootstrapGpkgProject:
    def test_creates_all_internal_tables(self, conn):
        bootstrap_gpkg_project(conn)
        for table in INTERNAL_TABLES:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            assert row is not None, f"Missing table {table}"

    def test_registers_every_internal_table_in_extensions(self, conn):
        bootstrap_gpkg_project(conn)
        registered = {
            row["table_name"]
            for row in conn.execute(
                "SELECT table_name FROM gpkg_extensions WHERE extension_name=?",
                (EXTENSION_NAME,),
            ).fetchall()
        }
        assert set(INTERNAL_TABLES).issubset(registered)

    def test_sets_schema_version_in_kv_store(self, conn):
        from persistence.schema import SCHEMA_VERSION

        bootstrap_gpkg_project(conn)
        row = conn.execute(
            "SELECT value FROM _gispulse_kv WHERE key='schema_version'"
        ).fetchone()
        assert row is not None
        assert row["value"] == str(SCHEMA_VERSION)

    def test_is_idempotent(self, conn):
        bootstrap_gpkg_project(conn)
        bootstrap_gpkg_project(conn)  # must not re-insert duplicates
        # schema_version should still be a single row
        rows = conn.execute(
            "SELECT COUNT(*) as c FROM _gispulse_kv WHERE key='schema_version'"
        ).fetchone()
        assert rows["c"] == 1


# ---------------------------------------------------------------------------
# install / uninstall change tracking
# ---------------------------------------------------------------------------


class TestInstallChangeTracking:
    def _prep_tracked_table(self, conn):
        bootstrap_gpkg_project(conn)
        conn.execute(
            'CREATE TABLE "parcels" (fid INTEGER PRIMARY KEY, name TEXT)'
        )
        conn.commit()

    def test_installs_three_triggers(self, conn):
        self._prep_tracked_table(conn)
        install_change_tracking(conn, "parcels")
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE '_gispulse_trg_parcels_%'"
        ).fetchall()
        assert len(triggers) == 3
        names = {t["name"] for t in triggers}
        assert "_gispulse_trg_parcels_insert" in names
        assert "_gispulse_trg_parcels_update" in names
        assert "_gispulse_trg_parcels_delete" in names

    def test_inserts_log_entry_on_row_insert(self, conn):
        self._prep_tracked_table(conn)
        install_change_tracking(conn, "parcels")
        conn.execute('INSERT INTO "parcels" (fid, name) VALUES (1, "A")')
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM _gispulse_change_log WHERE operation='INSERT'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["table_name"] == "parcels"

    def test_logs_update_and_delete(self, conn):
        self._prep_tracked_table(conn)
        install_change_tracking(conn, "parcels")
        conn.execute('INSERT INTO "parcels" (fid, name) VALUES (1, "A")')
        conn.execute('UPDATE "parcels" SET name="B" WHERE fid=1')
        conn.execute('DELETE FROM "parcels" WHERE fid=1')
        conn.commit()
        ops = {
            r["operation"]
            for r in conn.execute(
                "SELECT operation FROM _gispulse_change_log"
            ).fetchall()
        }
        assert ops == {"INSERT", "UPDATE", "DELETE"}

    def test_uninstall_removes_triggers(self, conn):
        self._prep_tracked_table(conn)
        install_change_tracking(conn, "parcels")
        uninstall_change_tracking(conn, "parcels")
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE '_gispulse_trg_parcels_%'"
        ).fetchall()
        assert len(triggers) == 0

    def test_uninstall_without_install_is_noop(self, conn):
        self._prep_tracked_table(conn)
        # no install first — must not raise
        uninstall_change_tracking(conn, "parcels")

    def test_install_respects_custom_pk(self, conn):
        bootstrap_gpkg_project(conn)
        conn.execute('CREATE TABLE "t" (id INTEGER PRIMARY KEY, x INTEGER)')
        conn.commit()
        install_change_tracking(conn, "t", pk_col="id")
        conn.execute('INSERT INTO "t" (id, x) VALUES (42, 0)')
        conn.commit()
        row = conn.execute(
            "SELECT row_pk FROM _gispulse_change_log WHERE table_name='t'"
        ).fetchone()
        assert row["row_pk"] == "42"


# ---------------------------------------------------------------------------
# migrate_sqlite_to_gpkg
# ---------------------------------------------------------------------------


class TestMigration:
    def test_missing_old_db_returns_empty(self, conn, tmp_path):
        result = migrate_sqlite_to_gpkg(
            tmp_path / "does_not_exist.db", conn
        )
        assert result == {}

    def test_copies_rows_from_old_tables(self, conn, tmp_path):
        # Build a minimal old-style DB with one of the mapped tables
        old_db = tmp_path / "old.db"
        old_conn = sqlite3.connect(old_db)
        try:
            old_table = next(iter(MODEL_TABLE_MAPPING.keys()))
            # Create the old table with the same schema as the new one
            new_table = MODEL_TABLE_MAPPING[old_table]
            # Bootstrap the new schema to know the column list
            bootstrap_gpkg_project(conn)
            cols_info = conn.execute(
                f"PRAGMA table_info({new_table})"
            ).fetchall()
            col_defs = ", ".join(f"{c['name']} TEXT" for c in cols_info)
            old_conn.execute(f"CREATE TABLE {old_table} ({col_defs})")

            # Insert one row with reasonable default values
            names = [c["name"] for c in cols_info]
            placeholders = ", ".join("?" for _ in names)
            old_conn.execute(
                f"INSERT INTO {old_table} ({', '.join(names)}) "
                f"VALUES ({placeholders})",
                tuple("x" for _ in names),
            )
            old_conn.commit()
        finally:
            old_conn.close()

        stats = migrate_sqlite_to_gpkg(old_db, conn)
        assert stats.get(old_table, 0) >= 1


# ---------------------------------------------------------------------------
# SQLi guard on layer/identifier names (P0-4c)
# ---------------------------------------------------------------------------


class TestIdentifierValidation:
    """Beta P0-4c: identifier guard. ``install_change_tracking`` interpolates
    layer_name into trigger DDL via f-strings (DDL cannot use bound params),
    so an unsafe name is a textbook SQLi vector. ``_validate_identifier``
    rejects anything that isn't ``[A-Za-z_]\\w*`` (Unicode-aware)."""

    @pytest.mark.parametrize(
        "name",
        [
            "a';DROP TABLE x;--",
            'a"; DROP TABLE x; --',
            "evil'); DROP TABLE _gispulse_change_log; --",
            "with space",
            "with-dash",
            "table.dot",
            "1starts_with_digit",
            "",
            ";",
            "--comment",
            "table\nname",
        ],
    )
    def test_rejects_unsafe_identifiers(self, name):
        with pytest.raises(ValueError):
            _validate_identifier(name)

    @pytest.mark.parametrize(
        "name",
        [
            "parcels",
            "parcels_2024",
            "_internal_metric",
            "Parcelles",
            "parcelles_éàü",  # Unicode word chars allowed
            "naïve_layer",
        ],
    )
    def test_accepts_safe_identifiers(self, name):
        assert _validate_identifier(name) == name

    def test_install_change_tracking_rejects_quote_in_layer_name(self, conn):
        bootstrap_gpkg_project(conn)
        with pytest.raises(ValueError):
            install_change_tracking(conn, "a'); DROP TABLE x; --")

    def test_install_change_tracking_rejects_semicolon(self, conn):
        bootstrap_gpkg_project(conn)
        with pytest.raises(ValueError):
            install_change_tracking(conn, "tbl;DROP")

    def test_install_change_tracking_rejects_space(self, conn):
        bootstrap_gpkg_project(conn)
        with pytest.raises(ValueError):
            install_change_tracking(conn, "with space")

    def test_uninstall_change_tracking_rejects_unsafe(self, conn):
        bootstrap_gpkg_project(conn)
        with pytest.raises(ValueError):
            uninstall_change_tracking(conn, "a';--")

    def test_install_change_tracking_unicode_layer_still_works(self, conn):
        """Unicode word characters (accents) must remain valid identifiers
        — Beta non-regression on test_install_change_tracking_with_unicode_layer_name."""
        bootstrap_gpkg_project(conn)
        layer = "parcelles_éàü"
        conn.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY, name TEXT)')
        conn.commit()
        install_change_tracking(conn, layer)
        # Trigger fires correctly.
        conn.execute(f'INSERT INTO "{layer}"(name) VALUES (?)', ("alpha",))
        conn.commit()
        row = conn.execute(
            "SELECT table_name FROM _gispulse_change_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None and row["table_name"] == layer
