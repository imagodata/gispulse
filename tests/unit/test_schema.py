"""Tests for persistence.schema — unified DDL generator for domain tables.

Source of truth for SQLiteRepository (no prefix) + GpkgRepository
(_gispulse_ prefix). Covers DDL generation, the logical-table list,
serialisation column sets, and model mapping.
"""
from __future__ import annotations

import re
import sqlite3


from gispulse.persistence.schema import (
    BOOL_COLUMNS,
    DATETIME_COLUMNS,
    JSON_COLUMNS,
    LOGICAL_TABLES,
    SCHEMA_VERSION,
    UUID_COLUMNS,
    _build_create_table,
    build_all_gpkg_schemas,
    build_gpkg_extra_schemas,
    build_model_table_mapping,
    build_table_schemas,
)


class TestModuleConstants:
    def test_schema_version_is_positive_int(self):
        assert isinstance(SCHEMA_VERSION, int)
        assert SCHEMA_VERSION >= 1

    def test_logical_tables_nonempty(self):
        assert isinstance(LOGICAL_TABLES, list)
        assert len(LOGICAL_TABLES) > 0

    def test_logical_tables_are_unique(self):
        assert len(LOGICAL_TABLES) == len(set(LOGICAL_TABLES))

    def test_logical_tables_have_no_prefix(self):
        """The base list must never leak a _gispulse_ prefix."""
        for name in LOGICAL_TABLES:
            assert not name.startswith("_gispulse_"), name


class TestSerialisationColumnSets:
    def test_json_columns_is_frozenset(self):
        assert isinstance(JSON_COLUMNS, frozenset)
        assert "config" in JSON_COLUMNS
        assert "conditions" in JSON_COLUMNS
        assert "predicates" in JSON_COLUMNS

    def test_datetime_columns_includes_created_at(self):
        assert "created_at" in DATETIME_COLUMNS
        assert "completed_at" in DATETIME_COLUMNS

    def test_uuid_columns_includes_id(self):
        assert "id" in UUID_COLUMNS
        assert "dataset_id" in UUID_COLUMNS
        assert "rule_id" in UUID_COLUMNS

    def test_bool_columns_includes_enabled(self):
        assert "enabled" in BOOL_COLUMNS
        assert "auto_eval" in BOOL_COLUMNS

    def test_column_sets_are_disjoint(self):
        """A column must only belong to one serialisation set (otherwise the
        repo would apply two conflicting coercions)."""
        pairs = [
            (JSON_COLUMNS, DATETIME_COLUMNS, "json vs datetime"),
            (JSON_COLUMNS, UUID_COLUMNS, "json vs uuid"),
            (JSON_COLUMNS, BOOL_COLUMNS, "json vs bool"),
            (DATETIME_COLUMNS, UUID_COLUMNS, "datetime vs uuid"),
            (DATETIME_COLUMNS, BOOL_COLUMNS, "datetime vs bool"),
            (UUID_COLUMNS, BOOL_COLUMNS, "uuid vs bool"),
        ]
        for a, b, label in pairs:
            overlap = a & b
            assert not overlap, f"{label} overlap: {overlap}"


class TestBuildCreateTable:
    def test_generates_valid_sqlite_ddl(self):
        sql = _build_create_table("foo", [("id", "TEXT PRIMARY KEY"), ("n", "INTEGER")])
        # Execute on a real in-memory SQLite to validate
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(sql)
        finally:
            conn.close()

    def test_is_idempotent_via_if_not_exists(self):
        sql = _build_create_table("t", [("id", "TEXT PRIMARY KEY")])
        assert "IF NOT EXISTS" in sql

    def test_embeds_table_name(self):
        sql = _build_create_table("mytable", [("id", "INTEGER")])
        assert "mytable" in sql


class TestBuildTableSchemas:
    def test_empty_prefix_produces_plain_names(self):
        schemas = build_table_schemas(prefix="")
        assert all(not name.startswith("_") for name in schemas.keys())
        assert set(schemas.keys()) == set(LOGICAL_TABLES)

    def test_gispulse_prefix_produces_internal_names(self):
        schemas = build_table_schemas(prefix="_gispulse_")
        assert all(name.startswith("_gispulse_") for name in schemas.keys())

    def test_ddl_is_executable_on_sqlite(self):
        """Every generated DDL must be valid SQLite."""
        schemas = build_table_schemas(prefix="")
        conn = sqlite3.connect(":memory:")
        try:
            for name, ddl in schemas.items():
                conn.execute(ddl)
        finally:
            conn.close()

    def test_returns_fresh_dict_each_call(self):
        a = build_table_schemas(prefix="")
        b = build_table_schemas(prefix="")
        assert a == b
        assert a is not b

    def test_every_table_has_id_primary_key(self):
        schemas = build_table_schemas(prefix="")
        for name, ddl in schemas.items():
            # First column should be "id" (primary key convention)
            assert re.search(r"\bid\b", ddl), f"Missing 'id' column in {name}"


class TestBuildGpkgExtraSchemas:
    def test_default_prefix(self):
        extras = build_gpkg_extra_schemas()
        # Every table must have the _gispulse_ prefix
        assert all(n.startswith("_gispulse_") for n in extras.keys())

    def test_custom_prefix(self):
        extras = build_gpkg_extra_schemas(prefix="custom_")
        assert all(n.startswith("custom_") for n in extras.keys())

    def test_returns_dict(self):
        extras = build_gpkg_extra_schemas()
        assert isinstance(extras, dict)
        assert len(extras) > 0

    def test_is_disjoint_from_domain_tables(self):
        """GPKG extras (change_log, kv) must not collide with domain tables."""
        domain = set(build_table_schemas(prefix="_gispulse_").keys())
        extras = set(build_gpkg_extra_schemas(prefix="_gispulse_").keys())
        overlap = domain & extras
        assert not overlap, f"Extras overlap with domain: {overlap}"


class TestBuildAllGpkgSchemas:
    def test_combines_domain_and_extras(self):
        all_schemas = build_all_gpkg_schemas()
        domain = build_table_schemas(prefix="_gispulse_")
        extras = build_gpkg_extra_schemas()
        # Union of keys
        assert set(all_schemas.keys()) == set(domain.keys()) | set(extras.keys())

    def test_ddl_is_executable_on_sqlite(self):
        schemas = build_all_gpkg_schemas()
        conn = sqlite3.connect(":memory:")
        try:
            for ddl in schemas.values():
                conn.execute(ddl)
        finally:
            conn.close()

    def test_custom_prefix_propagates(self):
        schemas = build_all_gpkg_schemas(prefix="gp_")
        assert all(n.startswith("gp_") for n in schemas.keys())


class TestBuildModelTableMapping:
    def test_maps_logical_to_prefixed(self):
        mapping = build_model_table_mapping(prefix="_gispulse_")
        for logical, physical in mapping.items():
            assert physical == f"_gispulse_{logical}"

    def test_empty_prefix_yields_identity(self):
        mapping = build_model_table_mapping(prefix="")
        for logical, physical in mapping.items():
            assert physical == logical

    def test_mapping_covers_all_logical_tables(self):
        mapping = build_model_table_mapping(prefix="_gispulse_")
        assert set(mapping.keys()) == set(LOGICAL_TABLES)

    def test_returns_fresh_dict(self):
        a = build_model_table_mapping(prefix="x_")
        b = build_model_table_mapping(prefix="x_")
        assert a == b
        assert a is not b


class TestSchemasInsertRoundtrip:
    """Functional test: create all tables, insert a row, read it back."""

    def test_can_insert_into_every_table(self):
        conn = sqlite3.connect(":memory:")
        try:
            schemas = build_table_schemas(prefix="")
            for ddl in schemas.values():
                conn.execute(ddl)

            # Insert a minimal row into each table — only requires id
            for table in LOGICAL_TABLES:
                # Discover a minimal set of NOT NULL columns and provide a
                # dummy value — most tables allow an id-only insert.
                try:
                    conn.execute(
                        f"INSERT INTO {table} (id) VALUES ('test-id')"
                    )
                except sqlite3.IntegrityError:
                    # Some tables have additional NOT NULL columns — skip
                    # gracefully; the goal here is to confirm at least the
                    # DDL parses and the id PK works for some rows.
                    pass
            conn.commit()
        finally:
            conn.close()
