"""Tests for persistence.gpkg_schema — GPKG internal schema bootstrap + migration.

This module installs the _gispulse_* tables, registers them in
gpkg_extensions per OGC Annex F, creates the GPKG core tables, and
installs/uninstalls per-layer change-tracking triggers. Bugs here
corrupt the project file's OGC compliance so QGIS refuses to open it.
"""
from __future__ import annotations

import sqlite3

import pytest

from gispulse.persistence.gpkg_schema import (
    EXTENSION_DEFINITION,
    EXTENSION_NAME,
    EXTENSION_SCOPE,
    INTERNAL_TABLES,
    MODEL_TABLE_MAPPING,
    _build_change_triggers,
    _ensure_gpkg_core_tables,
    _ensure_gpkg_extensions_table,
    _ensure_origin_column,
    _list_gispulse_triggers,
    _migrate_v2_to_v3,
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
        # v2 (#7): identifiers are double-quoted (NEW."id" / OLD."id") so
        # reserved-keyword and special-char column names are safe.
        triggers = _build_change_triggers("mytable", pk_col="id")
        assert all('."id"' in t for t in triggers)

    def test_default_pk_col_is_fid(self):
        triggers = _build_change_triggers("x")
        assert all('."fid"' in t for t in triggers)


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
        from gispulse.persistence.schema import SCHEMA_VERSION

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
    """Beta P0-4c (SQLi guard) + B-05 v1.5.3 (QGIS-friendly relaxation).

    ``install_change_tracking`` interpolates ``layer_name`` into trigger
    DDL via f-strings (DDL cannot use bound parameters), so a name
    containing ``"``, ``'``, ``;`` or ``\\`` is a textbook SQLi vector
    and must always raise. **B-05** widened the validator to accept
    QGIS desktop layer names with spaces, dashes, accents, leading
    digits — anything safe inside a quoted identifier / literal — so
    French datasets like ``"Parcelles cadastrales 2024"`` no longer
    fail at install time.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "a';DROP TABLE x;--",      # closes single-quoted literal
            'a"; DROP TABLE x; --',    # closes double-quoted identifier
            "evil'); DROP TABLE _gispulse_change_log; --",
            ";",                        # bare statement terminator
            "tbl;DROP",                # statement terminator + injection
            "back\\slash",            # backslash escape
            "",                         # empty
            "table\nname",             # newline (control char)
            "table\rname",             # carriage return
            "tab\tname",                # tab
            "\x00null",                # NUL byte
        ],
    )
    def test_rejects_unsafe_identifiers(self, name):
        with pytest.raises(ValueError):
            _validate_identifier(name)

    @pytest.mark.parametrize(
        "name",
        [
            # Strict ASCII names already worked pre-B-05 (no regression):
            "parcels",
            "parcels_2024",
            "_internal_metric",
            "Parcelles",
            "parcelles_éàü",   # Unicode word chars
            "naïve_layer",
            # B-05 — QGIS desktop layer names previously rejected:
            "Parcelles cadastrales 2024",  # spaces
            "voies-rapides",                # dash
            "table.dot",                    # dot (no schema in GPKG)
            "1starts_with_digit",          # leading digit
            "--comment",                    # SQL-comment marker (safe inside "...")
            "café",                          # accented + non-ASCII
            "couche QGIS éàüç-2024",       # full mix
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

    def test_install_change_tracking_accepts_space(self, conn):
        """B-05: layer names with spaces install + fire end-to-end."""
        bootstrap_gpkg_project(conn)
        layer = "Parcelles cadastrales 2024"
        conn.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY, name TEXT)')
        conn.commit()
        install_change_tracking(conn, layer)
        conn.execute(f'INSERT INTO "{layer}"(name) VALUES (?)', ("alpha",))
        conn.commit()
        row = conn.execute(
            "SELECT table_name FROM _gispulse_change_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None and row["table_name"] == layer

    def test_install_change_tracking_accepts_dash(self, conn):
        """B-05: dashes are allowed (SQL identifier always quoted)."""
        bootstrap_gpkg_project(conn)
        layer = "voies-rapides"
        conn.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY)')
        conn.commit()
        install_change_tracking(conn, layer)
        # Round-trip: uninstall must drop the same triggers.
        uninstall_change_tracking(conn, layer)
        # No leftover trigger named after that layer.
        leftovers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE '_gispulse_trg_%'"
        ).fetchall()
        for r in leftovers:
            assert "voies" not in r["name"], f"leftover trigger: {r['name']!r}"

    def test_uninstall_change_tracking_rejects_unsafe(self, conn):
        bootstrap_gpkg_project(conn)
        with pytest.raises(ValueError):
            uninstall_change_tracking(conn, "a';--")

    def test_install_change_tracking_slug_stable(self, conn):
        """B-05: same Unicode layer name → same trigger names across calls."""
        from gispulse.core.sql_safety import slug_identifier

        bootstrap_gpkg_project(conn)
        layer = "Parcelles cadastrales 2024"
        conn.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY)')
        conn.commit()
        install_change_tracking(conn, layer)
        slug = slug_identifier(layer)
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        for op in ("insert", "update", "delete"):
            assert f"_gispulse_trg_{slug}_{op}" in names

    def test_install_change_tracking_legacy_ascii_unchanged(self, conn):
        """B-05: pre-B-05 GPKGs use ``_gispulse_trg_<layer>_<op>`` trigger
        names. The slug must keep returning the same identifier for
        ASCII-safe layer names so legacy projects round-trip cleanly.
        """
        from gispulse.core.sql_safety import slug_identifier

        assert slug_identifier("parcels") == "parcels"
        assert slug_identifier("my_table_2024") == "my_table_2024"
        assert slug_identifier("_internal") == "_internal"

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


# ---------------------------------------------------------------------------
# B-02 (v1.5.3, #103) — origin-tagging M1 (loop bypass + sentinel column)
# ---------------------------------------------------------------------------


class TestOriginTaggingM1:
    """B-02 — sentinel column + AFTER UPDATE WHEN clause prevent the
    SET_FIELD / RUN_SQL infinite loop where a trigger writes back to the
    same table.

    Bug reproducer: ``ON UPDATE buildings → SET_FIELD area =
    ST_Area(geom)`` re-fired on every ``area`` write, locking CPU at
    100% and ballooning the GPKG with ``_gispulse_change_log`` rows.
    """

    def _prep(self, conn):
        bootstrap_gpkg_project(conn)
        conn.execute(
            'CREATE TABLE "buildings" '
            "(fid INTEGER PRIMARY KEY, name TEXT, area REAL)"
        )
        conn.commit()
        install_change_tracking(conn, "buildings")

    def test_install_adds_sentinel_column(self, conn):
        """Installing change tracking grows the layer with the v3
        ``_gispulse_origin`` column. Idempotent on re-install."""
        self._prep(conn)
        cols = {
            r[1]
            for r in conn.execute('PRAGMA table_info("buildings")').fetchall()
        }
        assert "_gispulse_origin" in cols
        # Re-install does not duplicate or recreate the column.
        install_change_tracking(conn, "buildings")
        cols_again = {
            r[1]
            for r in conn.execute('PRAGMA table_info("buildings")').fetchall()
        }
        assert cols == cols_again

    def test_qgis_update_still_fires_trigger(self, conn):
        """Baseline: a regular QGIS UPDATE (no marker) still produces a
        change-log row. The WHEN clause must not over-suppress."""
        self._prep(conn)
        conn.execute('INSERT INTO "buildings"(fid, name) VALUES (1, "A")')
        conn.commit()
        # Truncate to isolate the UPDATE we care about.
        conn.execute("DELETE FROM _gispulse_change_log")
        conn.execute('UPDATE "buildings" SET name="B" WHERE fid=1')
        conn.commit()
        rows = conn.execute(
            "SELECT operation FROM _gispulse_change_log"
        ).fetchall()
        assert [r["operation"] for r in rows] == ["UPDATE"]

    def test_trigger_marked_update_is_suppressed(self, conn):
        """Action-dispatcher write-back: an UPDATE that sets the marker
        to ``trigger:<id>`` MUST NOT produce a change-log row — that's
        the loop bypass."""
        self._prep(conn)
        conn.execute('INSERT INTO "buildings"(fid, name) VALUES (1, "A")')
        conn.commit()
        conn.execute("DELETE FROM _gispulse_change_log")
        conn.execute(
            'UPDATE "buildings" SET name="B", "_gispulse_origin" = ? '
            "WHERE fid=1",
            ("trigger:abc-123",),
        )
        conn.commit()
        rows = conn.execute("SELECT * FROM _gispulse_change_log").fetchall()
        assert rows == [], (
            "row tagged as trigger:<id> must not re-fire the trigger — "
            "that's the loop"
        )

    def test_clear_sentinel_update_is_suppressed(self, conn):
        """Action-dispatcher clear pass: ``UPDATE ... _gispulse_origin =
        NULL WHERE id = ?`` after a trigger marker MUST also be
        suppressed, otherwise the clear loops back to the trigger."""
        self._prep(conn)
        conn.execute(
            'INSERT INTO "buildings"(fid, name, "_gispulse_origin") '
            'VALUES (1, "A", ?)',
            ("trigger:abc-123",),
        )
        conn.commit()
        conn.execute("DELETE FROM _gispulse_change_log")
        conn.execute(
            'UPDATE "buildings" SET "_gispulse_origin" = NULL WHERE fid=1'
        )
        conn.commit()
        rows = conn.execute("SELECT * FROM _gispulse_change_log").fetchall()
        assert rows == [], (
            "the action_dispatcher's sentinel-clear UPDATE must be "
            "suppressed (NEW=NULL while OLD LIKE 'trigger:%')"
        )

    def test_qgis_edit_after_trigger_cycle_fires(self, conn):
        """End-to-end: trigger marker → clear → QGIS edit. The QGIS
        edit MUST fire the trigger (not be silently swallowed because a
        previous trigger ran on this row)."""
        self._prep(conn)
        conn.execute('INSERT INTO "buildings"(fid, name) VALUES (1, "A")')
        conn.commit()
        # Simulate the action_dispatcher pair: marker write + clear.
        conn.execute(
            'UPDATE "buildings" SET name="B", "_gispulse_origin" = ? '
            "WHERE fid=1",
            ("trigger:abc-123",),
        )
        conn.execute(
            'UPDATE "buildings" SET "_gispulse_origin" = NULL WHERE fid=1'
        )
        conn.commit()
        # Now a real QGIS edit.
        conn.execute("DELETE FROM _gispulse_change_log")
        conn.execute('UPDATE "buildings" SET name="C" WHERE fid=1')
        conn.commit()
        rows = conn.execute(
            "SELECT operation FROM _gispulse_change_log"
        ).fetchall()
        assert [r["operation"] for r in rows] == ["UPDATE"], (
            "after the action's marker+clear pair, a subsequent QGIS "
            "UPDATE must still produce a change-log row"
        )


class TestSchemaMigrationV2ToV3:
    """B-02 — :func:`_migrate_v2_to_v3` rebuilds tracked-layer triggers
    on the new v3 contract (sentinel column + WHEN clause)."""

    def _make_v2_gpkg(self, conn):
        """Bootstrap a GPKG, install change tracking, then forcibly
        downgrade to a v2-shaped trigger (no WHEN clause) so the
        migration has work to do."""
        bootstrap_gpkg_project(conn)
        conn.execute(
            'CREATE TABLE "parcels" (fid INTEGER PRIMARY KEY, name TEXT)'
        )
        conn.commit()
        install_change_tracking(conn, "parcels")
        for trg in _list_gispulse_triggers(conn, "parcels"):
            conn.execute(f'DROP TRIGGER "{trg}"')
        conn.execute(
            'CREATE TRIGGER "_gispulse_trg_parcels_update" '
            'AFTER UPDATE ON "parcels" BEGIN '
            "  INSERT INTO _gispulse_change_log "
            "(table_name, operation, row_pk, new_values, old_values, geom_changed) "
            "  VALUES ('parcels', 'UPDATE', NEW.\"fid\", "
            "          json_object('name', NEW.\"name\"), "
            "          json_object('name', OLD.\"name\"), 0); "
            "END"
        )
        conn.commit()

    def test_no_op_when_no_tracked_layer(self, conn):
        """A bootstrap-only GPKG (no tracked layer) is a no-op."""
        bootstrap_gpkg_project(conn)
        assert _migrate_v2_to_v3(conn) == 0

    def test_rebuilds_triggers_with_when_clause(self, conn):
        """After migration, the AFTER UPDATE trigger sql contains the
        WHEN clause."""
        self._make_v2_gpkg(conn)
        v2_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='_gispulse_trg_parcels_update'"
        ).fetchone()
        assert v2_sql is not None
        assert "WHEN" not in v2_sql["sql"].upper()

        rebuilt = _migrate_v2_to_v3(conn)
        assert rebuilt == 1

        v3_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE '_gispulse_trg_parcels_update%'"
        ).fetchone()
        assert v3_sql is not None
        assert "WHEN" in v3_sql["sql"].upper()
        assert "_gispulse_origin" in v3_sql["sql"]

    def test_idempotent(self, conn):
        """Running the migration twice on the same project is safe."""
        self._make_v2_gpkg(conn)
        _migrate_v2_to_v3(conn)
        rebuilt = _migrate_v2_to_v3(conn)
        assert rebuilt >= 1

    def test_bootstrap_runs_migration(self, conn):
        """Re-bootstrapping a v2 GPKG applies the v2→v3 migration so
        existing projects upgrade in-place."""
        self._make_v2_gpkg(conn)
        bootstrap_gpkg_project(conn)
        v3_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE '_gispulse_trg_parcels_update%'"
        ).fetchone()
        assert v3_sql is not None
        assert "WHEN" in v3_sql["sql"].upper()


class TestEnsureOriginColumn:
    """``_ensure_origin_column`` is a low-level helper that adds the
    sentinel column idempotently."""

    def test_adds_when_missing(self, conn):
        bootstrap_gpkg_project(conn)
        conn.execute('CREATE TABLE "x" (fid INTEGER PRIMARY KEY)')
        conn.commit()
        added = _ensure_origin_column(conn, "x")
        assert added is True
        cols = {r[1] for r in conn.execute('PRAGMA table_info("x")').fetchall()}
        assert "_gispulse_origin" in cols

    def test_noop_when_present(self, conn):
        bootstrap_gpkg_project(conn)
        conn.execute(
            'CREATE TABLE "x" '
            '(fid INTEGER PRIMARY KEY, "_gispulse_origin" TEXT)'
        )
        conn.commit()
        added = _ensure_origin_column(conn, "x")
        assert added is False

