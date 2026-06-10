"""
PostGIS bulk operations for PG-direct spatial workflows.

Pattern: PG-direct + ST_Subdivide
======================================
Spatial joins on large geometry layers (GPU zones, IRIS, OCSGE, …) cause
Out-Of-Memory errors in DuckDB when run at the national scale.  The durable
pattern is:

1. **Subdivide** large geometries in PostGIS using ``ST_Subdivide``:
   each original geometry is exploded into sub-polygons of at most
   ``max_vertices`` vertices.  This bounds the cost of spatial index lookups:
   *brut ≈ 12 min/commune vs. subdivisé ≈ 5 s*.

2. **Batch-load** the scoped data from a DuckDB query result into the
   subdivided PostGIS table using ``executemany`` in fixed-size batches,
   flushed with a single ``commit`` (atomique: delete + all inserts share
   one transaction).

Trust boundaries
----------------
* ``delete_predicate`` and ``insert_predicate`` (SubdivideSpec) — **trusted
  caller input**.  The caller is responsible for their safety, exactly like
  ``pre_drop_sql`` in the lifecycle module.  They are interpolated as-is into
  the generated SQL.
* ``select_sql`` and ``insert_sql`` (batch_load_duckdb_to_postgis) — likewise
  **trusted caller input**.
* Identifier arguments (schema/table/column names) and SQL type strings are
  **validated** by this module against strict whitelists before interpolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from gispulse.core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Identifier + type validation helpers
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# Strict type whitelist: lowercase letters and spaces (base type name), with
# an optional parenthesised numeric precision/scale suffix — e.g. varchar(255)
# or numeric(10,2).  Rejects multi-statement injections like
# "text, injected text" or "text NOT NULL, x text" because commas are only
# allowed inside the parentheses and only between digits.
_TYPE_RE = re.compile(r"^[a-z][a-z ]*(\(\s*\d+(\s*,\s*\d+)?\s*\))?$")


def _validate_ident(value: str, label: str) -> str:
    """Validate a SQL identifier (lowercase only, no quoting needed).

    Raises ValueError if *value* does not match ``^[a-z_][a-z0-9_]*$``.
    """
    if not _IDENT_RE.match(value):
        raise ValueError(
            f"Invalid SQL identifier for {label!r}: {value!r}. "
            "Must match ^[a-z_][a-z0-9_]*$ (lowercase, no special chars)."
        )
    return value


def _validate_type(value: str, column: str) -> str:
    """Validate a SQL type string.

    Accepts lowercase base type names (letters and spaces) with an optional
    numeric precision/scale suffix: ``text``, ``bigint``, ``double precision``,
    ``timestamptz``, ``varchar(255)``, ``numeric(10,2)``.

    Rejects multi-statement injections such as ``text, injected text``,
    ``text NOT NULL, x text``, or ``jsonb); DROP`` — commas are only permitted
    between digits inside parentheses.

    Note: the ``geom geometry(Geometry, {srid})`` column is built by the
    generator itself and never passes through this validator.

    Raises ValueError if the value does not match the whitelist.
    """
    if not _TYPE_RE.match(value):
        raise ValueError(
            f"Invalid SQL type for column {column!r}: {value!r}. "
            "Expected lowercase type name with optional numeric precision, "
            r"e.g. 'text', 'double precision', 'varchar(255)', 'numeric(10,2)'."
        )
    return value


def _qualified(schema: str, table: str) -> str:
    """Return ``schema.table`` after validating both identifiers."""
    return f"{_validate_ident(schema, 'schema')}.{_validate_ident(table, 'table')}"


# ---------------------------------------------------------------------------
# SubdivideSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubdivideSpec:
    """Declarative description of a ST_Subdivide materialisation.

    Parameters
    ----------
    source_schema:
        Schema of the source table (must exist in the same database).
    source_table:
        Name of the source table.
    target_schema:
        Schema where the subdivided table will be created.
    target_table:
        Name of the subdivided table to create / refresh.
    geom_column:
        Name of the geometry column on the source table.
    max_vertices:
        Maximum number of vertices per subdivided fragment (passed to
        ``ST_Subdivide``).  Default: 256.
    key_columns:
        Tuple of column names that together form the natural key of a feature
        (used as part of the PRIMARY KEY alongside ``fragment_id``).
    data_columns:
        Extra columns copied verbatim from the source row into the target.
    column_types:
        Mapping ``{column_name: sql_type}`` for every column in
        ``key_columns`` and ``data_columns``.  A ``ValueError`` is raised at
        SQL-build time if any column is missing from this mapping.
    srid:
        SRID for the ``geom`` column.  Default: 4326.
    delete_predicate:
        **Trusted caller input** — an optional SQL predicate applied to the
        DELETE statement.  The target table is aliased ``frag`` in that
        context, e.g. ``frag.dept = '63'``.  When ``None``, the DELETE
        removes all rows from the target table (full-refresh semantics).
    insert_predicate:
        **Trusted caller input** — an optional SQL predicate added to the
        WHERE clause of the ``dumped`` CTE inside the INSERT.  The source
        table is aliased ``src`` in that context, e.g. ``src.dept = '63'``.
        When ``None``, no extra filter is applied and all source rows are
        processed.

    Note: keeping ``delete_predicate`` and ``insert_predicate`` separate
    avoids the alias mismatch that a single ``scope_predicate`` would cause
    (the DELETE alias ``frag`` is different from the INSERT alias ``src``).
    """

    source_schema: str
    source_table: str
    target_schema: str
    target_table: str
    geom_column: str = "geom"
    max_vertices: int = 256
    key_columns: tuple[str, ...] = ()
    data_columns: tuple[str, ...] = ()
    column_types: Mapping[str, str] = field(default_factory=dict)
    srid: int = 4326
    delete_predicate: str | None = None
    insert_predicate: str | None = None


# ---------------------------------------------------------------------------
# build_subdivide_sql — pure function
# ---------------------------------------------------------------------------


def build_subdivide_sql(spec: SubdivideSpec) -> str:  # noqa: C901  (acceptable complexity)
    """Return the full DDL+DML SQL block that materialises subdivided fragments.

    This is a **pure function**: it performs no I/O and can be tested without
    a live database connection.

    The generated block (in order):

    1. ``CREATE TABLE IF NOT EXISTS`` target with typed key+data columns,
       ``fragment_id bigint``, and ``geom geometry(Geometry, {srid})``.
       Primary key = ``(key_columns..., fragment_id)``.
    2. ``DELETE`` — scoped by ``delete_predicate`` if provided (alias ``frag``);
       otherwise a full-table delete (full-refresh semantics).
    3. Plain ``INSERT`` (no ON CONFLICT) — uses the ST_Dump → dimension-aware
       ST_Subdivide pipeline:
       * ``CROSS JOIN LATERAL (SELECT (ST_Dump(src.geom)).geom) c`` explodes
         multi-geometries.
       * ``CROSS JOIN LATERAL (passthrough dim=0 UNION ALL ST_Subdivide dim>0)``
         passes point geometries through unchanged and subdivides all surface
         and line geometries (``ST_Dimension > 0``).
       * ``row_number() OVER (PARTITION BY key_columns ORDER BY
         ST_Area(ST_Envelope) DESC, ST_AsEWKB)`` produces stable
         ``fragment_id`` values.
    4. ``CREATE INDEX IF NOT EXISTS`` GIST on ``geom``.
    5. ``CREATE INDEX IF NOT EXISTS`` btree on ``key_columns`` (if non-empty).

    Parameters
    ----------
    spec:
        Fully-populated :class:`SubdivideSpec`.

    Raises
    ------
    ValueError
        * Any identifier (schema/table/column) fails the ``^[a-z_][a-z0-9_]*$``
          regex.
        * A column in ``key_columns`` or ``data_columns`` is absent from
          ``spec.column_types``.
        * A SQL type string fails the type whitelist regex.
    """
    # --- validate identifiers -----------------------------------------------
    src = _qualified(spec.source_schema, spec.source_table)
    tgt = _qualified(spec.target_schema, spec.target_table)
    geom_col = _validate_ident(spec.geom_column, "geom_column")

    all_data_cols: list[str] = []
    for col in list(spec.key_columns) + list(spec.data_columns):
        _validate_ident(col, f"column:{col}")
        all_data_cols.append(col)

    # --- validate types ------------------------------------------------------
    for col in list(spec.key_columns) + list(spec.data_columns):
        if col not in spec.column_types:
            raise ValueError(
                f"Column {col!r} has no entry in column_types. "
                "Every key_column and data_column must have an explicit SQL type."
            )
        _validate_type(spec.column_types[col], col)

    max_vertices = int(spec.max_vertices)
    srid = int(spec.srid)

    # --- CREATE TABLE --------------------------------------------------------
    col_defs: list[str] = []
    for col in list(spec.key_columns) + list(spec.data_columns):
        col_defs.append(f"    {col} {spec.column_types[col]} NOT NULL")
    col_defs.append("    fragment_id bigint NOT NULL")
    col_defs.append(f"    geom geometry(Geometry, {srid}) NOT NULL")

    pk_cols = list(spec.key_columns) + ["fragment_id"]
    pk_clause = ", ".join(pk_cols)
    col_defs.append(f"    PRIMARY KEY ({pk_clause})")

    create_table = (
        f"CREATE TABLE IF NOT EXISTS {tgt} (\n"
        + ",\n".join(col_defs)
        + "\n);"
    )

    # --- DELETE (scope or full refresh) -------------------------------------
    if spec.delete_predicate:
        delete_sql = (
            f"DELETE FROM {tgt} frag\nWHERE {spec.delete_predicate};"
        )
    else:
        # Full-refresh: no delete_predicate → delete everything in the target.
        # Documented as intentional full-refresh semantics (see module docstring).
        delete_sql = f"DELETE FROM {tgt};"

    # --- INSERT --------------------------------------------------------------
    # Columns to select from the source in the dumped CTE (excluding geom):
    src_key_data_cols = list(spec.key_columns) + list(spec.data_columns)

    # dumped CTE — select key+data cols from src + ST_Dump + ST_Subdivide
    if src_key_data_cols:
        dumped_select_cols = ",\n        ".join(f"src.{c}" for c in src_key_data_cols)
        dumped_select = f"        {dumped_select_cols},\n        d.geom"
    else:
        dumped_select = "        d.geom"

    geom_not_null = (
        f"WHERE src.{geom_col} IS NOT NULL\n      AND NOT ST_IsEmpty(d.geom)"
    )
    if spec.insert_predicate:
        geom_not_null += f"\n      AND {spec.insert_predicate}"

    dumped_cte = f"""\
dumped AS (
    SELECT
{dumped_select}
    FROM {src} src
    CROSS JOIN LATERAL (
        SELECT (ST_Dump(src.{geom_col})).geom
    ) c
    CROSS JOIN LATERAL (
        SELECT c.geom
        WHERE ST_Dimension(c.geom) = 0
        UNION ALL
        SELECT subdivided.geom
        FROM ST_Subdivide(c.geom, {max_vertices}) AS subdivided(geom)
        WHERE ST_Dimension(c.geom) > 0
    ) d
    {geom_not_null}
)"""

    # numbered CTE — row_number partitioned by key_columns
    if spec.key_columns:
        partition_cols = ", ".join(spec.key_columns)
        numbered_select_cols = ", ".join(src_key_data_cols) + ",\n        " if src_key_data_cols else ""
        numbered_cte = f"""\
numbered AS (
    SELECT
        {numbered_select_cols}row_number() OVER (
            PARTITION BY {partition_cols}
            ORDER BY ST_Area(ST_Envelope(geom)) DESC, ST_AsEWKB(geom)
        ) AS fragment_id,
        geom
    FROM dumped
)"""
    else:
        # No key columns: partition is the whole table (degenerate case)
        numbered_cte = """\
numbered AS (
    SELECT
        row_number() OVER (
            ORDER BY ST_Area(ST_Envelope(geom)) DESC, ST_AsEWKB(geom)
        ) AS fragment_id,
        geom
    FROM dumped
)"""

    # INSERT target columns
    insert_cols = src_key_data_cols + ["fragment_id", "geom"]
    insert_col_list = ", ".join(insert_cols)

    # Plain INSERT — no ON CONFLICT clause.
    #
    # Rationale: the DELETE above is supposed to clear exactly the scope that
    # will be re-inserted.  If the DELETE predicate and the INSERT WHERE
    # diverge, ON CONFLICT DO UPDATE would silently overwrite the overlapping
    # fragments while leaving stale fragments alive (e.g. a feature that went
    # from 10 to 7 fragments would keep 3 ghost fragments → wrong spatial
    # joins, zero signal).  A PK violation is the honest signal of a
    # scope mismatch — matches the behaviour of the reference implementation.
    final_select = ", ".join(insert_cols)

    insert_sql = f"""\
INSERT INTO {tgt} (
    {insert_col_list}
)
WITH {dumped_cte},
{numbered_cte}
SELECT
    {final_select}
FROM numbered;"""

    # --- Indexes -------------------------------------------------------------
    tgt_table = _validate_ident(spec.target_table, "target_table")
    gist_index = (
        f"CREATE INDEX IF NOT EXISTS {tgt_table}_geom_gist\n"
        f"    ON {tgt} USING GIST (geom);"
    )

    indexes = [gist_index]
    if spec.key_columns:
        key_col_list = ", ".join(spec.key_columns)
        key_index = (
            f"CREATE INDEX IF NOT EXISTS {tgt_table}_key_idx\n"
            f"    ON {tgt} ({key_col_list});"
        )
        indexes.append(key_index)

    index_sql = "\n".join(indexes)

    return "\n\n".join([create_table, delete_sql, insert_sql, index_sql])


# ---------------------------------------------------------------------------
# subdivide_table — executes (or dry-runs) the generated SQL
# ---------------------------------------------------------------------------


def subdivide_table(
    conn: Any,
    spec: SubdivideSpec,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Materialise (or preview) a subdivided PostGIS table.

    Parameters
    ----------
    conn:
        A psycopg-compatible connection (``conn.cursor()`` / ``conn.commit()``
        / ``conn.rollback()``).
    spec:
        Fully-populated :class:`SubdivideSpec`.
    dry_run:
        When ``True`` (the default), the SQL is generated and returned but
        **never executed**.  Safe for inspection without touching the database.

    Returns
    -------
    dict
        * ``"sql"`` — the full SQL block (always present).
        * ``"executed"`` — ``False`` on dry-run, ``True`` on success.
        * ``"rows_inserted"`` — row count from the INSERT statement's
          ``cursor.rowcount``, or ``-1`` if unavailable (only on success).
    """
    sql = build_subdivide_sql(spec)
    if dry_run:
        return {"sql": sql, "executed": False}

    log.info(
        "subdivide_table.start",
        target=f"{spec.target_schema}.{spec.target_table}",
        source=f"{spec.source_schema}.{spec.source_table}",
        max_vertices=spec.max_vertices,
        dry_run=False,
    )

    cur = conn.cursor()
    rows_inserted = -1
    try:
        cur.execute(sql)
        # rowcount after a multi-statement block is implementation-defined;
        # document as -1 when unavailable.
        try:
            rows_inserted = cur.rowcount
        except Exception:  # noqa: BLE001
            rows_inserted = -1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    log.info(
        "subdivide_table.done",
        target=f"{spec.target_schema}.{spec.target_table}",
        rows_inserted=rows_inserted,
    )
    return {"sql": sql, "executed": True, "rows_inserted": rows_inserted}


# ---------------------------------------------------------------------------
# batch_load_duckdb_to_postgis
# ---------------------------------------------------------------------------


def batch_load_duckdb_to_postgis(
    duck_conn: Any,
    pg_conn: Any,
    *,
    select_sql: str,
    insert_sql: str,
    batch_size: int = 5_000,
    delete_sql: str | None = None,
    delete_params: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Stream rows from a DuckDB query into a PostGIS table in batches.

    Reproduces the core of ``load_duckdb_scope_to_postgis`` from
    gispulse-foncier as a parameterised primitive.

    The entire operation (optional delete + all inserts) is wrapped in a
    **single transaction**: only one ``commit`` is issued at the very end.
    Any exception triggers ``rollback`` before propagating.

    Trust boundary
    --------------
    ``select_sql``, ``insert_sql``, and ``delete_sql`` are **trusted caller
    input** — the caller is responsible for their correctness and safety.

    Parameters
    ----------
    duck_conn:
        An open DuckDB connection (``duck_conn.execute(sql)`` returning a
        cursor supporting ``fetchmany``).
    pg_conn:
        A psycopg-compatible connection.
    select_sql:
        DuckDB query whose result rows are streamed into PostGIS.  Must be
        non-empty.
    insert_sql:
        Parameterised INSERT statement with ``%s`` placeholders (psycopg
        style).  Must be non-empty.
    batch_size:
        Number of rows per ``executemany`` call.  Must be >= 1.
    delete_sql:
        Optional DELETE statement executed first (before any inserts) within
        the same transaction.
    delete_params:
        Optional sequence of parameters bound to ``delete_sql``.

    Returns
    -------
    dict
        * ``"rows_loaded"`` — total number of rows inserted.
        * ``"batches"`` — number of ``executemany`` calls issued.
        * ``"executed"`` — always ``True``.

    Raises
    ------
    ValueError
        ``batch_size < 1`` or ``select_sql``/``insert_sql`` are empty.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size!r}")
    if not select_sql or not select_sql.strip():
        raise ValueError("select_sql must not be empty")
    if not insert_sql or not insert_sql.strip():
        raise ValueError("insert_sql must not be empty")

    log.info(
        "batch_load_duckdb_to_postgis.start",
        batch_size=batch_size,
        has_delete=delete_sql is not None,
    )

    pg_cur = pg_conn.cursor()
    rows_loaded = 0
    batches = 0

    try:
        # --- optional scoped delete (same transaction) ----------------------
        if delete_sql is not None:
            if delete_params:
                pg_cur.execute(delete_sql, delete_params)
            else:
                pg_cur.execute(delete_sql)

        # --- stream DuckDB → PostGIS ----------------------------------------
        duck_cur = duck_conn.execute(select_sql)
        while True:
            rows = duck_cur.fetchmany(batch_size)
            if not rows:
                break
            pg_cur.executemany(insert_sql, rows)
            rows_loaded += len(rows)
            batches += 1

            if batches % 50 == 0:
                log.info(
                    "batch_load_duckdb_to_postgis.progress",
                    rows_loaded=rows_loaded,
                    batches=batches,
                )

        # --- single commit covering delete + all inserts --------------------
        pg_conn.commit()

    except Exception:
        pg_conn.rollback()
        raise
    finally:
        pg_cur.close()

    log.info(
        "batch_load_duckdb_to_postgis.done",
        rows_loaded=rows_loaded,
        batches=batches,
    )
    return {"rows_loaded": rows_loaded, "batches": batches, "executed": True}
