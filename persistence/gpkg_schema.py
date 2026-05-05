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

from core.sql_safety import slug_identifier, validate_layer_name
from persistence.schema import (
    SCHEMA_VERSION,
    build_all_gpkg_schemas,
    build_model_table_mapping,
)

# ---------------------------------------------------------------------------
# Identifier validation
# ---------------------------------------------------------------------------
# P0-4c (Beta) + B-05 (v1.5.3): layer/identifier names are interpolated into
# trigger DDL via f-strings (CREATE TRIGGER + INSERT INTO ... VALUES('{layer}',
# ...)). A name containing single-quotes, double-quotes or semicolons becomes
# a textbook SQL injection vector. We refuse such names up front with
# ValueError so callers (HTTP endpoints, engine wrappers) can surface a clean
# 400 instead of a silently broken trigger or a dropped table.
#
# B-05 (2026-05-04): the legacy regex ``^[^\W\d][\w]*$`` rejected QGIS
# desktop layer names containing spaces ("Parcelles cadastrales 2024"),
# dashes ("voies-rapides") or leading digits — killing FR adoption. The
# validation now delegates to :func:`core.sql_safety.validate_layer_name`,
# which accepts any character except those that break the quoted DDL
# (``"``, ``'``, ``;``, ``\\``, NUL, control chars). Trigger names are
# derived through :func:`core.sql_safety.slug_identifier` so they stay
# pure-ASCII and stable across the original layer name's casing /
# encoding.


def _validate_identifier(name: str) -> str:
    """Validate that *name* is a safe SQL layer/column identifier.

    Backward-compat shim — delegates to
    :func:`core.sql_safety.validate_layer_name`. Kept as a module-level
    import so existing tests
    (``from persistence.gpkg_schema import _validate_identifier``) keep
    working after the B-05 relaxation.
    """
    return validate_layer_name(name)

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

def _build_change_triggers(
    table_name: str,
    pk_col: str = "fid",
    *,
    columns: list[str] | None = None,
    geom_col: str | None = None,
) -> list[str]:
    """Generate INSERT/UPDATE/DELETE trigger SQL for a spatial layer.

    All identifier arguments (``table_name``, ``pk_col``, every entry in
    ``columns``, ``geom_col``) MUST be valid SQL identifiers — they are
    interpolated directly into the DDL (no parameter binding possible in
    DDL). See :func:`_validate_identifier` for the rules.

    Args:
        table_name: Spatial layer name.
        pk_col:     Primary-key column (default ``fid`` per GPKG spec).
        columns:    Non-pk, non-geometry columns to capture in the
                    ``new_values`` / ``old_values`` JSON payload. When
                    ``None`` or empty, the JSON column is set to
                    ``json_object()`` (empty object).
        geom_col:   Geometry column name. When set, the ``geom_changed``
                    flag is populated as
                    ``NEW.<geom> IS NOT NULL`` for INSERT,
                    ``NEW.<geom> IS NOT OLD.<geom>`` for UPDATE,
                    ``OLD.<geom> IS NOT NULL`` for DELETE.
                    When ``None``, ``geom_changed`` is always ``0``.

    Raises:
        ValueError: If any identifier is unsafe.
    """
    _validate_identifier(table_name)
    _validate_identifier(pk_col)
    cols = list(columns or [])
    for c in cols:
        _validate_identifier(c)
    if geom_col is not None:
        _validate_identifier(geom_col)

    # B-05: trigger object names must remain pure ASCII so they can be
    # referenced without quoting in DROP TRIGGER and so SQLite's catalog
    # ``sqlite_master`` stays readable. The layer reference in
    # ``ON "{table_name}"`` and the literal in ``VALUES ('{table_name}',
    # ...)`` keep the original (Unicode-safe) name because the validator
    # has already refused single/double quotes that would close the
    # surrounding quotes.
    slug = slug_identifier(table_name)

    def _json_obj(ref: str) -> str:
        if not cols:
            return "json_object()"
        pairs = [f"'{c}', {ref}.\"{c}\"" for c in cols]
        return f"json_object({', '.join(pairs)})"

    if geom_col:
        gc_insert = f'(NEW."{geom_col}" IS NOT NULL)'
        gc_update = f'(NEW."{geom_col}" IS NOT OLD."{geom_col}")'
        gc_delete = f'(OLD."{geom_col}" IS NOT NULL)'
    else:
        gc_insert = gc_update = gc_delete = "0"

    insert_sql = (
        f'CREATE TRIGGER IF NOT EXISTS "_gispulse_trg_{slug}_insert"\n'
        f'AFTER INSERT ON "{table_name}"\n'
        f"BEGIN\n"
        f"  INSERT INTO _gispulse_change_log"
        f"(table_name, operation, row_pk, new_values, geom_changed)\n"
        f"  VALUES ('{table_name}', 'INSERT', "
        f'NEW."{pk_col}", {_json_obj("NEW")}, {gc_insert});\n'
        f"END"
    )

    # B-02 (v1.5.3, #103): origin-tagging M1. The AFTER UPDATE trigger
    # gains a WHEN clause that suppresses re-fires when the
    # ``action_dispatcher`` write-back tagged the row with
    # ``trigger:<id>``. Two sub-conditions:
    #   1. ``NEW._gispulse_origin`` not a trigger marker  → fire
    #   2. ...except the action_dispatcher's own "clear sentinel"
    #      UPDATE (``NEW=NULL`` while ``OLD`` was a trigger marker) —
    #      that reset must NOT loop back, so we suppress it here.
    # The column is added by :func:`install_change_tracking` (v3 schema),
    # so the WHEN clause is safe to reference unconditionally.
    update_sql = (
        f'CREATE TRIGGER IF NOT EXISTS "_gispulse_trg_{slug}_update"\n'
        f'AFTER UPDATE ON "{table_name}"\n'
        f"WHEN (NEW.\"_gispulse_origin\" IS NULL "
        f"OR NEW.\"_gispulse_origin\" NOT LIKE 'trigger:%')\n"
        f"  AND NOT (\n"
        f"    NEW.\"_gispulse_origin\" IS NULL\n"
        f"    AND OLD.\"_gispulse_origin\" IS NOT NULL\n"
        f"    AND OLD.\"_gispulse_origin\" LIKE 'trigger:%'\n"
        f"  )\n"
        f"BEGIN\n"
        f"  INSERT INTO _gispulse_change_log"
        f"(table_name, operation, row_pk, new_values, old_values, geom_changed)\n"
        f"  VALUES ('{table_name}', 'UPDATE', "
        f'NEW."{pk_col}", {_json_obj("NEW")}, {_json_obj("OLD")}, {gc_update});\n'
        f"END"
    )

    delete_sql = (
        f'CREATE TRIGGER IF NOT EXISTS "_gispulse_trg_{slug}_delete"\n'
        f'AFTER DELETE ON "{table_name}"\n'
        f"BEGIN\n"
        f"  INSERT INTO _gispulse_change_log"
        f"(table_name, operation, row_pk, old_values, geom_changed)\n"
        f"  VALUES ('{table_name}', 'DELETE', "
        f'OLD."{pk_col}", {_json_obj("OLD")}, {gc_delete});\n'
        f"END"
    )

    return [insert_sql, update_sql, delete_sql]


def _inspect_layer(
    conn: sqlite3.Connection, layer_name: str, pk_col: str
) -> tuple[list[str], str | None]:
    """Return (non-pk non-geom columns, geom_col_name) for a spatial layer.

    Reads ``PRAGMA table_info`` + ``gpkg_geometry_columns``. When the
    geometry registration is missing (non-spatial table or partially
    bootstrapped GPKG), falls back to detecting columns whose declared
    type matches a known geometry keyword (``POINT``, ``POLYGON``,
    ``LINESTRING``, ``GEOMETRY``, ``MULTI*``).

    Internal helper for :func:`install_change_tracking` — kept here
    rather than in the engine to avoid a circular import.
    """
    geom_col: str | None = None
    try:
        row = conn.execute(
            "SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?",
            (layer_name,),
        ).fetchone()
        if row is not None:
            geom_col = row[0]
    except sqlite3.OperationalError:
        pass  # gpkg_geometry_columns missing → fall back below

    geometry_type_keywords = (
        "POINT", "POLYGON", "LINESTRING",
        "MULTIPOINT", "MULTIPOLYGON", "MULTILINESTRING",
        "GEOMETRY", "GEOMCOLLECTION", "GEOMETRYCOLLECTION",
    )

    non_pk_cols: list[str] = []
    for cid, name, ctype, *_ in conn.execute(
        f'PRAGMA table_info("{layer_name}")'
    ).fetchall():
        if name == pk_col:
            continue
        if geom_col is None and ctype:
            if ctype.upper() in geometry_type_keywords:
                geom_col = name
                continue
        if name == geom_col:
            continue
        non_pk_cols.append(name)

    return non_pk_cols, geom_col


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


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> bool:
    """Add ``geom_changed`` column to ``_gispulse_change_log`` (v1 → v2, #7).

    Idempotent: if the column already exists (table created fresh under v2
    schema, or migration already applied), this is a no-op.

    Returns True when an ALTER TABLE was executed, False otherwise.
    """
    try:
        cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(_gispulse_change_log)"
            ).fetchall()
        }
    except sqlite3.OperationalError:
        # Table doesn't exist yet — nothing to migrate; the bootstrap DDL
        # will create it with the v2 schema.
        return False

    # PRAGMA table_info returns an empty list for a missing table (it does
    # not raise). Same outcome as the OperationalError branch: skip.
    if not cols or "geom_changed" in cols:
        return False

    conn.execute(
        "ALTER TABLE _gispulse_change_log ADD COLUMN geom_changed INTEGER DEFAULT 0"
    )
    logger.info("schema_migration_v1_to_v2: added _gispulse_change_log.geom_changed")
    return True


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> int:
    """Re-install change tracking on every previously tracked layer (v2→v3).

    B-02 (#103): v3 grows the per-layer ``_gispulse_origin`` sentinel
    column and the AFTER UPDATE WHEN-clause. Existing v2 GPKGs need
    both the column added and their triggers rebuilt; a fresh-install
    bootstrap leaves v3 triggers in place but does not touch user
    layers.

    The migration enumerates every distinct ``tbl_name`` referenced by
    a ``_gispulse_trg_*`` trigger, pulls the layer's columns from
    :func:`_inspect_layer` (so the existing JSON payload contract is
    preserved), and calls :func:`install_change_tracking` — which
    drops the v2 triggers and re-creates them with the v3 WHEN clause.

    Idempotent.

    Returns:
        The number of layers that were re-installed (0 when no layer
        was tracked or the project was already v3).
    """
    layer_names: list[str] = []
    try:
        rows = conn.execute(
            "SELECT DISTINCT tbl_name FROM sqlite_master "
            "WHERE type = 'trigger' "
            "AND name LIKE '\\_gispulse\\_trg\\_%' ESCAPE '\\'"
        ).fetchall()
    except sqlite3.OperationalError:
        # sqlite_master always exists in SQLite, but be defensive.
        return 0
    layer_names = [str(r[0]) for r in rows if r[0]]
    if not layer_names:
        return 0
    for layer in layer_names:
        # ``install_change_tracking`` validates the identifier, ensures
        # the sentinel column, drops legacy triggers, and re-creates
        # them with the v3 WHEN clause. Crucially we honour the
        # original PK column name (``id``, ``fid``, ...) — the legacy
        # default of ``fid`` would silently rewrite the trigger DDL
        # with ``NEW."fid"`` and break tables whose PK is named
        # otherwise.
        try:
            pk_col = _detect_pk_col(conn, layer)
            install_change_tracking(conn, layer, pk_col=pk_col)
        except ValueError as exc:
            # A pre-B-05 GPKG could conceivably hold a layer name that
            # is now rejected by ``validate_layer_name`` (control char,
            # quote, ...). Such a name could never have produced a
            # legal trigger in the first place — log and skip.
            logger.warning(
                "schema_migration_v2_to_v3_skipped layer=%r reason=%s",
                layer,
                exc,
            )
    logger.info(
        "schema_migration_v2_to_v3: rebuilt %d tracked layer(s)",
        len(layer_names),
    )
    return len(layer_names)


def bootstrap_gpkg_project(conn: sqlite3.Connection) -> None:
    """Create all _gispulse_* tables and register them in gpkg_extensions.

    Also ensures the GPKG core tables (gpkg_spatial_ref_sys, gpkg_contents)
    exist so the file is a valid GeoPackage even before any spatial layer
    is written.

    Safe to call multiple times (all DDL is IF NOT EXISTS) and applies any
    pending schema migration so existing v1 GPKGs are upgraded in-place.
    """
    # Ensure valid GPKG structure first
    _ensure_gpkg_core_tables(conn)

    # Ensure gpkg_extensions exists
    _ensure_gpkg_extensions_table(conn)

    # Apply v1 → v2 migration BEFORE the IF NOT EXISTS DDL, so the column
    # is added to existing tables (the DDL would skip the table entirely
    # because it already exists).
    _migrate_v1_to_v2(conn)
    # B-02 (#103): v2 → v3 rebuilds per-layer triggers + adds the
    # ``_gispulse_origin`` sentinel column. Runs after the internal
    # tables exist (the migration relies on
    # :func:`install_change_tracking`, which logs through the schema).
    _migrate_v2_to_v3(conn)

    # Create internal tables
    for table_name, ddl in _TABLE_DDL.items():
        conn.execute(ddl)
        _register_extension(conn, table_name)

    # Schema versioning — refresh to current version (idempotent).
    conn.execute(
        "INSERT INTO _gispulse_kv (key, value, updated_at) "
        "VALUES ('schema_version', ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=datetime('now')",
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
    *,
    columns: list[str] | None = None,
    geom_col: str | None = None,
) -> None:
    """Install INSERT/UPDATE/DELETE triggers on a spatial layer.

    The triggers populate ``_gispulse_change_log`` with:
        * ``new_values`` JSON for INSERT and UPDATE
        * ``old_values`` JSON for UPDATE and DELETE
        * ``geom_changed`` flag (1 when the geometry column actually
          changed, 0 otherwise — useful for rules that only care about
          attribute edits, not move/reshape)

    Column inspection is automatic: if *columns* / *geom_col* are
    omitted, the function reads ``PRAGMA table_info`` +
    ``gpkg_geometry_columns`` to discover them. Pass them explicitly only
    when you need to limit the JSON payload (very wide tables, sensitive
    fields, etc.).

    Args:
        conn:       Open SQLite connection to the GPKG file.
        layer_name: Spatial table to track. Any string except those
                    containing ``"``, ``'``, ``;``, ``\\`` or control
                    chars — see :func:`_validate_identifier`.
        pk_col:     Primary key column (default ``fid`` per GPKG spec).
        columns:    Optional explicit list of columns to capture in the
                    JSON payload. When ``None``, all non-pk non-geom
                    columns of the layer are inspected and included.
        geom_col:   Optional explicit geometry column. When ``None``, the
                    geometry column is auto-detected from
                    ``gpkg_geometry_columns`` (preferred) or
                    ``PRAGMA table_info`` declared types.

    Raises:
        ValueError: If any identifier is unsafe.
    """
    _validate_identifier(layer_name)
    _validate_identifier(pk_col)

    if columns is None or geom_col is None:
        auto_cols, auto_geom = _inspect_layer(conn, layer_name, pk_col)
        if columns is None:
            columns = auto_cols
        if geom_col is None:
            geom_col = auto_geom

    # B-02 (#103): ensure the v3 sentinel column exists on the layer
    # *before* the AFTER UPDATE trigger references it in its WHEN
    # clause. Idempotent — skip when already present.
    _ensure_origin_column(conn, layer_name)
    # Drop any existing GISPulse triggers on this layer so a re-install
    # picks up the v3 WHEN clause (CREATE TRIGGER IF NOT EXISTS would
    # otherwise leave the v2 trigger in place).
    for trg in _list_gispulse_triggers(conn, layer_name):
        conn.execute(f'DROP TRIGGER IF EXISTS "{trg}"')

    for sql in _build_change_triggers(
        layer_name, pk_col, columns=columns, geom_col=geom_col
    ):
        conn.execute(sql)
    conn.commit()
    logger.info(
        "change_tracking_installed: %s (pk=%s, cols=%d, geom=%s)",
        layer_name,
        pk_col,
        len(columns or []),
        geom_col,
    )


def _ensure_origin_column(conn: sqlite3.Connection, layer_name: str) -> bool:
    """B-02: add ``_gispulse_origin TEXT`` to the layer if missing.

    The column is the loop-bypass sentinel for origin-tagging M1: the
    action_dispatcher writes ``trigger:<id>`` here, the AFTER UPDATE
    trigger's WHEN clause skips when the marker is present.

    Idempotent.

    Returns:
        ``True`` when an ``ALTER TABLE`` was issued, ``False`` when the
        column was already there.
    """
    cols = {
        row[1]
        for row in conn.execute(
            f'PRAGMA table_info("{layer_name}")'
        ).fetchall()
    }
    if "_gispulse_origin" in cols:
        return False
    conn.execute(
        f'ALTER TABLE "{layer_name}" ADD COLUMN "_gispulse_origin" TEXT'
    )
    logger.info("origin_column_added: %s._gispulse_origin", layer_name)
    return True


def _detect_pk_col(conn: sqlite3.Connection, layer_name: str) -> str:
    """Return the layer's primary key column name (defaults to ``"fid"``).

    B-02 (#103): the v2→v3 migration re-installs change tracking on
    every previously tracked layer. The legacy default of
    ``pk_col="fid"`` would silently rewrite the trigger DDL with
    ``NEW."fid"`` even on tables whose PK is named otherwise (``id``,
    ``rowid``, ...) — breaking ``test_set_field_e2e`` and any caller
    that originally passed an explicit ``pk_col=`` to
    :func:`install_change_tracking`.

    SQLite's ``PRAGMA table_info`` reports the PK position in column 5
    (``pk``) — non-zero means PK, with the position when composite. We
    pick the *first* PK column; for single-column PKs that's always
    correct. For multi-column PKs (rare in GPKG layers) the change-log
    can only carry one ``row_pk`` value, so taking the first is the
    conservative compromise.

    Falls back to ``"fid"`` when PRAGMA returns no PK (legacy GPKG
    layers without a declared primary key would have failed install
    too — keep the legacy default for graceful degradation).
    """
    try:
        rows = conn.execute(
            f'PRAGMA table_info("{layer_name}")'
        ).fetchall()
    except Exception:
        return "fid"
    pk_cols = sorted(
        ((row[5], row[1]) for row in rows if row[5]),
        key=lambda t: t[0],
    )
    if pk_cols:
        return str(pk_cols[0][1])
    return "fid"


def _list_gispulse_triggers(
    conn: sqlite3.Connection, layer_name: str
) -> list[str]:
    """Return the names of every ``_gispulse_trg_*`` trigger on *layer_name*.

    Used by :func:`install_change_tracking` (drop-then-recreate) and by
    the v2 → v3 migration so it can re-install change tracking on every
    layer that already had it.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'trigger' "
        "AND tbl_name = ? "
        "AND name LIKE '\\_gispulse\\_trg\\_%' ESCAPE '\\'",
        (layer_name,),
    ).fetchall()
    return [r[0] for r in rows]


def uninstall_change_tracking(conn: sqlite3.Connection, layer_name: str) -> None:
    """Remove change tracking triggers for a spatial layer.

    Raises:
        ValueError: If *layer_name* contains unsafe characters.
    """
    _validate_identifier(layer_name)
    # B-05: derive the trigger name from the same slug used at install
    # time so layer names with spaces / accents (post-B-05) and ASCII-safe
    # legacy names (pre-B-05) both round-trip cleanly.
    slug = slug_identifier(layer_name)
    for op in ("insert", "update", "delete"):
        conn.execute(f'DROP TRIGGER IF EXISTS "_gispulse_trg_{slug}_{op}"')
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
