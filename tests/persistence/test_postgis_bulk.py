"""Tests for gispulse.persistence.postgis_bulk.

Coverage
--------
* build_subdivide_sql — pure function, no DB required.
* subdivide_table — dry_run (no execute), apply (execute+commit), rollback on error.
* batch_load_duckdb_to_postgis — fake duck/pg fakes; ordering, counting, rollback.
"""

from __future__ import annotations

from typing import Any

import pytest

from gispulse.persistence.postgis_bulk import (
    SubdivideSpec,
    batch_load_duckdb_to_postgis,
    build_subdivide_sql,
    subdivide_table,
)


# ---------------------------------------------------------------------------
# Helpers — minimal SubdivideSpec fixtures
# ---------------------------------------------------------------------------


def _simple_spec(
    *,
    delete_predicate: str | None = None,
    insert_predicate: str | None = None,
) -> SubdivideSpec:
    return SubdivideSpec(
        source_schema="staging",
        source_table="iris_raw",
        target_schema="staging",
        target_table="iris_subdivided",
        geom_column="geom",
        max_vertices=256,
        key_columns=("code_iris", "dept"),
        data_columns=("insee_com",),
        column_types={
            "code_iris": "text",
            "dept": "text",
            "insee_com": "text",
        },
        srid=4326,
        delete_predicate=delete_predicate,
        insert_predicate=insert_predicate,
    )


def _no_key_spec() -> SubdivideSpec:
    """Spec with no key_columns (degenerate case)."""
    return SubdivideSpec(
        source_schema="public",
        source_table="geom_raw",
        target_schema="public",
        target_table="geom_subdivided",
        geom_column="shape",
        max_vertices=128,
        key_columns=(),
        data_columns=(),
        column_types={},
        srid=4326,
    )


# ---------------------------------------------------------------------------
# build_subdivide_sql — CREATE TABLE
# ---------------------------------------------------------------------------


class TestBuildSubdivideSqlCreateTable:
    def test_create_table_if_not_exists(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "CREATE TABLE IF NOT EXISTS staging.iris_subdivided" in sql

    def test_key_columns_in_create(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "code_iris text NOT NULL" in sql
        assert "dept text NOT NULL" in sql

    def test_data_columns_in_create(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "insee_com text NOT NULL" in sql

    def test_fragment_id_in_create(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "fragment_id bigint NOT NULL" in sql

    def test_geom_column_with_srid(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "geom geometry(Geometry, 4326) NOT NULL" in sql

    def test_primary_key_includes_key_cols_and_fragment_id(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "PRIMARY KEY (code_iris, dept, fragment_id)" in sql

    def test_no_key_columns_pk_is_fragment_id_only(self) -> None:
        sql = build_subdivide_sql(_no_key_spec())
        assert "PRIMARY KEY (fragment_id)" in sql


# ---------------------------------------------------------------------------
# build_subdivide_sql — DELETE
# ---------------------------------------------------------------------------


class TestBuildSubdivideSqlDelete:
    def test_full_refresh_when_no_delete_predicate(self) -> None:
        sql = build_subdivide_sql(_simple_spec(delete_predicate=None))
        # Full-table delete — no alias, no WHERE clause
        assert "DELETE FROM staging.iris_subdivided;" in sql

    def test_scoped_delete_uses_predicate(self) -> None:
        sql = build_subdivide_sql(
            _simple_spec(delete_predicate="frag.dept = 'dept_63'")
        )
        assert "DELETE FROM staging.iris_subdivided frag" in sql
        assert "frag.dept = 'dept_63'" in sql

    def test_full_refresh_no_where_keyword(self) -> None:
        sql = build_subdivide_sql(_simple_spec(delete_predicate=None))
        # The full-table delete should not carry a WHERE clause
        delete_line = [ln for ln in sql.splitlines() if "DELETE FROM staging" in ln]
        assert len(delete_line) == 1
        assert "WHERE" not in delete_line[0]


# ---------------------------------------------------------------------------
# build_subdivide_sql — INSERT / ST_Dump / ST_Subdivide
# ---------------------------------------------------------------------------


class TestBuildSubdivideSqlInsert:
    def test_insert_into_target(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "INSERT INTO staging.iris_subdivided" in sql

    def test_st_dump_present(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "ST_Dump" in sql

    def test_st_subdivide_with_max_vertices(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "ST_Subdivide(c.geom, 256)" in sql

    def test_dimension_zero_passthrough(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "ST_Dimension(c.geom) = 0" in sql

    def test_dimension_gt_zero_subdivide_branch(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "ST_Dimension(c.geom) > 0" in sql

    def test_union_all_between_passthrough_and_subdivide(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "UNION ALL" in sql

    def test_row_number_partitioned_by_key_columns(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "row_number() OVER" in sql
        assert "PARTITION BY code_iris, dept" in sql

    def test_row_number_order_by_st_area_st_asewkb(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "ST_Area(ST_Envelope(geom)) DESC" in sql
        assert "ST_AsEWKB(geom)" in sql

    def test_no_on_conflict(self) -> None:
        # Plain INSERT — PK violation is the honest signal of a scope mismatch.
        sql = build_subdivide_sql(_simple_spec())
        assert "ON CONFLICT" not in sql

    def test_geom_not_null_filter(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "src.geom IS NOT NULL" in sql
        assert "NOT ST_IsEmpty(d.geom)" in sql

    def test_insert_predicate_in_dumped_where(self) -> None:
        spec = _simple_spec(insert_predicate="src.dept = '63'")
        sql = build_subdivide_sql(spec)
        # predicate should appear in the WHERE of the dumped CTE
        assert "src.dept = '63'" in sql

    def test_predicates_go_to_correct_statement(self) -> None:
        # Regression: a single scope_predicate with different aliases would
        # break one of the two statements.  Verify each predicate lands only
        # in its intended context.
        spec = _simple_spec(
            delete_predicate="frag.dept = '63'",
            insert_predicate="src.dept = '63'",
        )
        sql = build_subdivide_sql(spec)

        # Split on blank lines to isolate the DELETE block from the INSERT block.
        blocks = sql.split("\n\n")
        delete_block = next(b for b in blocks if b.strip().startswith("DELETE"))
        insert_block = next(b for b in blocks if b.strip().startswith("INSERT"))

        # delete_predicate must appear in DELETE, not in INSERT
        assert "frag.dept = '63'" in delete_block
        assert "frag.dept = '63'" not in insert_block

        # insert_predicate must appear in INSERT, not in DELETE
        assert "src.dept = '63'" in insert_block
        assert "src.dept = '63'" not in delete_block

    def test_no_key_columns_row_number_no_partition(self) -> None:
        sql = build_subdivide_sql(_no_key_spec())
        assert "row_number() OVER" in sql
        # Without key columns, no PARTITION BY clause
        assert "PARTITION BY" not in sql

    def test_source_table_referenced_in_insert(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "staging.iris_raw" in sql


# ---------------------------------------------------------------------------
# build_subdivide_sql — Indexes
# ---------------------------------------------------------------------------


class TestBuildSubdivideSqlIndexes:
    def test_gist_index_on_geom(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "CREATE INDEX IF NOT EXISTS iris_subdivided_geom_gist" in sql
        assert "USING GIST (geom)" in sql

    def test_btree_index_on_key_columns(self) -> None:
        sql = build_subdivide_sql(_simple_spec())
        assert "CREATE INDEX IF NOT EXISTS iris_subdivided_key_idx" in sql
        assert "code_iris, dept" in sql

    def test_no_key_index_when_no_key_columns(self) -> None:
        sql = build_subdivide_sql(_no_key_spec())
        assert "_key_idx" not in sql


# ---------------------------------------------------------------------------
# build_subdivide_sql — Validation errors (fast-fail)
# ---------------------------------------------------------------------------


class TestBuildSubdivideSqlValidation:
    def test_mixed_case_schema_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="Staging",  # capital S
                    source_table="iris_raw",
                    target_schema="staging",
                    target_table="iris_subdivided",
                    column_types={},
                )
            )

    def test_mixed_case_table_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="staging",
                    source_table="IrisRaw",  # camel case
                    target_schema="staging",
                    target_table="iris_subdivided",
                    column_types={},
                )
            )

    def test_identifier_with_special_chars_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="staging; DROP TABLE users--",
                    source_table="iris_raw",
                    target_schema="staging",
                    target_table="iris_subdivided",
                    column_types={},
                )
            )

    def test_column_with_dash_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="staging",
                    source_table="iris_raw",
                    target_schema="staging",
                    target_table="iris_subdivided",
                    key_columns=("code-iris",),  # dash not allowed
                    column_types={"code-iris": "text"},
                )
            )

    def test_column_missing_from_column_types_raises(self) -> None:
        with pytest.raises(ValueError, match="no entry in column_types"):
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="staging",
                    source_table="iris_raw",
                    target_schema="staging",
                    target_table="iris_subdivided",
                    key_columns=("code_iris",),
                    column_types={},  # missing code_iris
                )
            )

    def test_type_comma_injection_raises(self) -> None:
        # "text, injected text" was accepted by the old broad regex — must reject
        with pytest.raises(ValueError, match="Invalid SQL type"):
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="staging",
                    source_table="iris_raw",
                    target_schema="staging",
                    target_table="iris_subdivided",
                    key_columns=("code_iris",),
                    column_types={"code_iris": "text, injected text"},
                )
            )

    def test_type_not_null_injection_raises(self) -> None:
        # "text NOT NULL, x text" must be rejected
        with pytest.raises(ValueError, match="Invalid SQL type"):
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="staging",
                    source_table="iris_raw",
                    target_schema="staging",
                    target_table="iris_subdivided",
                    key_columns=("col",),
                    column_types={"col": "text NOT NULL, x text"},
                )
            )

    def test_type_drop_injection_raises(self) -> None:
        # "jsonb); DROP" must be rejected
        with pytest.raises(ValueError, match="Invalid SQL type"):
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="staging",
                    source_table="iris_raw",
                    target_schema="staging",
                    target_table="iris_subdivided",
                    key_columns=("col",),
                    column_types={"col": "jsonb); DROP"},
                )
            )

    def test_type_with_single_quote_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL type"):
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="staging",
                    source_table="iris_raw",
                    target_schema="staging",
                    target_table="iris_subdivided",
                    key_columns=("col",),
                    column_types={"col": "text'"},
                )
            )

    # --- accepted valid types ------------------------------------------------

    def test_valid_types_accepted(self) -> None:
        valid_types = ["text", "bigint", "double precision", "timestamptz",
                       "varchar(255)", "numeric(10,2)"]
        for t in valid_types:
            # Should not raise
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="staging",
                    source_table="iris_raw",
                    target_schema="staging",
                    target_table="iris_subdivided",
                    key_columns=("col",),
                    column_types={"col": t},
                )
            )

    def test_geom_column_with_space_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            build_subdivide_sql(
                SubdivideSpec(
                    source_schema="staging",
                    source_table="iris_raw",
                    target_schema="staging",
                    target_table="iris_subdivided",
                    geom_column="bad col",  # space not allowed
                    column_types={},
                )
            )


# ---------------------------------------------------------------------------
# subdivide_table — dry_run / apply / rollback
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.rowcount: int = 42

    def execute(self, sql: str) -> None:
        self.executed.append(sql)

    def close(self) -> None:
        pass


class _FakeConn:
    def __init__(self, raise_on_execute: bool = False) -> None:
        self._raise = raise_on_execute
        self._cursor = _FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _FakeCursor:
        if self._raise:
            # Raise on the first execute inside cursor
            orig_execute = self._cursor.execute

            def _failing_execute(sql: str) -> None:
                raise RuntimeError("DB error")

            self._cursor.execute = _failing_execute  # type: ignore[method-assign]
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class TestSubdivideTable:
    def test_dry_run_returns_sql_not_executed(self) -> None:
        conn = _FakeConn()
        spec = _simple_spec()
        result = subdivide_table(conn, spec, dry_run=True)
        assert result["executed"] is False
        assert "CREATE TABLE IF NOT EXISTS" in result["sql"]
        # No execute should have happened
        assert conn._cursor.executed == []
        assert not conn.committed

    def test_apply_executes_and_commits(self) -> None:
        conn = _FakeConn()
        spec = _simple_spec()
        result = subdivide_table(conn, spec, dry_run=False)
        assert result["executed"] is True
        assert len(conn._cursor.executed) == 1  # single SQL block
        assert conn.committed is True
        assert conn.rolled_back is False

    def test_apply_returns_rows_inserted(self) -> None:
        conn = _FakeConn()
        result = subdivide_table(conn, _simple_spec(), dry_run=False)
        assert result["rows_inserted"] == 42

    def test_exception_triggers_rollback_not_commit(self) -> None:
        conn = _FakeConn(raise_on_execute=True)
        with pytest.raises(RuntimeError, match="DB error"):
            subdivide_table(conn, _simple_spec(), dry_run=False)
        assert conn.rolled_back is True
        assert conn.committed is False

    def test_dry_run_is_default(self) -> None:
        conn = _FakeConn()
        result = subdivide_table(conn, _simple_spec())  # no dry_run kwarg
        assert result["executed"] is False


# ---------------------------------------------------------------------------
# batch_load_duckdb_to_postgis
# ---------------------------------------------------------------------------


class _FakeDuckCursor:
    """Fake DuckDB cursor yielding predetermined batches."""

    def __init__(self, batches: list[list[Any]]) -> None:
        self._batches = list(batches)
        self._index = 0

    def fetchmany(self, size: int) -> list[Any]:
        if self._index >= len(self._batches):
            return []
        rows = self._batches[self._index]
        self._index += 1
        return rows


class _FakeDuckConn:
    def __init__(self, batches: list[list[Any]], raise_on_batch: int | None = None) -> None:
        self._batches = batches
        self._raise_on_batch = raise_on_batch

    def execute(self, sql: str) -> _FakeDuckCursor:
        if self._raise_on_batch is not None:
            # Inject a cursor that will raise on the specified batch
            return _RaisingDuckCursor(self._batches, self._raise_on_batch)
        return _FakeDuckCursor(self._batches)


class _RaisingDuckCursor(_FakeDuckCursor):
    def __init__(self, batches: list[list[Any]], raise_on: int) -> None:
        super().__init__(batches)
        self._raise_on = raise_on

    def fetchmany(self, size: int) -> list[Any]:
        if self._index == self._raise_on:
            raise RuntimeError("DuckDB fetch error")
        return super().fetchmany(size)


class _FakePgCursor:
    def __init__(self) -> None:
        self.executed_deletes: list[tuple[str, Any]] = []
        self.inserted_rows: list[Any] = []
        self.executemany_calls: int = 0

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed_deletes.append((sql, params))

    def executemany(self, sql: str, rows: list[Any]) -> None:
        self.inserted_rows.extend(rows)
        self.executemany_calls += 1

    def close(self) -> None:
        pass


class _FakePgConn:
    def __init__(self) -> None:
        self._cursor = _FakePgCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _FakePgCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class TestBatchLoadDuckdbToPostgis:
    def _make(self, batches: list[list[Any]], **kwargs: Any) -> tuple[_FakeDuckConn, _FakePgConn, dict]:
        duck = _FakeDuckConn(batches)
        pg = _FakePgConn()
        result = batch_load_duckdb_to_postgis(
            duck, pg,
            select_sql="SELECT * FROM x",
            insert_sql="INSERT INTO y VALUES (%s, %s)",
            **kwargs,
        )
        return duck, pg, result

    # --- basic correctness --------------------------------------------------

    def test_rows_and_batches_counted(self) -> None:
        batches = [[("a", 1), ("b", 2)], [("c", 3)]]
        _, _, result = self._make(batches)
        assert result["rows_loaded"] == 3
        assert result["batches"] == 2
        assert result["executed"] is True

    def test_single_commit_at_end(self) -> None:
        _, pg, _ = self._make([[("a", 1)]])
        assert pg.committed is True
        assert pg.rolled_back is False

    def test_empty_source_zero_rows(self) -> None:
        _, _, result = self._make([])
        assert result["rows_loaded"] == 0
        assert result["batches"] == 0

    def test_last_partial_batch_handled(self) -> None:
        # batch_size=3 but last batch has only 1 row
        batches = [[(i,) for i in range(3)], [(99,)]]
        _, pg, result = self._make(batches, batch_size=3)
        assert result["rows_loaded"] == 4
        assert result["batches"] == 2
        assert len(pg._cursor.inserted_rows) == 4

    # --- delete before inserts ----------------------------------------------

    def test_delete_executed_before_inserts(self) -> None:
        duck = _FakeDuckConn([[("a", 1)]])
        pg = _FakePgConn()
        batch_load_duckdb_to_postgis(
            duck, pg,
            select_sql="SELECT * FROM x",
            insert_sql="INSERT INTO y VALUES (%s, %s)",
            delete_sql="DELETE FROM y WHERE dept = %s",
            delete_params=("63",),
        )
        # Delete should have been recorded
        assert len(pg._cursor.executed_deletes) == 1
        del_sql, del_params = pg._cursor.executed_deletes[0]
        assert "DELETE FROM y" in del_sql
        assert del_params == ("63",)
        # Insert should also have happened
        assert len(pg._cursor.inserted_rows) == 1

    def test_delete_then_insert_then_single_commit(self) -> None:
        duck = _FakeDuckConn([[("x",)]])
        pg = _FakePgConn()
        batch_load_duckdb_to_postgis(
            duck, pg,
            select_sql="SELECT 1",
            insert_sql="INSERT INTO t VALUES (%s)",
            delete_sql="DELETE FROM t WHERE 1=1",
        )
        assert pg.committed is True
        assert pg.rolled_back is False

    def test_no_delete_when_delete_sql_is_none(self) -> None:
        _, pg, _ = self._make([[("a",)]])
        assert len(pg._cursor.executed_deletes) == 0

    # --- rollback on error --------------------------------------------------

    def test_exception_on_second_batch_triggers_rollback(self) -> None:
        batches = [[("a",)], [("b",)], [("c",)]]
        duck = _FakeDuckConn(batches, raise_on_batch=1)  # raise on batch index 1
        pg = _FakePgConn()
        with pytest.raises(RuntimeError, match="DuckDB fetch error"):
            batch_load_duckdb_to_postgis(
                duck, pg,
                select_sql="SELECT 1",
                insert_sql="INSERT INTO t VALUES (%s)",
            )
        assert pg.rolled_back is True
        assert pg.committed is False

    def test_no_commit_on_rollback(self) -> None:
        duck = _FakeDuckConn([[("a",)]], raise_on_batch=0)
        pg = _FakePgConn()
        with pytest.raises(RuntimeError):
            batch_load_duckdb_to_postgis(
                duck, pg,
                select_sql="SELECT 1",
                insert_sql="INSERT INTO t VALUES (%s)",
            )
        assert not pg.committed

    # --- validation ---------------------------------------------------------

    def test_batch_size_zero_raises(self) -> None:
        duck = _FakeDuckConn([])
        pg = _FakePgConn()
        with pytest.raises(ValueError, match="batch_size must be >= 1"):
            batch_load_duckdb_to_postgis(
                duck, pg,
                select_sql="SELECT 1",
                insert_sql="INSERT INTO t VALUES (%s)",
                batch_size=0,
            )

    def test_batch_size_negative_raises(self) -> None:
        duck = _FakeDuckConn([])
        pg = _FakePgConn()
        with pytest.raises(ValueError, match="batch_size must be >= 1"):
            batch_load_duckdb_to_postgis(
                duck, pg,
                select_sql="SELECT 1",
                insert_sql="INSERT INTO t VALUES (%s)",
                batch_size=-5,
            )

    def test_empty_select_sql_raises(self) -> None:
        duck = _FakeDuckConn([])
        pg = _FakePgConn()
        with pytest.raises(ValueError, match="select_sql must not be empty"):
            batch_load_duckdb_to_postgis(
                duck, pg,
                select_sql="",
                insert_sql="INSERT INTO t VALUES (%s)",
            )

    def test_empty_insert_sql_raises(self) -> None:
        duck = _FakeDuckConn([])
        pg = _FakePgConn()
        with pytest.raises(ValueError, match="insert_sql must not be empty"):
            batch_load_duckdb_to_postgis(
                duck, pg,
                select_sql="SELECT 1",
                insert_sql="   ",
            )

    # --- multi-batch exact count --------------------------------------------

    def test_multi_batch_exact_row_count(self) -> None:
        # 5 batches × 3 rows + 1 partial = 16 rows
        full_batches = [[(i, j) for j in range(3)] for i in range(5)]
        partial_batch = [(99, 0)]
        batches = full_batches + [partial_batch]
        _, _, result = self._make(batches, batch_size=3)
        assert result["rows_loaded"] == 16
        assert result["batches"] == 6
