"""Tests for :mod:`gispulse.persistence.postgis_lifecycle`.

TDD approach — tests written before implementation.  The fake DB-API
connection is fully scripted (no real Postgres required).

Coverage contract (all must pass):
  - Each gate raises PostgisLifecycleError with the expected *code*
  - The proof-without-partition_values hardening (rejected, not "covers all")
  - dry_run=True (default): zero mutating SQL statements pass through
  - apply nominal path: pre_drop_sql → ALTER multi-clause → commit → VACUUM
  - columns already absent: no VACUUM issued, executed=False
  - rollback on exception during DROP
  - injection attempt on identifier is caught as invalid_identifier
  - materialize_generated_column: DROP EXPRESSION path, fallback recreate
    (with index re-creation), no-op for non-generated column, dry_run plan
  - CLI: CliRunner smoke test (or direct function test with monkeypatched conn)
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake DB-API connection
# ---------------------------------------------------------------------------


class FakeCursor:
    """Scriptable cursor that records every SQL+params pair.

    Responses are dispatched by a callable ``(sql, params) -> (fetchone, fetchall) | None``.
    The first responder that returns non-None wins.
    """

    def __init__(self, responders: list) -> None:
        self._responders = responders
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql, params))

    def _resolve(self) -> tuple[Any, list]:
        if not self.executed:
            return None, []
        sql, params = self.executed[-1]
        for fn in self._responders:
            result = fn(sql, params)
            if result is not None:
                return result
        return None, []

    def fetchone(self) -> Any:
        return self._resolve()[0]

    def fetchall(self) -> list:
        return self._resolve()[1]

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class FakeConn:
    """Scriptable DB-API connection that records mutations."""

    def __init__(self, responders: list) -> None:
        self._responses = responders  # kept as _responses for subclass compat
        self.committed = 0
        self.rolled_back = 0
        self.autocommit = False
        self._cursors: list[FakeCursor] = []

    @contextmanager
    def cursor(self):  # type: ignore[override]
        cur = FakeCursor(self._responses)
        self._cursors.append(cur)
        yield cur

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def all_sql(self) -> list[str]:
        return [sql for cur in self._cursors for sql, _ in cur.executed]

    def mutating_sql(self) -> list[str]:
        keywords = ("alter", "drop", "vacuum", "update", "insert", "delete", "create")
        return [
            sql for sql in self.all_sql()
            if any(sql.strip().lower().startswith(k) for k in keywords)
        ]


def _make_conn(
    *,
    live_partitions: list[str] | None = None,
    row_count: int = 100,
    existing_columns: list[str] | None = None,
    is_generated: bool = True,
    col_type: str = "geometry(Point,4326)",
    indexes: list[str] | None = None,
    has_nulls: bool = False,
) -> FakeConn:
    """Build a FakeConn with canned responses for the lifecycle module queries.

    Responders are callables ``(sql, params) -> (fetchone_result, fetchall_result) | None``.
    ``has_nulls`` controls the EXISTS(... IS NULL) gate response.
    """
    _live = live_partitions if live_partitions is not None else ["63"]
    _cols = existing_columns if existing_columns is not None else ["geom_wkb_hex", "geom_z0_wkb_hex"]
    _indexes = indexes or []

    def _resp_distinct(sql: str, params: tuple) -> tuple | None:
        if re.search(r"SELECT\s+DISTINCT", sql, re.IGNORECASE):
            rows = [(v,) for v in _live]
            return (rows[0] if rows else None), rows
        return None

    def _resp_count(sql: str, params: tuple) -> tuple | None:
        if re.search(r"count\(\*\)", sql, re.IGNORECASE):
            return (row_count,), [(row_count,)]
        return None

    def _resp_nulls_exists(sql: str, params: tuple) -> tuple | None:
        # SELECT EXISTS(SELECT 1 ... WHERE partition_column IS NULL)
        if re.search(r"EXISTS", sql, re.IGNORECASE) and re.search(r"IS NULL", sql, re.IGNORECASE):
            val = True if has_nulls else False
            return (val,), [(val,)]
        return None

    def _resp_is_generated(sql: str, params: tuple) -> tuple | None:
        if re.search(r"is_generated", sql, re.IGNORECASE):
            val = "ALWAYS" if is_generated else "NEVER"
            return (val,), [(val,)]
        return None

    def _resp_col_exists_single(sql: str, params: tuple) -> tuple | None:
        # SELECT 1 FROM information_schema.columns WHERE ... AND column_name = %s
        if re.search(r"SELECT\s+1\s+FROM\s+information_schema", sql, re.IGNORECASE):
            # params[-1] is the column name being checked
            col_name = params[-1] if params else None
            if col_name in _cols:
                return (1,), [(1,)]
            return None, []
        return None

    def _resp_col_list(sql: str, params: tuple) -> tuple | None:
        # SELECT column_name FROM information_schema.columns WHERE ... AND column_name = ANY(...)
        if re.search(r"SELECT\s+column_name\s+FROM\s+information_schema", sql, re.IGNORECASE):
            # params[-1] is a list of requested columns; return intersection with existing
            requested = params[-1] if params else []
            found = [c for c in _cols if c in (requested if isinstance(requested, list) else [])]
            rows = [(c,) for c in found]
            return (rows[0] if rows else None), rows
        return None

    def _resp_pg_indexes(sql: str, params: tuple) -> tuple | None:
        # Matches both the catalogue-join query (pg_index/pg_attribute) and
        # the expression-index UNION branch (pg_indexes regex filter).
        if re.search(r"pg_index\b|pg_indexes\b", sql, re.IGNORECASE):
            rows = [(idx,) for idx in _indexes]
            return (rows[0] if rows else None), rows
        return None

    def _resp_format_type(sql: str, params: tuple) -> tuple | None:
        if re.search(r"format_type|atttypid", sql, re.IGNORECASE):
            return (col_type,), [(col_type,)]
        return None

    responders = [
        _resp_distinct,
        _resp_count,
        _resp_nulls_exists,
        _resp_is_generated,
        _resp_col_exists_single,
        _resp_col_list,
        _resp_pg_indexes,
        _resp_format_type,
    ]
    return FakeConn(responders)


# ---------------------------------------------------------------------------
# Imports (will fail RED until module exists)
# ---------------------------------------------------------------------------

from gispulse.persistence.postgis_lifecycle import (  # noqa: E402
    ColumnShedProof,
    PostgisLifecycleError,
    materialize_generated_column,
    shed_columns,
)


# ---------------------------------------------------------------------------
# Helper: valid proof
# ---------------------------------------------------------------------------

def _valid_proof(
    partition_values: tuple[str, ...] = ("63",),
    row_count: int = 100,
    checks: dict[str, bool] | None = None,
) -> ColumnShedProof:
    return ColumnShedProof(
        partition_values=partition_values,
        row_count=row_count,
        checks=checks if checks is not None else {"parquet_written": True, "layer_count_ok": True},
    )


# ---------------------------------------------------------------------------
# Gate 1 — invalid_identifier
# ---------------------------------------------------------------------------


def test_invalid_identifier_schema_raises() -> None:
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="bad schema",  # space
            table="mytable",
            columns=["geom"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(),
        )
    assert exc_info.value.code == "invalid_identifier"


def test_invalid_identifier_sql_injection_raises() -> None:
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="dept; DROP TABLE x",
            columns=["geom"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(),
        )
    assert exc_info.value.code == "invalid_identifier"


def test_invalid_identifier_column_raises() -> None:
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom", "1bad_col"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(),
        )
    assert exc_info.value.code == "invalid_identifier"


def test_invalid_identifier_partition_column_raises() -> None:
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom"],
            partition_column="dept; --",
            partition_values=["63"],
            proof=_valid_proof(),
        )
    assert exc_info.value.code == "invalid_identifier"


def test_invalid_identifier_mixedcase_raises() -> None:
    """MixedCase identifiers are rejected: PostgreSQL folds unquoted names to lowercase
    while information_schema comparisons are case-sensitive → gates would check a
    different object than ALTER targets."""
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="Public",  # capital P
            table="mytable",
            columns=["geom_wkb_hex"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(),
        )
    err = exc_info.value
    assert err.code == "invalid_identifier"
    assert "lowercase" in str(err).lower()


def test_invalid_identifier_mixedcase_column_raises() -> None:
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom_Wkb_hex"],  # capital W
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(),
        )
    assert exc_info.value.code == "invalid_identifier"


def test_invalid_identifier_mixedcase_materialize_raises() -> None:
    conn = _make_conn(existing_columns=["centroid"])
    with pytest.raises(PostgisLifecycleError) as exc_info:
        materialize_generated_column(
            conn,
            schema="public",
            table="myTable",  # capital T
            column="centroid",
        )
    assert exc_info.value.code == "invalid_identifier"


# ---------------------------------------------------------------------------
# Gate 2 — proof_checks_missing and proof_checks_failed
# ---------------------------------------------------------------------------


def test_proof_checks_missing_raises() -> None:
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(checks={}),
        )
    assert exc_info.value.code == "proof_checks_missing"


def test_proof_checks_failed_raises() -> None:
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(checks={"parquet_written": True, "layer_count_ok": False}),
        )
    err = exc_info.value
    assert err.code == "proof_checks_failed"
    assert "layer_count_ok" in err.context["failed_checks"]


# ---------------------------------------------------------------------------
# Gate 3 — proof_partition_values_missing (hardening: empty proof rejected)
# ---------------------------------------------------------------------------


def test_proof_partition_values_empty_raises() -> None:
    """A proof with no partition_values must be REJECTED, never treated as 'covers all'."""
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom"],
            partition_column="dept",
            partition_values=["63"],
            proof=ColumnShedProof(
                partition_values=(),
                row_count=100,
                checks={"ok": True},
            ),
        )
    assert exc_info.value.code == "proof_partition_values_missing"


# ---------------------------------------------------------------------------
# Gate 4 — requested_not_covered
# ---------------------------------------------------------------------------


def test_requested_not_covered_raises() -> None:
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom"],
            partition_column="dept",
            partition_values=["63", "75"],  # 75 not in proof
            proof=_valid_proof(partition_values=("63",)),
        )
    err = exc_info.value
    assert err.code == "requested_not_covered"
    assert "75" in err.context["missing"]


# ---------------------------------------------------------------------------
# Gate 5 — table_not_covered (live partition outside proof)
# ---------------------------------------------------------------------------


def test_table_not_covered_raises() -> None:
    # DB reports dept 44 live but proof only covers 63
    conn = _make_conn(live_partitions=["63", "44"])
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(partition_values=("63",)),
        )
    err = exc_info.value
    assert err.code == "table_not_covered"
    assert "44" in err.context["missing"]


# ---------------------------------------------------------------------------
# Gate 5b — partition_nulls_present
# ---------------------------------------------------------------------------


def test_partition_nulls_present_raises() -> None:
    """If any row has partition_column IS NULL, DROP COLUMN would affect those rows
    too but no gate currently covers them → must be explicitly blocked."""
    conn = _make_conn(live_partitions=["63"], has_nulls=True)
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom_wkb_hex"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(),
        )
    err = exc_info.value
    assert err.code == "partition_nulls_present"
    assert err.context["partition_column"] == "dept"


def test_partition_nulls_absent_passes_gate() -> None:
    """When no NULLs exist, the gate must pass and appear in gates_passed."""
    conn = _make_conn(live_partitions=["63"], has_nulls=False)
    report = shed_columns(
        conn,
        schema="public",
        table="mytable",
        columns=["geom_wkb_hex"],
        partition_column="dept",
        partition_values=["63"],
        proof=_valid_proof(),
    )
    assert "no_partition_nulls" in report.gates_passed


# ---------------------------------------------------------------------------
# Gate 6 — row_count_mismatch
# ---------------------------------------------------------------------------


def test_row_count_mismatch_raises() -> None:
    conn = _make_conn(live_partitions=["63"], row_count=999)
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(row_count=100),  # 100 != 999
        )
    err = exc_info.value
    assert err.code == "row_count_mismatch"
    assert err.context["expected"] == 100
    assert err.context["actual"] == 999


# ---------------------------------------------------------------------------
# dry_run=True (default) — zero mutating SQL
# ---------------------------------------------------------------------------


def test_dry_run_default_no_mutating_sql() -> None:
    conn = _make_conn()
    report = shed_columns(
        conn,
        schema="public",
        table="mytable",
        columns=["geom_wkb_hex"],
        partition_column="dept",
        partition_values=["63"],
        proof=_valid_proof(),
    )
    assert report.dry_run is True
    assert report.executed is False
    assert conn.mutating_sql() == [], f"Unexpected mutations: {conn.mutating_sql()}"


def test_dry_run_explicit_false_is_apply() -> None:
    """Sanity: passing dry_run=False does execute (tested more fully in nominal path)."""
    conn = _make_conn()
    report = shed_columns(
        conn,
        schema="public",
        table="mytable",
        columns=["geom_wkb_hex"],
        partition_column="dept",
        partition_values=["63"],
        proof=_valid_proof(),
        dry_run=False,
    )
    assert report.executed is True


# ---------------------------------------------------------------------------
# dry_run raises gates even without executing
# ---------------------------------------------------------------------------


def test_dry_run_still_validates_gates() -> None:
    """A dry-run on invalid identifiers must still raise (plan validation)."""
    conn = _make_conn()
    with pytest.raises(PostgisLifecycleError) as exc_info:
        shed_columns(
            conn,
            schema="bad schema",
            table="mytable",
            columns=["geom"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(),
            dry_run=True,
        )
    assert exc_info.value.code == "invalid_identifier"


# ---------------------------------------------------------------------------
# apply — nominal path: pre_drop_sql → ALTER multi-clause → commit → VACUUM
# ---------------------------------------------------------------------------


def test_apply_nominal_order() -> None:
    """Verify execution order: pre_drop_sql first, then ALTER multi-clause, commit, VACUUM."""
    conn = _make_conn(existing_columns=["geom_wkb_hex", "geom_z0_wkb_hex"])
    pre_sql = ["UPDATE mytable SET foo = 1"]
    report = shed_columns(
        conn,
        schema="public",
        table="mytable",
        columns=["geom_wkb_hex", "geom_z0_wkb_hex"],
        partition_column="dept",
        partition_values=["63"],
        proof=_valid_proof(),
        dry_run=False,
        vacuum_full=True,
        pre_drop_sql=pre_sql,
    )
    all_sql = conn.all_sql()
    # pre_drop_sql must appear before ALTER
    pre_index = next(i for i, s in enumerate(all_sql) if "UPDATE" in s.upper())
    alter_index = next(i for i, s in enumerate(all_sql) if s.strip().upper().startswith("ALTER"))
    assert pre_index < alter_index, "pre_drop_sql must execute before ALTER"

    # ALTER must be a single multi-clause statement (both columns in one ALTER)
    alter_stmts = [s for s in all_sql if s.strip().upper().startswith("ALTER")]
    assert len(alter_stmts) == 1, f"Expected one ALTER statement, got {alter_stmts}"
    assert "geom_wkb_hex" in alter_stmts[0]
    assert "geom_z0_wkb_hex" in alter_stmts[0]

    # commit must happen
    assert conn.committed >= 1

    # VACUUM issued (autocommit path)
    vacuum_stmts = [s for s in all_sql if "VACUUM" in s.upper()]
    assert len(vacuum_stmts) == 1

    assert report.executed is True
    assert report.vacuum_full is True
    assert set(report.columns_to_drop) == {"geom_wkb_hex", "geom_z0_wkb_hex"}


def test_apply_vacuum_autocommit_save_restore() -> None:
    """autocommit is restored to its original value after VACUUM."""
    conn = _make_conn()
    conn.autocommit = False
    shed_columns(
        conn,
        schema="public",
        table="mytable",
        columns=["geom_wkb_hex"],
        partition_column="dept",
        partition_values=["63"],
        proof=_valid_proof(),
        dry_run=False,
        vacuum_full=True,
    )
    assert conn.autocommit is False, "autocommit must be restored after VACUUM"


# ---------------------------------------------------------------------------
# columns already absent — no VACUUM, executed=False
# ---------------------------------------------------------------------------


def test_columns_absent_no_vacuum() -> None:
    # DB reports no existing columns matching our request
    conn = _make_conn(existing_columns=["other_col"])
    report = shed_columns(
        conn,
        schema="public",
        table="mytable",
        columns=["geom_wkb_hex"],
        partition_column="dept",
        partition_values=["63"],
        proof=_valid_proof(),
        dry_run=False,
        vacuum_full=True,
    )
    assert report.columns_to_drop == ()
    assert report.columns_absent == ("geom_wkb_hex",)
    assert report.vacuum_full is False
    assert report.executed is False
    vacuum_stmts = [s for s in conn.all_sql() if "VACUUM" in s.upper()]
    assert vacuum_stmts == []


# ---------------------------------------------------------------------------
# rollback on exception during DROP
# ---------------------------------------------------------------------------


class _DropFails(FakeConn):
    """Raises on ALTER TABLE."""

    def cursor(self):  # type: ignore[override]
        parent = self

        @contextmanager
        def _ctx():
            cur = _RaisingCursor(parent._responses)
            parent._cursors.append(cur)
            yield cur

        return _ctx()


class _RaisingCursor(FakeCursor):
    def execute(self, sql: str, params: tuple = ()) -> None:
        super().execute(sql, params)
        if sql.strip().upper().startswith("ALTER"):
            raise RuntimeError("simulated PG error")


def test_rollback_on_drop_exception() -> None:
    base = _make_conn()
    conn = _DropFails(base._responses)
    with pytest.raises(RuntimeError, match="simulated PG error"):
        shed_columns(
            conn,
            schema="public",
            table="mytable",
            columns=["geom_wkb_hex"],
            partition_column="dept",
            partition_values=["63"],
            proof=_valid_proof(),
            dry_run=False,
        )
    assert conn.rolled_back >= 1


# ---------------------------------------------------------------------------
# ShedReport.to_dict()
# ---------------------------------------------------------------------------


def test_shed_report_to_dict_is_json_serialisable() -> None:
    conn = _make_conn()
    report = shed_columns(
        conn,
        schema="public",
        table="mytable",
        columns=["geom_wkb_hex"],
        partition_column="dept",
        partition_values=["63"],
        proof=_valid_proof(),
    )
    d = report.to_dict()
    # Must be JSON-serialisable
    json.dumps(d)
    assert d["dry_run"] is True
    assert d["schema"] == "public"
    assert d["table"] == "mytable"


# ---------------------------------------------------------------------------
# materialize_generated_column — DROP EXPRESSION path
# ---------------------------------------------------------------------------


def test_materialize_drop_expression_path() -> None:
    conn = _make_conn(existing_columns=["centroid"], is_generated=True)
    result = materialize_generated_column(
        conn,
        schema="public",
        table="mytable",
        column="centroid",
        dry_run=False,
    )
    assert result["action"] == "drop_expression"
    alter_stmts = [s for s in conn.all_sql() if "ALTER" in s.upper() and "DROP EXPRESSION" in s.upper()]
    assert alter_stmts, "Expected ALTER ... DROP EXPRESSION"


def test_materialize_dry_run_returns_plan_no_write() -> None:
    conn = _make_conn(existing_columns=["centroid"], is_generated=True)
    result = materialize_generated_column(
        conn,
        schema="public",
        table="mytable",
        column="centroid",
        dry_run=True,
    )
    assert result["action"] in ("drop_expression", "recreate")
    assert conn.mutating_sql() == []


def test_materialize_noop_for_non_generated_column() -> None:
    conn = _make_conn(existing_columns=["centroid"], is_generated=False)
    result = materialize_generated_column(
        conn,
        schema="public",
        table="mytable",
        column="centroid",
        dry_run=False,
    )
    assert result["action"] == "noop"
    assert conn.mutating_sql() == []


def test_materialize_column_absent_raises() -> None:
    conn = _make_conn(existing_columns=["other"])
    with pytest.raises(PostgisLifecycleError) as exc_info:
        materialize_generated_column(
            conn,
            schema="public",
            table="mytable",
            column="centroid",
            dry_run=False,
        )
    assert exc_info.value.code == "column_absent"


def test_get_indexes_uses_catalogue_join_not_like() -> None:
    """_get_indexes_for_column must use a catalogue join, not substring LIKE %col%.
    The LIKE pattern with _ wildcard produces false positives and the raw indexdef
    replay can silently break on restored indexes."""
    conn = _make_conn(
        existing_columns=["centroid"],
        is_generated=True,
        indexes=["CREATE INDEX idx_centroid ON mytable USING GIST (centroid)"],
    )
    materialize_generated_column(
        conn,
        schema="public",
        table="mytable",
        column="centroid",
        dry_run=True,  # dry_run so it reads indexes but does not mutate
    )
    # No raw LIKE %centroid% must appear — only catalogue join or regex filter
    like_sqls = [s for s in conn.all_sql() if re.search(r"LIKE\s+%s", s, re.IGNORECASE)]
    assert like_sqls == [], f"Found forbidden LIKE query: {like_sqls}"
    # Catalogue join must be present
    catalogue_sqls = [
        s for s in conn.all_sql()
        if re.search(r"pg_index\b|pg_indexes\b", s, re.IGNORECASE)
    ]
    assert catalogue_sqls, "Expected a catalogue-join query for index discovery"


def test_drop_expression_unsupported_42601_with_expression_in_msg() -> None:
    """pgcode=42601 AND 'expression' in message → treated as unsupported, fallback."""
    conn = _make_conn(existing_columns=["centroid"], is_generated=True)

    class _DropExprFails(FakeConn):
        @contextmanager
        def cursor(self):  # type: ignore[override]
            cur = _DropExpr42601ExprCursor(self._responses)
            self._cursors.append(cur)
            yield cur

    class _DropExpr42601ExprCursor(FakeCursor):
        def execute(self, sql: str, params: tuple = ()) -> None:
            super().execute(sql, params)
            if "DROP EXPRESSION" in sql.upper():
                err = Exception("drop expression syntax error")
                err.pgcode = "42601"  # type: ignore[attr-defined]
                raise err

    conn2 = _DropExprFails(conn._responses)
    result = materialize_generated_column(
        conn2, schema="public", table="mytable", column="centroid", dry_run=False
    )
    # Must not re-raise — should have fallen back
    assert result["action"] == "recreate"


def test_drop_expression_unsupported_42601_without_expression_reraises() -> None:
    """pgcode=42601 WITHOUT 'expression' in message is a real syntax error — must re-raise,
    not silently fall back to the destructive recreate path."""
    conn = _make_conn(existing_columns=["centroid"], is_generated=True)

    class _DropExprFails(FakeConn):
        @contextmanager
        def cursor(self):  # type: ignore[override]
            cur = _DropExpr42601NoExprCursor(self._responses)
            self._cursors.append(cur)
            yield cur

    class _DropExpr42601NoExprCursor(FakeCursor):
        def execute(self, sql: str, params: tuple = ()) -> None:
            super().execute(sql, params)
            if "DROP EXPRESSION" in sql.upper():
                err = Exception("syntax error near COLUMN")  # no 'expression' in message
                err.pgcode = "42601"  # type: ignore[attr-defined]
                raise err

    conn2 = _DropExprFails(conn._responses)
    with pytest.raises(Exception, match="syntax error near COLUMN"):
        materialize_generated_column(
            conn2, schema="public", table="mytable", column="centroid", dry_run=False
        )


def test_drop_expression_unsupported_0a000_always_fallback() -> None:
    """pgcode=0A000 always triggers fallback regardless of message."""
    conn = _make_conn(existing_columns=["centroid"], is_generated=True)

    class _DropExprFails(FakeConn):
        @contextmanager
        def cursor(self):  # type: ignore[override]
            cur = _DropExpr0A000Cursor(self._responses)
            self._cursors.append(cur)
            yield cur

    class _DropExpr0A000Cursor(FakeCursor):
        def execute(self, sql: str, params: tuple = ()) -> None:
            super().execute(sql, params)
            if "DROP EXPRESSION" in sql.upper():
                err = Exception("feature not supported")
                err.pgcode = "0A000"  # type: ignore[attr-defined]
                raise err

    conn2 = _DropExprFails(conn._responses)
    result = materialize_generated_column(
        conn2, schema="public", table="mytable", column="centroid", dry_run=False
    )
    assert result["action"] == "recreate"


def test_materialize_fallback_recreate_reexecutes_indexes() -> None:
    """When DROP EXPRESSION is unsupported, fallback recreates column and re-runs indexes."""
    conn = _make_conn(
        existing_columns=["centroid"],
        is_generated=True,
        indexes=["CREATE INDEX idx_centroid ON mytable USING GIST (centroid)"],
    )

    class _DropExprFails(FakeConn):
        @contextmanager
        def cursor(self):  # type: ignore[override]
            cur = _DropExprFailCursor(self._responses)
            self._cursors.append(cur)
            yield cur

    class _DropExprFailCursor(FakeCursor):
        def execute(self, sql: str, params: tuple = ()) -> None:
            super().execute(sql, params)
            if "DROP EXPRESSION" in sql.upper():
                err = Exception("syntax error: drop expression not supported")
                err.pgcode = "42601"  # type: ignore[attr-defined]
                raise err

    conn2 = _DropExprFails(conn._responses)
    result = materialize_generated_column(
        conn2,
        schema="public",
        table="mytable",
        column="centroid",
        dry_run=False,
    )
    assert result["action"] == "recreate"
    all_sql = conn2.all_sql()
    assert any("CREATE INDEX" in s.upper() for s in all_sql), "Indexes must be re-created"
    assert any("ADD COLUMN" in s.upper() for s in all_sql), "Temp column must be added"
    assert any("RENAME" in s.upper() for s in all_sql), "Column must be renamed"


def test_materialize_fallback_recreate_single_transaction() -> None:
    """The entire fallback swap (ADD/UPDATE/DROP/RENAME + all CREATE INDEX) must
    be executed within a single transaction: one commit at the end, and a rollback
    with re-raise on any failure — including on a second CREATE INDEX."""
    conn = _make_conn(
        existing_columns=["centroid"],
        is_generated=True,
        indexes=[
            "CREATE INDEX idx_centroid_1 ON mytable USING GIST (centroid)",
            "CREATE INDEX idx_centroid_2 ON mytable USING GIST (centroid)",
        ],
    )

    class _DropExprFails(FakeConn):
        @contextmanager
        def cursor(self):  # type: ignore[override]
            cur = _DropExprFailCursor(self._responses)
            self._cursors.append(cur)
            yield cur

    class _DropExprFailCursor(FakeCursor):
        _second_index_seen: int = 0

        def execute(self, sql: str, params: tuple = ()) -> None:
            super().execute(sql, params)
            if "DROP EXPRESSION" in sql.upper():
                err = Exception("syntax error: drop expression not supported")
                err.pgcode = "42601"  # type: ignore[attr-defined]
                raise err
            if "idx_centroid_2" in sql:
                raise RuntimeError("simulated failure on second CREATE INDEX")

    conn2 = _DropExprFails(conn._responses)
    with pytest.raises(RuntimeError, match="simulated failure on second CREATE INDEX"):
        materialize_generated_column(
            conn2,
            schema="public",
            table="mytable",
            column="centroid",
            dry_run=False,
        )
    # Entire transaction must be rolled back — no commit should have occurred
    assert conn2.committed == 0, "No commit must occur when fallback fails mid-way"
    assert conn2.rolled_back >= 1, "rollback must be called on failure"


# ---------------------------------------------------------------------------
# CLI smoke test (CliRunner or direct function test)
# ---------------------------------------------------------------------------


def test_cli_shed_columns_dry_run_exit_zero(tmp_path, monkeypatch) -> None:
    """CLI dry-run prints JSON and exits 0.

    Note: postgis_app has a single command (shed-columns), so typer
    treats it as the app itself — no subcommand name needed.
    """
    import json as _json

    from typer.testing import CliRunner

    from gispulse.cli_postgis import postgis_app

    proof_path = tmp_path / "proof.json"
    proof_path.write_text(
        _json.dumps({
            "partition_values": ["63"],
            "row_count": 100,
            "checks": {"ok": True},
        })
    )

    fake_conn = _make_conn()

    def _fake_connect(dsn: str) -> FakeConn:
        return fake_conn

    monkeypatch.setattr(
        "gispulse.persistence.postgis_lifecycle._connect_psycopg2",
        _fake_connect,
    )

    runner = CliRunner()
    result = runner.invoke(
        postgis_app,
        [
            "--dsn", "postgresql://fake",
            "--schema", "public",
            "--table", "mytable",
            "--column", "geom_wkb_hex",
            "--partition-column", "dept",
            "--partition-value", "63",
            "--proof", str(proof_path),
        ],
    )
    assert result.exit_code == 0, result.output
    # structlog may write log lines before the JSON report; take the last non-empty line
    last_line = [l for l in result.output.strip().splitlines() if l.strip()][-1]
    data = _json.loads(last_line)
    assert data["dry_run"] is True


def test_cli_shed_columns_gate_failure_exit_one(tmp_path, monkeypatch) -> None:
    """CLI gate failure prints JSON error on output and exits 1.

    typer.testing.CliRunner mixes stderr into stdout by default; we parse
    from output since there is no mix_stderr kwarg in this typer version.
    """
    import json as _json

    from typer.testing import CliRunner

    from gispulse.cli_postgis import postgis_app

    proof_path = tmp_path / "proof.json"
    # checks has a False → proof_checks_failed
    proof_path.write_text(
        _json.dumps({
            "partition_values": ["63"],
            "row_count": 100,
            "checks": {"ok": False},
        })
    )

    fake_conn = _make_conn()

    def _fake_connect(dsn: str) -> FakeConn:
        return fake_conn

    monkeypatch.setattr(
        "gispulse.persistence.postgis_lifecycle._connect_psycopg2",
        _fake_connect,
    )

    runner = CliRunner()
    result = runner.invoke(
        postgis_app,
        [
            "--dsn", "postgresql://fake",
            "--schema", "public",
            "--table", "mytable",
            "--column", "geom_wkb_hex",
            "--partition-column", "dept",
            "--partition-value", "63",
            "--proof", str(proof_path),
        ],
    )
    assert result.exit_code == 1
    # CliRunner merges stderr into output; parse JSON from output
    err_data = _json.loads(result.output)
    assert err_data["error"] == "proof_checks_failed"
