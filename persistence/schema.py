"""Unified DDL definitions for GISPulse domain tables.

Single source of truth for table schemas used by both SQLiteRepository
(tables without prefix) and GpkgRepository (tables with ``_gispulse_`` prefix).

Usage::

    from persistence.schema import build_table_schemas, SERIALISATION_COLUMNS

    # For SQLiteRepository (no prefix)
    schemas = build_table_schemas(prefix="")

    # For GpkgRepository (_gispulse_ prefix)
    schemas = build_table_schemas(prefix="_gispulse_")
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Table definitions — {logical_name: column_defs}
# Each entry is a list of (column_name, column_type_and_default) tuples.
# The first column is always the primary key.
# ---------------------------------------------------------------------------

_TABLE_DEFS: dict[str, list[tuple[str, str]]] = {
    "rules": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT NOT NULL"),
        ("description", "TEXT DEFAULT ''"),
        ("scope", "TEXT DEFAULT 'global'"),
        ("scope_target_id", "TEXT"),
        ("capability", "TEXT DEFAULT ''"),
        ("config", "TEXT DEFAULT '{}'"),
        ("enabled", "INTEGER DEFAULT 1"),
        ("order_idx", "INTEGER DEFAULT 0"),
    ],
    "jobs": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT NOT NULL"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("dataset_id", "TEXT"),
        ("parameters", "TEXT DEFAULT '{}'"),
        ("created_at", "TEXT"),
        ("started_at", "TEXT"),
        ("completed_at", "TEXT"),
        ("result_path", "TEXT"),
        ("error_message", "TEXT"),
        ("attempts", "INTEGER DEFAULT 0"),
        ("max_retries", "INTEGER DEFAULT 3"),
    ],
    "datasets": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT NOT NULL"),
        ("source_path", "TEXT"),
        ("metadata", "TEXT DEFAULT '{}'"),
        ("created_at", "TEXT"),
        ("data_category", "TEXT DEFAULT 'vector'"),
        ("crs", "TEXT DEFAULT 'EPSG:4326'"),
        ("format", "TEXT"),
        ("ogc_source", "TEXT DEFAULT 'null'"),
    ],
    "layers": [
        ("id", "TEXT PRIMARY KEY"),
        ("dataset_id", "TEXT"),
        ("name", "TEXT NOT NULL"),
        ("geometry_type", "TEXT"),
        ("srid", "INTEGER DEFAULT 4326"),
        ("feature_count", "INTEGER DEFAULT 0"),
        ("layer_type", "TEXT DEFAULT 'vector'"),
        ("has_z", "INTEGER DEFAULT 0"),
        ("has_m", "INTEGER DEFAULT 0"),
    ],
    "scenarios": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT NOT NULL"),
        ("dataset_id", "TEXT"),
        ("jobs", "TEXT DEFAULT '[]'"),
        ("rules", "TEXT DEFAULT '[]'"),
        ("metadata", "TEXT DEFAULT '{}'"),
        ("created_at", "TEXT"),
        ("locked_by", "TEXT"),
        ("locked_at", "TEXT"),
        ("version", "INTEGER DEFAULT 1"),
        ("graph", "TEXT DEFAULT '{}'"),
    ],
    "triggers": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT NOT NULL"),
        ("description", "TEXT DEFAULT ''"),
        ("event", "TEXT DEFAULT 'manual'"),
        ("trigger_type", "TEXT DEFAULT 'api'"),
        ("category", "TEXT DEFAULT 'data'"),
        ("severity", "TEXT DEFAULT 'info'"),
        ("rule_id", "TEXT"),
        ("conditions", "TEXT DEFAULT '{}'"),
        ("predicates", "TEXT DEFAULT '[]'"),
        ("predicate_logic", "TEXT DEFAULT 'AND'"),
        ("actions", "TEXT DEFAULT '[]'"),
        ("enabled", "INTEGER DEFAULT 1"),
        ("auto_eval", "INTEGER DEFAULT 0"),
    ],
    "table_relations": [
        ("id", "TEXT PRIMARY KEY"),
        ("source_layer_id", "TEXT"),
        ("target_layer_id", "TEXT"),
        ("source_layer_name", "TEXT DEFAULT ''"),
        ("target_layer_name", "TEXT DEFAULT ''"),
        ("relation_type", "TEXT DEFAULT 'spatial'"),
        ("source_field", "TEXT"),
        ("target_field", "TEXT"),
        ("spatial_op", "TEXT"),
        ("spatial_config", "TEXT DEFAULT '{}'"),
        ("confidence", "REAL DEFAULT 1.0"),
        ("confirmed", "INTEGER DEFAULT 0"),
        ("auto_detected", "INTEGER DEFAULT 0"),
        ("label", "TEXT DEFAULT ''"),
        ("trigger_id", "TEXT"),
        ("computed_fields", "TEXT DEFAULT '[]'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "projects": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT NOT NULL"),
        ("description", "TEXT DEFAULT ''"),
        ("schema_name", "TEXT DEFAULT 'public'"),
        ("engine_backend", "TEXT DEFAULT 'duckdb'"),
        ("dsn", "TEXT"),
        ("datasets", "TEXT DEFAULT '[]'"),
        ("rules", "TEXT DEFAULT '[]'"),
        ("triggers", "TEXT DEFAULT '[]'"),
        ("metadata", "TEXT DEFAULT '{}'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "ref_layers": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT NOT NULL"),
        ("source_type", "TEXT DEFAULT 'gpkg_layer'"),
        ("source_path", "TEXT DEFAULT ''"),
        ("layer_name", "TEXT DEFAULT ''"),
        ("geom_col", "TEXT DEFAULT 'geom'"),
        ("srid", "INTEGER DEFAULT 4326"),
        ("cacheable", "INTEGER DEFAULT 1"),
        ("ttl_minutes", "INTEGER DEFAULT 60"),
        ("metadata", "TEXT DEFAULT '{}'"),
        ("created_at", "TEXT"),
    ],
}

# Tables unique to GPKG (not in SQLiteRepository)
_GPKG_ONLY_TABLES: dict[str, list[tuple[str, str]]] = {
    "change_log": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("table_name", "TEXT NOT NULL"),
        ("operation", "TEXT NOT NULL"),
        ("row_pk", "TEXT"),
        ("old_values", "TEXT"),
        ("new_values", "TEXT"),
        ("changed_at", "TEXT DEFAULT (datetime('now'))"),
        ("processed", "INTEGER DEFAULT 0"),
        # v2 (#7): geometry-change flag — TRUE when NEW.geom != OLD.geom
        # for UPDATE, NEW.geom IS NOT NULL for INSERT, OLD.geom IS NOT NULL
        # for DELETE. NULL for non-spatial layers.
        ("geom_changed", "INTEGER DEFAULT 0"),
    ],
    "kv": [
        ("key", "TEXT PRIMARY KEY"),
        ("value", "TEXT"),
        ("updated_at", "TEXT DEFAULT (datetime('now'))"),
    ],
}

# Logical table names (used by SQLiteRepository and model mapping)
LOGICAL_TABLES = list(_TABLE_DEFS.keys())


# ---------------------------------------------------------------------------
# Serialisation column sets (used by sqlite_repository / gpkg_repository)
# ---------------------------------------------------------------------------

# Columns that store JSON (serialised as TEXT)
JSON_COLUMNS = frozenset({
    "config", "parameters", "metadata", "conditions",
    "predicates", "actions", "jobs", "rules", "graph",
    "datasets", "triggers", "ogc_source",
    "spatial_config", "computed_fields",
})

# Columns that store datetimes (serialised as ISO text)
DATETIME_COLUMNS = frozenset({
    "created_at", "started_at", "completed_at", "locked_at",
    "updated_at", "expired_at", "torn_down_at",
})

# Columns that store UUIDs (serialised as TEXT)
UUID_COLUMNS = frozenset({
    "id", "dataset_id", "job_id", "rule_id",
    "source_layer_id", "target_layer_id", "trigger_id",
    "scope_target_id",
})

# Columns that store booleans (serialised as INTEGER 0/1)
BOOL_COLUMNS = frozenset({
    "enabled", "auto_eval", "confirmed", "auto_detected",
    "cacheable",
})


# ---------------------------------------------------------------------------
# DDL generators
# ---------------------------------------------------------------------------


def _build_create_table(table_name: str, columns: list[tuple[str, str]]) -> str:
    """Generate a CREATE TABLE IF NOT EXISTS statement."""
    col_defs = ",\n            ".join(f"{col} {spec}" for col, spec in columns)
    return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            {col_defs}
        )
    """


def build_table_schemas(prefix: str = "") -> dict[str, str]:
    """Generate DDL for all domain tables with the given prefix.

    Args:
        prefix: Table name prefix. Use ``""`` for SQLiteRepository,
                ``"_gispulse_"`` for GpkgRepository.

    Returns:
        Dict mapping prefixed table name to its CREATE TABLE DDL.
    """
    result: dict[str, str] = {}
    for logical_name, columns in _TABLE_DEFS.items():
        table_name = f"{prefix}{logical_name}"
        result[table_name] = _build_create_table(table_name, columns)
    return result


def build_gpkg_extra_schemas(prefix: str = "_gispulse_") -> dict[str, str]:
    """Generate DDL for GPKG-only tables (change_log, kv)."""
    result: dict[str, str] = {}
    for logical_name, columns in _GPKG_ONLY_TABLES.items():
        table_name = f"{prefix}{logical_name}"
        result[table_name] = _build_create_table(table_name, columns)
    return result


def build_all_gpkg_schemas(prefix: str = "_gispulse_") -> dict[str, str]:
    """Generate DDL for ALL internal GPKG tables (domain + extras)."""
    schemas = build_table_schemas(prefix)
    schemas.update(build_gpkg_extra_schemas(prefix))
    return schemas


def build_model_table_mapping(prefix: str = "_gispulse_") -> dict[str, str]:
    """Generate mapping from logical name → prefixed table name."""
    return {name: f"{prefix}{name}" for name in _TABLE_DEFS}


# Current schema version — increment when adding/removing/altering columns
# v1 → v2 (2026-04-27, #7): added _gispulse_change_log.geom_changed,
#                           triggers now populate new_values / old_values JSON
# v2 → v3 (2026-05-05, #103 B-02): tracked layers grow a ``_gispulse_origin``
#                                  TEXT column + AFTER UPDATE triggers gain a
#                                  WHEN clause that suppresses re-fires when
#                                  an action_dispatcher write-back tagged the
#                                  row with ``trigger:<id>`` (origin-tagging M1).
SCHEMA_VERSION = 3
