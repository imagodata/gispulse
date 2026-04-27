"""Guardrail tests for :meth:`GeoPackageEngine.execute`.

We test both the standalone :func:`persistence.sql_guardrails.enforce`
parser (cheap, fast unit tests) and the full ``execute()`` path
end-to-end (so we know the parser is actually invoked before any
statement reaches SQLite).

Threat model — what the YAML attacker MUST NOT achieve:

* Drop or alter user/internal tables.
* Tamper with GPKG metadata (``gpkg_*``) or the change-log.
* Open a sibling SQLite via ``ATTACH``.
* Flip ``PRAGMA writable_schema`` then bidouille ``sqlite_master``.
* Chain a second statement after a benign-looking INSERT.
* DoS the parser with deeply nested CTEs.

A passing run of this file is part of the contract that ``execute()``
is the **single sandbox** through which YAML actions touch the GPKG.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from persistence.gpkg_engine import GeoPackageEngine
from persistence.sql_guardrails import (
    MAX_PAREN_DEPTH,
    SecurityError,
    enforce,
    parse_statement,
)


# ---------------------------------------------------------------------------
# Engine fixture — minimal user table, change tracking off (we test the
# guardrail boundary, not the change-log).
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine(tmp_path: Path) -> GeoPackageEngine:
    gpkg = tmp_path / "guardrails.gpkg"
    eng = GeoPackageEngine(path=gpkg)
    eng.open()
    # User table created via the internal allow_ddl path — same gate
    # the engine itself uses for migrations. We then verify YAML-style
    # DDL is refused below.
    eng.execute(
        'CREATE TABLE "items" (id INTEGER PRIMARY KEY, label TEXT)',
        allow_ddl=True,
    )
    yield eng
    eng.close()


# ---------------------------------------------------------------------------
# Standalone parser tests
# ---------------------------------------------------------------------------


class TestParseStatement:
    def test_identifies_select(self) -> None:
        parsed = parse_statement("SELECT 1")
        assert parsed.statement_type == "SELECT"

    def test_identifies_insert_with_leading_comment(self) -> None:
        parsed = parse_statement("-- a comment\nINSERT INTO x(id) VALUES (1)")
        assert parsed.statement_type == "INSERT"

    def test_strips_block_comment(self) -> None:
        parsed = parse_statement("/* DROP TABLE x */ SELECT 1")
        assert parsed.statement_type == "SELECT"

    def test_empty_sql_raises(self) -> None:
        with pytest.raises(SecurityError):
            parse_statement("")
        with pytest.raises(SecurityError):
            parse_statement("   /* only */ -- comments\n")


class TestEnforceWhitelist:
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO items(id, label) VALUES (1, 'a')",
            "UPDATE items SET label = 'b' WHERE id = 1",
            "DELETE FROM items WHERE id = 1",
            "SELECT * FROM items",
        ],
    )
    def test_dml_allowed(self, sql: str) -> None:
        parsed = enforce(sql)
        assert parsed.statement_type in {"INSERT", "UPDATE", "DELETE", "SELECT"}

    def test_drop_blocked_without_allow_ddl(self) -> None:
        with pytest.raises(SecurityError, match="DROP"):
            enforce("DROP TABLE items")

    def test_create_blocked_without_allow_ddl(self) -> None:
        with pytest.raises(SecurityError, match="CREATE"):
            enforce("CREATE TABLE foo (id INT)")

    def test_alter_blocked_without_allow_ddl(self) -> None:
        with pytest.raises(SecurityError, match="ALTER"):
            enforce("ALTER TABLE items ADD COLUMN extra TEXT")

    def test_create_allowed_with_allow_ddl(self) -> None:
        parsed = enforce("CREATE TABLE foo (id INT)", allow_ddl=True)
        assert parsed.statement_type == "CREATE"

    def test_drop_allowed_with_allow_ddl(self) -> None:
        parsed = enforce("DROP TABLE foo", allow_ddl=True)
        assert parsed.statement_type == "DROP"

    @pytest.mark.parametrize(
        "sql",
        [
            "PRAGMA writable_schema = 1",
            "PRAGMA journal_mode = OFF",
            "ATTACH DATABASE 'foo.db' AS bar",
            "DETACH DATABASE bar",
            "VACUUM",
            "REINDEX",
            "ANALYZE",
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
            "SAVEPOINT s1",
        ],
    )
    def test_hard_blocked_statements(self, sql: str) -> None:
        with pytest.raises(SecurityError):
            enforce(sql)


class TestEnforceProtectedTables:
    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM gpkg_contents",
            "DELETE FROM gpkg_geometry_columns WHERE table_name='x'",
            'INSERT INTO "gpkg_extensions" (extension_name) VALUES (1)',
            "UPDATE gpkg_metadata SET md_scope='dataset'",
            "DELETE FROM rtree_layer_geom",
        ],
    )
    def test_blocks_writes_to_gpkg_internals(self, sql: str) -> None:
        with pytest.raises(SecurityError, match="protected"):
            enforce(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            # ``sqlite_master`` is blocked even earlier by the danger
            # pattern check (defense in depth — both layers refuse it).
            "INSERT INTO sqlite_master(type) VALUES ('table')",
            "UPDATE sqlite_master SET sql='hacked'",
        ],
    )
    def test_blocks_writes_to_sqlite_master_via_danger_scan(
        self, sql: str
    ) -> None:
        with pytest.raises(SecurityError, match="sqlite_master"):
            enforce(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "UPDATE _gispulse_change_log SET processed = 1",
            "DELETE FROM _gispulse_change_log",
            'INSERT INTO "_gispulse_kv"(key, value) VALUES (1, 1)',
        ],
    )
    def test_blocks_writes_to_internal_audit_tables(self, sql: str) -> None:
        with pytest.raises(SecurityError, match="protected"):
            enforce(sql)

    def test_allows_writes_to_user_tables_with_similar_names(self) -> None:
        # ``my_gpkg_helper`` only matches the prefix when normalised, so
        # the protection is prefix-based at the START of the table name.
        # ``user_gpkg_helper`` (with prefix ``user_``) is fine.
        enforce("UPDATE user_gpkg_helper SET x = 1")
        enforce('INSERT INTO "users" VALUES (1)')


class TestEnforceDangerPatterns:
    def test_detects_writable_schema_in_update(self) -> None:
        # An attacker who tries to slip writable_schema into an UPDATE
        # (e.g. via a TEXT column name) gets refused even when the
        # statement-type check would pass.
        with pytest.raises(SecurityError, match="writable_schema"):
            enforce("UPDATE items SET label = writable_schema")

    def test_detects_sqlite_master_reference(self) -> None:
        with pytest.raises(SecurityError, match="sqlite_master"):
            enforce("SELECT * FROM sqlite_master")

    def test_detects_load_extension(self) -> None:
        with pytest.raises(SecurityError, match="load_extension"):
            enforce("SELECT load_extension('evil.so')")

    def test_string_literal_does_not_trigger(self) -> None:
        # A user value that happens to mention 'sqlite_master' is fine
        # because it sits inside a string literal — masking strips it
        # before the danger scan.
        enforce("UPDATE items SET label = 'reference: sqlite_master'")


class TestEnforceMultiStatement:
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO items(id) VALUES (1); DROP TABLE items",
            "UPDATE items SET label='a'; UPDATE items SET label='b'",
            "SELECT 1; SELECT 2",
            # Trailing comment cannot mask the second statement.
            "INSERT INTO items(id) VALUES (1); /* sneaky */ DROP TABLE items",
        ],
    )
    def test_blocks_chained_statements(self, sql: str) -> None:
        with pytest.raises(SecurityError, match="multiple SQL statements"):
            enforce(sql)

    def test_allows_single_trailing_semicolon(self) -> None:
        # A formatter-friendly trailing semicolon is fine.
        enforce("INSERT INTO items(id) VALUES (1);")
        enforce("SELECT 1   ;   ")

    def test_semicolon_inside_string_does_not_count(self) -> None:
        enforce("INSERT INTO items(label) VALUES ('a; b')")


class TestEnforceParenDepth:
    def test_allows_up_to_max(self) -> None:
        # Build a SELECT with exactly MAX_PAREN_DEPTH levels of nesting.
        depth = MAX_PAREN_DEPTH
        sql = "SELECT 1 FROM " + "(SELECT 1 " * depth + ")" * depth + " "
        # Note: the outer-most SELECT does not add a paren, so this hits
        # exactly ``depth`` levels.
        enforce(sql)

    def test_rejects_above_max(self) -> None:
        depth = MAX_PAREN_DEPTH + 1
        sql = "SELECT 1 FROM " + "(SELECT 1 " * depth + ")" * depth
        with pytest.raises(SecurityError, match="nesting depth"):
            enforce(sql)


# ---------------------------------------------------------------------------
# Engine end-to-end — the same scenarios applied through the real
# ``execute()`` so we know the guardrail is actually invoked, not just
# importable.
# ---------------------------------------------------------------------------


class TestExecuteEndToEnd:
    def test_insert_update_delete_user_table(
        self, engine: GeoPackageEngine
    ) -> None:
        # INSERT
        rc = engine.execute(
            "INSERT INTO items(id, label) VALUES (%s, %s)", [1, "alpha"]
        )
        assert rc == 1
        # UPDATE
        rc = engine.execute(
            "UPDATE items SET label = %s WHERE id = %s", ["beta", 1]
        )
        assert rc == 1
        # DELETE
        rc = engine.execute("DELETE FROM items WHERE id = %s", [1])
        assert rc == 1

    def test_drop_user_table_blocked(self, engine: GeoPackageEngine) -> None:
        with pytest.raises(SecurityError):
            engine.execute("DROP TABLE items")
        # And the table is still there.
        engine.execute("INSERT INTO items(id) VALUES (1)")  # would fail if dropped

    def test_drop_allowed_with_internal_flag(
        self, engine: GeoPackageEngine
    ) -> None:
        engine.execute(
            "CREATE TABLE temp_migration (x INT)", allow_ddl=True
        )
        engine.execute("DROP TABLE temp_migration", allow_ddl=True)

    def test_writes_to_gpkg_contents_blocked(
        self, engine: GeoPackageEngine
    ) -> None:
        with pytest.raises(SecurityError, match="protected"):
            engine.execute("DELETE FROM gpkg_contents")

    def test_writes_to_change_log_blocked(
        self, engine: GeoPackageEngine
    ) -> None:
        with pytest.raises(SecurityError, match="protected"):
            engine.execute("UPDATE _gispulse_change_log SET processed = 1")

    def test_multi_statement_blocked(self, engine: GeoPackageEngine) -> None:
        with pytest.raises(SecurityError, match="multiple"):
            engine.execute(
                "INSERT INTO items(id) VALUES (1); DROP TABLE items"
            )

    def test_pragma_writable_schema_blocked(
        self, engine: GeoPackageEngine
    ) -> None:
        with pytest.raises(SecurityError):
            engine.execute("PRAGMA writable_schema = 1")

    def test_attach_database_blocked(self, engine: GeoPackageEngine) -> None:
        with pytest.raises(SecurityError):
            engine.execute("ATTACH DATABASE 'evil.db' AS evil")

    def test_deeply_nested_subquery_blocked(
        self, engine: GeoPackageEngine
    ) -> None:
        depth = MAX_PAREN_DEPTH + 1
        sql = "SELECT 1 FROM " + "(SELECT 1 " * depth + ")" * depth
        with pytest.raises(SecurityError, match="nesting depth"):
            engine.execute(sql)

    def test_params_are_bound_not_interpolated(
        self, engine: GeoPackageEngine
    ) -> None:
        """Classic SQLi probe: the param value is treated as a value,
        not as SQL. If it were interpolated, the table would be empty."""
        engine.execute(
            "INSERT INTO items(id, label) VALUES (%s, %s)",
            [1, "'); DROP TABLE items; --"],
        )
        # Read it back — the literal string survived untouched.
        rows = engine.execute_sql("SELECT label FROM items WHERE id = 1")
        assert rows[0]["label"] == "'); DROP TABLE items; --"

    def test_security_error_does_not_leak_params_in_log(
        self, engine: GeoPackageEngine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Guardrail logs MUST NOT contain bound parameter values
        (they may carry PII)."""
        secret = "PII_SECRET_VALUE_DO_NOT_LOG"
        with caplog.at_level("WARNING", logger="gispulse.engine.exec"):
            with pytest.raises(SecurityError):
                engine.execute(
                    "DELETE FROM gpkg_contents WHERE x = %s", [secret]
                )
        # The blocked-log line is emitted, but never echoes the param
        # array.
        for record in caplog.records:
            assert secret not in record.getMessage(), (
                f"PII leaked into guardrail log: {record.getMessage()!r}"
            )

    def test_psycopg_placeholder_is_translated(
        self, engine: GeoPackageEngine
    ) -> None:
        # %s -> ? translation: the engine must accept psycopg-style
        # placeholders (the dispatcher uses them so PostGIS and GPKG
        # can share one code path).
        engine.execute(
            "INSERT INTO items(id, label) VALUES (%s, %s)", [42, "translated"]
        )
        rows = engine.execute_sql("SELECT label FROM items WHERE id = 42")
        assert rows[0]["label"] == "translated"

    def test_percent_inside_string_literal_is_preserved(
        self, engine: GeoPackageEngine
    ) -> None:
        # A literal '50%' must NOT be rewritten to '50?'.
        engine.execute(
            "INSERT INTO items(id, label) VALUES (%s, %s)", [99, "50%s"]
        )
        rows = engine.execute_sql("SELECT label FROM items WHERE id = 99")
        assert rows[0]["label"] == "50%s"

    def test_translate_handles_escaped_single_quote(self) -> None:
        """SQL '' escape inside a single-quoted literal must survive
        the placeholder translation."""
        translated = GeoPackageEngine._translate_placeholders(
            "INSERT INTO x(label) VALUES ('it''s %s', %s)"
        )
        # %s INSIDE the literal stays untouched; the trailing %s -> ?
        assert translated == "INSERT INTO x(label) VALUES ('it''s %s', ?)"

    def test_translate_handles_escaped_double_quote(self) -> None:
        """SQLite supports "" as the escape inside a double-quoted
        identifier."""
        translated = GeoPackageEngine._translate_placeholders(
            'UPDATE "weird""col" SET v = %s'
        )
        assert translated == 'UPDATE "weird""col" SET v = ?'

    def test_translate_no_percent_short_circuits(self) -> None:
        """Pure ?-placeholder SQL passes through without rewriting."""
        sql = "SELECT * FROM items WHERE id = ?"
        assert GeoPackageEngine._translate_placeholders(sql) is sql

    def test_translate_percent_inside_double_quoted_identifier(self) -> None:
        """``%s`` appearing inside a double-quoted identifier (legal in
        SQLite for weird column names) must NOT be rewritten."""
        translated = GeoPackageEngine._translate_placeholders(
            'SELECT "weird%scol" FROM x WHERE id = %s'
        )
        assert translated == 'SELECT "weird%scol" FROM x WHERE id = ?'

    def test_execute_with_mapping_params(
        self, engine: GeoPackageEngine
    ) -> None:
        """``:name``-style binding with a dict — SQLite accepts it
        natively, our normalisation goes through ``dict(params)``."""
        engine.execute(
            "INSERT INTO items(id, label) VALUES (:id, :label)",
            {"id": 7, "label": "mapped"},
        )
        rows = engine.execute_sql("SELECT label FROM items WHERE id = 7")
        assert rows[0]["label"] == "mapped"

    def test_execute_raises_when_engine_not_open(
        self, tmp_path: Path
    ) -> None:
        """``execute()`` must refuse to run on a closed engine — a
        ``RuntimeError`` is the documented contract."""
        eng = GeoPackageEngine(path=tmp_path / "closed.gpkg")
        # Never opened.
        with pytest.raises(RuntimeError, match="not open"):
            eng.execute("SELECT 1")

    def test_rollback_on_internal_error(
        self, engine: GeoPackageEngine
    ) -> None:
        """A real SQLite OperationalError mid-statement must roll back —
        partial writes never persist, even if BEGIN IMMEDIATE was issued.
        """
        # First populate
        engine.execute("INSERT INTO items(id) VALUES (1)")
        # Now try to violate the PK — SQLite raises IntegrityError, which
        # is NOT a SecurityError but is also not caught by execute().
        # It propagates and the implicit transaction rolls back.
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            engine.execute("INSERT INTO items(id) VALUES (1)")
        # The original row is still there, no zombie second copy.
        rows = engine.execute_sql("SELECT COUNT(*) AS c FROM items")
        assert rows[0]["c"] == 1
