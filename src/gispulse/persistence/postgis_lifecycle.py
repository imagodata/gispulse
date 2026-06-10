"""PostGIS column lifecycle — manifest-gated column shed (dry-run by default).

Context
-------
After a verified export (PMTiles / GeoParquet) takes over serving, heavy geometry
columns in a PostGIS table become dead weight.  This module provides a
*generalised*, *agent-friendly* utility for shedding those columns safely.

Contract
--------
DROP COLUMN is table-wide in Postgres.  Before any column is removed the
following gates are checked **in order** — each failure raises
``PostgisLifecycleError`` with a machine-readable ``code`` and ``context``,
even when ``dry_run=True`` (a dry-run validates the full plan):

1. **invalid_identifier** — schema / table / column / partition_column names
   must match ``^[a-z_][a-z0-9_]*$`` (lowercase only).  PostgreSQL folds
   unquoted identifiers to lowercase; accepting mixed-case would let the
   ``information_schema`` gates check a different object than the ``ALTER``
   targets.  No SQL identifiers are interpolated without passing this check.
2. **proof_checks_missing** — ``proof.checks`` must not be empty.
3. **proof_checks_failed** — every entry in ``proof.checks`` must be ``True``.
4. **proof_partition_values_missing** — ``proof.partition_values`` must not be
   empty.  A proof with no explicit scope is **rejected**, never interpreted as
   "covers the whole table".
5. **requested_not_covered** — every value in ``partition_values`` must appear
   in ``proof.partition_values``.
6. **table_not_covered** — DROP COLUMN is table-wide, so every *live* value of
   ``partition_column`` (queried from the DB) must be covered by
   ``proof.partition_values``.
6b.**partition_nulls_present** — if any row has ``partition_column IS NULL``,
   those rows are also affected by the table-wide DROP but are invisible to
   every partition-scoped gate → rejected explicitly.
7. **row_count_mismatch** — live ``COUNT(*)`` for the covered partition values
   must equal ``proof.row_count``.

After all gates pass, columns are partitioned into *to_drop* (present) and
*absent* (already gone).  If ``columns_to_drop`` is empty no DDL is emitted
and ``vacuum_full`` defaults to ``False`` in the report.

``dry_run=True`` (the default — safe default per AX principle 2) returns a
fully-populated ``ShedReport`` without writing anything.

``dry_run=False`` executes ``pre_drop_sql``, one multi-clause ``ALTER TABLE``
(single DDL statement), commits, then optionally ``VACUUM (FULL, ANALYZE)``
via the autocommit-bascule pattern required by Postgres.

This is NOT a transform capability — the operation is destructive and
non-replayable; callers must keep the proof manifest for audit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import structlog

logger = structlog.get_logger(__name__)

# Lowercase-only: PostgreSQL folds unquoted identifiers to lowercase at parse time,
# while information_schema stores names in their original (folded) case.
# Accepting mixed-case here would let the gates check a different object than the
# ALTER targets → security gap.  Callers must quote and pass the already-folded name.
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class PostgisLifecycleError(RuntimeError):
    """Machine-readable lifecycle gate failure.

    Attributes
    ----------
    code:
        Snake_case error code (parseable by agents/CI).
    context:
        Structured detail dict (never prose-only).
    """

    def __init__(self, code: str, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.context: dict[str, Any] = context or {}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnShedProof:
    """Caller-supplied evidence that the export has been verified.

    ``partition_values`` must be explicitly non-empty — the module rejects an
    empty tuple rather than treating it as "covers everything".
    """

    partition_values: tuple[str, ...]
    row_count: int
    checks: Mapping[str, bool]


@dataclass(frozen=True)
class ShedReport:
    """Structured output of :func:`shed_columns`."""

    schema: str
    table: str
    dry_run: bool
    gates_passed: tuple[str, ...]
    columns_to_drop: tuple[str, ...]
    columns_absent: tuple[str, ...]
    pre_drop_statements: int
    vacuum_full: bool
    executed: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "schema": self.schema,
            "table": self.table,
            "dry_run": self.dry_run,
            "gates_passed": list(self.gates_passed),
            "columns_to_drop": list(self.columns_to_drop),
            "columns_absent": list(self.columns_absent),
            "pre_drop_statements": self.pre_drop_statements,
            "vacuum_full": self.vacuum_full,
            "executed": self.executed,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise PostgisLifecycleError(
            "invalid_identifier",
            f"Identifier {name!r} is invalid: identifiers must be lowercase "
            f"(unquoted PostgreSQL semantics) and match ^[a-z_][a-z0-9_]*$.",
            {"identifier": name},
        )


def _drop_expression_unsupported(exc: BaseException) -> bool:
    """Return True only when *exc* signals that DROP EXPRESSION is not supported by
    this Postgres version/build — triggering the safe column-recreate fallback.

    Decision table:
    - pgcode ``0A000`` (feature_not_supported) → always unsupported.
    - pgcode ``42601`` (syntax_error) → unsupported **only if** the message also
      contains the word "expression" (the token that tripped the parser).
      A generic syntax error (e.g. a bug in caller SQL) must **not** silently
      chain into the destructive recreate path.
    - No pgcode, message heuristic → "drop expression" + "syntax" or "not supported".
    """
    pgcode = getattr(exc, "pgcode", None)
    if pgcode == "0A000":
        return True
    message = str(exc).lower()
    if pgcode == "42601":
        return "expression" in message
    return "drop expression" in message and ("syntax" in message or "not supported" in message)


def _existing_columns(conn: Any, schema: str, table: str, columns: Sequence[str]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name   = %s
              AND column_name  = ANY(%s::text[])
            """,
            (schema, table, list(columns)),
        )
        return {row[0] for row in cur.fetchall()}


def _live_partition_values(conn: Any, schema: str, table: str, partition_column: str) -> set[str]:
    sql = (
        f"SELECT DISTINCT {partition_column}"
        f" FROM {schema}.{table}"
        f" WHERE {partition_column} IS NOT NULL"
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        return {row[0] for row in cur.fetchall()}


def _count_rows(conn: Any, schema: str, table: str, partition_column: str, values: Sequence[str]) -> int:
    sql = (
        f"SELECT count(*)"
        f" FROM {schema}.{table}"
        f" WHERE {partition_column} = ANY(%s::text[])"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (list(values),))
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _vacuum_full(conn: Any, schema: str, table: str) -> None:
    conn.commit()
    old_autocommit = getattr(conn, "autocommit", False)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f"VACUUM (FULL, ANALYZE) {schema}.{table}")
    finally:
        conn.autocommit = old_autocommit


def _connect_psycopg2(dsn: str) -> Any:
    """Thin wrapper so tests can monkeypatch this symbol."""
    try:
        import psycopg2  # type: ignore[import]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "psycopg2 is required; install with `pip install gispulse[postgis]`"
        ) from exc
    return psycopg2.connect(dsn)


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------


def shed_columns(
    conn: Any,
    *,
    schema: str,
    table: str,
    columns: Sequence[str],
    partition_column: str,
    partition_values: Sequence[str],
    proof: ColumnShedProof,
    dry_run: bool = True,
    vacuum_full: bool = True,
    pre_drop_sql: Sequence[str] = (),
) -> ShedReport:
    """Shed heavy columns from a PostGIS table after a verified export.

    Parameters
    ----------
    conn:
        DB-API 2 connection (psycopg2-style: ``cursor()`` context manager,
        ``execute``, ``fetchone``/``fetchall``, ``commit``/``rollback``,
        ``autocommit`` attribute).
    schema, table:
        Target table.  Both must be lowercase unquoted identifiers
        (``^[a-z_][a-z0-9_]*$``).
    columns:
        Column names to shed (all must be lowercase).  Columns already absent
        are reported but not treated as errors.
    partition_column:
        Column used for scope checks (e.g. ``dept``).  Must be lowercase.
    partition_values:
        The partition values that *this call* is responsible for.  Must be a
        subset of ``proof.partition_values``.
    proof:
        Caller-supplied evidence that export is verified.
    dry_run:
        When ``True`` (default) validates all gates and returns the plan without
        executing any DDL.
    vacuum_full:
        When ``True`` and columns were dropped, run ``VACUUM (FULL, ANALYZE)``
        after the drop.
    pre_drop_sql:
        SQL statements to execute before the ALTER (e.g. materialise a
        generated column that depends on the columns being dropped).
    """
    gates_passed: list[str] = []

    # --- Gate 1: identifier safety -----------------------------------------
    _validate_identifier(schema)
    _validate_identifier(table)
    _validate_identifier(partition_column)
    for col in columns:
        _validate_identifier(col)
    gates_passed.append("identifiers")

    # --- Gate 2: proof checks present --------------------------------------
    if not proof.checks:
        raise PostgisLifecycleError(
            "proof_checks_missing",
            "proof.checks is empty; export may be incomplete.",
        )
    gates_passed.append("proof_checks_present")

    # --- Gate 2b: all checks passed ----------------------------------------
    failed_checks = [name for name, ok in proof.checks.items() if not ok]
    if failed_checks:
        raise PostgisLifecycleError(
            "proof_checks_failed",
            f"Proof checks failed: {failed_checks}",
            {"failed_checks": failed_checks},
        )
    gates_passed.append("proof_checks_passed")

    # --- Gate 3: proof.partition_values non-empty --------------------------
    if not proof.partition_values:
        raise PostgisLifecycleError(
            "proof_partition_values_missing",
            "proof.partition_values is empty. "
            "An undeclared scope is rejected to prevent accidental full-table drops.",
        )
    gates_passed.append("proof_partition_values_present")

    # --- Gate 4: requested ⊆ proof -----------------------------------------
    proof_set = set(proof.partition_values)
    requested_set = set(partition_values)
    not_covered = sorted(requested_set - proof_set)
    if not_covered:
        raise PostgisLifecycleError(
            "requested_not_covered",
            f"Partition values not covered by proof: {not_covered}",
            {"missing": not_covered},
        )
    gates_passed.append("requested_covered")

    # --- Gate 5: table-wide coverage ---------------------------------------
    live_values = _live_partition_values(conn, schema, table, partition_column)
    table_missing = sorted(live_values - proof_set)
    if table_missing:
        raise PostgisLifecycleError(
            "table_not_covered",
            f"Live partition values not covered by proof: {table_missing}. "
            "DROP COLUMN is table-wide; all live values must be proven.",
            {"missing": table_missing},
        )
    gates_passed.append("table_covered")

    # --- Gate 5b: no NULL partition values ------------------------------------
    # Rows where partition_column IS NULL are also affected by the table-wide
    # DROP COLUMN but are invisible to every partition-scoped gate above.
    null_sql = (
        f"SELECT EXISTS("
        f"SELECT 1 FROM {schema}.{table} WHERE {partition_column} IS NULL)"
    )
    with conn.cursor() as cur:
        cur.execute(null_sql)
        row = cur.fetchone()
    has_nulls = bool(row[0]) if row is not None else False
    if has_nulls:
        raise PostgisLifecycleError(
            "partition_nulls_present",
            f"Table {schema}.{table} has rows where {partition_column} IS NULL. "
            "DROP COLUMN is table-wide; NULL-partition rows must be cleaned up first.",
            {"partition_column": partition_column},
        )
    gates_passed.append("no_partition_nulls")

    # --- Gate 6: row count --------------------------------------------------
    actual = _count_rows(conn, schema, table, partition_column, list(proof.partition_values))
    if actual != proof.row_count:
        raise PostgisLifecycleError(
            "row_count_mismatch",
            f"Row count mismatch: expected={proof.row_count}, actual={actual}.",
            {"expected": proof.row_count, "actual": actual},
        )
    gates_passed.append("row_count")

    # --- Classify columns ---------------------------------------------------
    existing = _existing_columns(conn, schema, table, columns)
    columns_to_drop = tuple(c for c in columns if c in existing)
    columns_absent = tuple(c for c in columns if c not in existing)

    # Nothing to drop — return immediately, no VACUUM
    if not columns_to_drop:
        logger.info(
            "postgis_lifecycle.shed_columns.noop",
            schema=schema, table=table, columns_absent=list(columns_absent),
        )
        return ShedReport(
            schema=schema,
            table=table,
            dry_run=dry_run,
            gates_passed=tuple(gates_passed),
            columns_to_drop=(),
            columns_absent=columns_absent,
            pre_drop_statements=len(pre_drop_sql),
            vacuum_full=False,
            executed=False,
        )

    # dry_run: return the plan without writing
    if dry_run:
        logger.info(
            "postgis_lifecycle.shed_columns.dry_run",
            schema=schema, table=table,
            columns_to_drop=list(columns_to_drop),
            columns_absent=list(columns_absent),
        )
        return ShedReport(
            schema=schema,
            table=table,
            dry_run=True,
            gates_passed=tuple(gates_passed),
            columns_to_drop=columns_to_drop,
            columns_absent=columns_absent,
            pre_drop_statements=len(pre_drop_sql),
            vacuum_full=vacuum_full,
            executed=False,
        )

    # --- Execute -----------------------------------------------------------
    try:
        # pre_drop_sql (e.g. materialise generated columns)
        with conn.cursor() as cur:
            for stmt in pre_drop_sql:
                cur.execute(stmt)

        # Single multi-clause ALTER
        drop_clauses = ",\n    ".join(
            f"DROP COLUMN IF EXISTS {col}" for col in columns_to_drop
        )
        alter_sql = f"ALTER TABLE {schema}.{table}\n    {drop_clauses}"
        with conn.cursor() as cur:
            cur.execute(alter_sql)

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # Optional VACUUM (requires autocommit — exclusive lock)
    do_vacuum = vacuum_full
    if do_vacuum:
        _vacuum_full(conn, schema, table)

    logger.info(
        "postgis_lifecycle.shed_columns.done",
        schema=schema, table=table,
        columns_dropped=list(columns_to_drop),
        vacuum_full=do_vacuum,
    )
    return ShedReport(
        schema=schema,
        table=table,
        dry_run=False,
        gates_passed=tuple(gates_passed),
        columns_to_drop=columns_to_drop,
        columns_absent=columns_absent,
        pre_drop_statements=len(pre_drop_sql),
        vacuum_full=do_vacuum,
        executed=True,
    )


# ---------------------------------------------------------------------------
# materialize_generated_column
# ---------------------------------------------------------------------------


def _is_generated_column(conn: Any, schema: str, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_generated
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name   = %s
              AND column_name  = %s
            """,
            (schema, table, column),
        )
        row = cur.fetchone()
    return bool(row and str(row[0]).upper() == "ALWAYS")


def _column_exists_single(conn: Any, schema: str, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name   = %s
              AND column_name  = %s
            """,
            (schema, table, column),
        )
        return cur.fetchone() is not None


def _get_column_type(conn: Any, schema: str, table: str, column: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relname = %s
              AND a.attname = %s
              AND a.attnum  > 0
            """,
            (schema, table, column),
        )
        row = cur.fetchone()
    return str(row[0]) if row else "text"


def _get_indexes_for_column(conn: Any, schema: str, table: str, column: str) -> list[str]:
    """Return indexdef strings for every index that references *column*.

    Two branches are unioned:
    1. Regular indexes: catalogue join on pg_attribute/pg_index (exact attname match,
       no substring false-positives, no ``_`` wildcard issues from LIKE).
    2. Expression indexes (indkey contains 0): pg_indexes filtered by a word-boundary
       regex so ``centroid`` does not match ``centroid_shed`` etc.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_indexdef(i.indexrelid)
            FROM pg_index     i
            JOIN pg_class     t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_attribute a ON a.attrelid = t.oid
                               AND a.attnum   = ANY(i.indkey)
            WHERE n.nspname = %s
              AND t.relname = %s
              AND a.attname = %s
            UNION
            SELECT ix.indexdef
            FROM pg_indexes ix
            JOIN pg_class   t ON t.relname = ix.tablename
            JOIN pg_namespace n ON n.oid = t.relnamespace
                               AND n.nspname = ix.schemaname
            JOIN pg_index  i ON i.indrelid = t.oid
            WHERE ix.schemaname = %s
              AND ix.tablename  = %s
              AND 0             = ANY(i.indkey)
              AND ix.indexdef   ~ ('\\m' || %s || '\\M')
            """,
            (schema, table, column, schema, table, column),
        )
        return [row[0] for row in cur.fetchall()]


def materialize_generated_column(
    conn: Any,
    *,
    schema: str,
    table: str,
    column: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Convert a ``GENERATED ALWAYS`` column into a plain stored column.

    This is a companion to :func:`shed_columns` used to materialise a
    computed column (e.g. centroid) before dropping the source columns it
    was generated from.

    Returns a dict with at least ``{"action": "<action>"}`` where action is:

    * ``"noop"``           — column not generated, nothing to do.
    * ``"drop_expression"``— ``ALTER COLUMN … DROP EXPRESSION`` succeeded.
    * ``"recreate"``       — fallback: tmp column → copy → drop → rename.

    All of ``schema``, ``table``, and ``column`` must be lowercase unquoted
    identifiers (``^[a-z_][a-z0-9_]*$``).

    Raises ``PostgisLifecycleError(code="column_absent")`` if the column does
    not exist.
    """
    _validate_identifier(schema)
    _validate_identifier(table)
    _validate_identifier(column)

    if not _column_exists_single(conn, schema, table, column):
        raise PostgisLifecycleError(
            "column_absent",
            f"Column {schema}.{table}.{column} does not exist.",
            {"schema": schema, "table": table, "column": column},
        )

    if not _is_generated_column(conn, schema, table, column):
        return {"action": "noop", "schema": schema, "table": table, "column": column}

    # Determine fallback plan before dry_run gate so the plan is always known
    indexes = _get_indexes_for_column(conn, schema, table, column)
    col_type = _get_column_type(conn, schema, table, column)
    tmp_col = f"{column}_shed"

    if dry_run:
        # Optimistically assume DROP EXPRESSION will work
        return {
            "action": "drop_expression",
            "schema": schema,
            "table": table,
            "column": column,
            "indexes_to_recreate": indexes,
            "fallback_col_type": col_type,
        }

    # Try DROP EXPRESSION first
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"ALTER TABLE {schema}.{table} ALTER COLUMN {column} DROP EXPRESSION"
            )
        conn.commit()
        logger.info(
            "postgis_lifecycle.materialize.drop_expression",
            schema=schema, table=table, column=column,
        )
        return {"action": "drop_expression", "schema": schema, "table": table, "column": column}
    except Exception as exc:
        conn.rollback()
        if not _drop_expression_unsupported(exc):
            raise

    # Fallback: recreate column — entire swap + index recreation in ONE transaction.
    # CREATE INDEX (non-CONCURRENT) is transactional in Postgres; a single commit
    # at the end ensures no partial state survives a mid-sequence failure.
    logger.info(
        "postgis_lifecycle.materialize.recreate",
        schema=schema, table=table, column=column,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"ALTER TABLE {schema}.{table} ADD COLUMN IF NOT EXISTS {tmp_col} {col_type}"
            )
            cur.execute(
                f"UPDATE {schema}.{table}"
                f" SET {tmp_col} = {column}"
                f" WHERE {tmp_col} IS DISTINCT FROM {column}"
            )
            cur.execute(f"ALTER TABLE {schema}.{table} DROP COLUMN {column}")
            cur.execute(
                f"ALTER TABLE {schema}.{table} RENAME COLUMN {tmp_col} TO {column}"
            )
            for idx_sql in indexes:
                cur.execute(idx_sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "action": "recreate",
        "schema": schema,
        "table": table,
        "column": column,
        "indexes_recreated": indexes,
    }
