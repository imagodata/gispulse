"""
GeoPackage project schema — DDL, gpkg_extensions registration, migrations.

All GISPulse-internal tables use the ``_gispulse_`` prefix and are registered
in ``gpkg_extensions`` per OGC GPKG Annex F.  They never appear in
``gpkg_contents``, so QGIS/GDAL ignore them completely.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from persistence.schema import (
    SCHEMA_VERSION,
    build_all_gpkg_schemas,
    build_model_table_mapping,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extension metadata
# ---------------------------------------------------------------------------

EXTENSION_NAME = "gispulse"
EXTENSION_DEFINITION = "https://gispulse.dev/gpkg-extension"
EXTENSION_SCOPE = "read-write"

# ---------------------------------------------------------------------------
# Generated from unified schema definitions
# ---------------------------------------------------------------------------

_TABLE_DDL = build_all_gpkg_schemas(prefix="_gispulse_")

INTERNAL_TABLES = list(_TABLE_DDL.keys())

MODEL_TABLE_MAPPING = build_model_table_mapping(prefix="_gispulse_")


# ---------------------------------------------------------------------------
# Change tracking trigger templates
# ---------------------------------------------------------------------------

_CHANGE_TRIGGER_TEMPLATE = """
CREATE TRIGGER IF NOT EXISTS "_gispulse_trg_{table}_{op}"
AFTER {OP} ON "{table}"
BEGIN
  INSERT INTO _gispulse_change_log(table_name, operation, row_pk, {extra_cols})
  VALUES ('{table}', '{OP}', {pk_expr}, {extra_vals});
END
"""


def _build_change_triggers(table_name: str, pk_col: str = "fid") -> list[str]:
    """Generate INSERT/UPDATE/DELETE trigger SQL for a spatial layer."""
    triggers = []
    for op, ref, extra_c, extra_v in [
        ("insert", "NEW", "new_values", "NULL"),
        ("update", "NEW", "new_values, old_values", "NULL, NULL"),
        ("delete", "OLD", "old_values", "NULL"),
    ]:
        sql = (
            f'CREATE TRIGGER IF NOT EXISTS "_gispulse_trg_{table_name}_{op}"\n'
            f"AFTER {op.upper()} ON \"{table_name}\"\n"
            f"BEGIN\n"
            f"  INSERT INTO _gispulse_change_log(table_name, operation, row_pk)\n"
            f"  VALUES ('{table_name}', '{op.upper()}', {ref}.{pk_col});\n"
            f"END"
        )
        triggers.append(sql)
    return triggers


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------


def _ensure_gpkg_extensions_table(conn: sqlite3.Connection) -> None:
    """Create the gpkg_extensions table if it doesn't exist.

    Some GPKG files created by pyogrio may not have this table if no
    extensions were registered.  OGC spec says it's optional until needed.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gpkg_extensions (
            table_name TEXT,
            column_name TEXT,
            extension_name TEXT NOT NULL,
            definition TEXT NOT NULL,
            scope TEXT NOT NULL,
            CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name)
        )
    """)


def _register_extension(conn: sqlite3.Connection, table_name: str) -> None:
    """Register a single internal table in gpkg_extensions."""
    conn.execute(
        """
        INSERT OR IGNORE INTO gpkg_extensions
            (table_name, column_name, extension_name, definition, scope)
        VALUES (?, NULL, ?, ?, ?)
        """,
        (table_name, EXTENSION_NAME, EXTENSION_DEFINITION, EXTENSION_SCOPE),
    )


def _ensure_gpkg_core_tables(conn: sqlite3.Connection) -> None:
    """Create the OGC GeoPackage core tables if they don't exist.

    A valid GPKG must have ``gpkg_spatial_ref_sys``, ``gpkg_contents``,
    and the correct ``application_id`` pragma.  If we're opening a fresh
    SQLite file that has no spatial layers yet, we need to initialise these
    so that GDAL/pyogrio can append layers later.
    """
    # Set GPKG application_id (0x47504B47 = 'GPKG')
    conn.execute("PRAGMA application_id=1196444487")

    # gpkg_spatial_ref_sys — CRS catalog
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL PRIMARY KEY,
            organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL,
            definition TEXT NOT NULL,
            description TEXT
        )
    """)
    # Seed with WGS 84 and undefined CRS entries (OGC spec requirement)
    conn.execute("""
        INSERT OR IGNORE INTO gpkg_spatial_ref_sys
            (srs_name, srs_id, organization, organization_coordsys_id, definition)
        VALUES ('Undefined cartesian SRS', -1, 'NONE', -1, 'undefined')
    """)
    conn.execute("""
        INSERT OR IGNORE INTO gpkg_spatial_ref_sys
            (srs_name, srs_id, organization, organization_coordsys_id, definition)
        VALUES ('Undefined geographic SRS', 0, 'NONE', 0, 'undefined')
    """)
    conn.execute("""
        INSERT OR IGNORE INTO gpkg_spatial_ref_sys
            (srs_name, srs_id, organization, organization_coordsys_id, definition)
        VALUES ('WGS 84 geodetic', 4326, 'EPSG', 4326,
                'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]')
    """)

    # gpkg_contents — layer catalog
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gpkg_contents (
            table_name TEXT NOT NULL PRIMARY KEY,
            data_type TEXT NOT NULL DEFAULT 'features',
            identifier TEXT UNIQUE,
            description TEXT DEFAULT '',
            last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            min_x DOUBLE,
            min_y DOUBLE,
            max_x DOUBLE,
            max_y DOUBLE,
            srs_id INTEGER,
            CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
        )
    """)

    # gpkg_geometry_columns
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gpkg_geometry_columns (
            table_name TEXT NOT NULL,
            column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL DEFAULT 'GEOMETRY',
            srs_id INTEGER NOT NULL,
            z TINYINT NOT NULL DEFAULT 0,
            m TINYINT NOT NULL DEFAULT 0,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
            CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
            CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
        )
    """)

    conn.commit()


def bootstrap_gpkg_project(conn: sqlite3.Connection) -> None:
    """Create all _gispulse_* tables and register them in gpkg_extensions.

    Also ensures the GPKG core tables (gpkg_spatial_ref_sys, gpkg_contents)
    exist so the file is a valid GeoPackage even before any spatial layer
    is written.

    Safe to call multiple times (all DDL is IF NOT EXISTS).
    """
    # Ensure valid GPKG structure first
    _ensure_gpkg_core_tables(conn)

    # Ensure gpkg_extensions exists
    _ensure_gpkg_extensions_table(conn)

    # Create internal tables
    for table_name, ddl in _TABLE_DDL.items():
        conn.execute(ddl)
        _register_extension(conn, table_name)

    # Schema versioning — store current version in KV store
    conn.execute(
        "INSERT OR IGNORE INTO _gispulse_kv (key, value, updated_at) "
        "VALUES ('schema_version', ?, datetime('now'))",
        (str(SCHEMA_VERSION),),
    )

    conn.commit()
    logger.info(
        "gpkg_project_bootstrapped: %d internal tables (schema v%d)",
        len(_TABLE_DDL),
        SCHEMA_VERSION,
    )


def install_change_tracking(
    conn: sqlite3.Connection,
    layer_name: str,
    pk_col: str = "fid",
) -> None:
    """Install INSERT/UPDATE/DELETE triggers on a spatial layer.

    Args:
        conn:       Open SQLite connection to the GPKG file.
        layer_name: Name of the spatial table to track.
        pk_col:     Primary key column (default ``fid`` per GPKG spec).
    """
    for sql in _build_change_triggers(layer_name, pk_col):
        conn.execute(sql)
    conn.commit()
    logger.info("change_tracking_installed: %s (pk=%s)", layer_name, pk_col)


def uninstall_change_tracking(conn: sqlite3.Connection, layer_name: str) -> None:
    """Remove change tracking triggers for a spatial layer."""
    for op in ("insert", "update", "delete"):
        conn.execute(f'DROP TRIGGER IF EXISTS "_gispulse_trg_{layer_name}_{op}"')
    conn.commit()
    logger.info("change_tracking_removed: %s", layer_name)


# ---------------------------------------------------------------------------
# Migration: existing gispulse.db → GPKG project
# ---------------------------------------------------------------------------


def migrate_sqlite_to_gpkg(
    old_db_path: str | Path,
    gpkg_conn: sqlite3.Connection,
) -> dict[str, int]:
    """Copy domain objects from the old SQLite repository into a GPKG project.

    Args:
        old_db_path: Path to ``~/.gispulse/gispulse.db``.
        gpkg_conn:   Open connection to the target GPKG project.

    Returns:
        Dict mapping table names to row counts migrated.
    """
    old_db_path = Path(old_db_path)
    if not old_db_path.exists():
        return {}

    # Ensure target schema exists
    bootstrap_gpkg_project(gpkg_conn)

    old_conn = sqlite3.connect(str(old_db_path))
    old_conn.row_factory = sqlite3.Row
    stats: dict[str, int] = {}

    try:
        for old_table, new_table in MODEL_TABLE_MAPPING.items():
            # Check if old table exists
            cur = old_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (old_table,),
            )
            if cur.fetchone() is None:
                continue

            rows = old_conn.execute(f"SELECT * FROM {old_table}").fetchall()
            if not rows:
                continue

            columns = rows[0].keys()
            placeholders = ", ".join("?" for _ in columns)
            col_names = ", ".join(columns)
            insert_sql = (
                f"INSERT OR IGNORE INTO {new_table} ({col_names}) "
                f"VALUES ({placeholders})"
            )

            for row in rows:
                gpkg_conn.execute(insert_sql, tuple(row))

            stats[old_table] = len(rows)

        gpkg_conn.commit()
        logger.info("migration_complete: %s", stats)
    finally:
        old_conn.close()

    return stats
