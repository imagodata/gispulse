"""Tests for ``gispulse.runtime.validation_runner`` (v1.6.0).

Coverage:
- ``compile_validate_rules`` produces ``CompiledValidateRule`` objects.
- Compile errors surface in the ``errors`` list, not as exceptions.
- Disabled rules are skipped during compilation.
- ``ValidationRunner.evaluate`` runs each rule and returns failures.
- Rule SQL is wrapped in ``SELECT NOT (rule) FROM table WHERE pk = ?``.
- Failures broadcast on the event hub when one is configured.
- Driver-side exceptions on a single rule do not abort the batch.
- DuckDB E2E: a ``geom_is_valid()`` rule fires correctly against an
  actual table (uses the duckdb_engine wrapper).
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from gispulse.runtime.validation_runner import (
    CompileError,
    CompiledValidateRule,
    ValidationRunner,
    compile_validate_rules,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[tuple[str, dict[str, Any]]] = []

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.events.append((event_type, data or {}))


def _rule(
    *,
    id: str = "r",
    rule: str = "geom_is_valid()",
    mode: str = "warn",
    tag_field: str | None = None,
    message: str | None = None,
    enabled: bool = True,
):
    """Build a duck-typed ValidateRuleConfigModel-compatible object."""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=id,
        rule=rule,
        mode=mode,
        tag_field=tag_field,
        message=message,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# compile_validate_rules
# ---------------------------------------------------------------------------


class TestCompileValidateRules:
    def test_minimal_rule_compiles(self) -> None:
        result = compile_validate_rules(
            [_rule(rule="geom_is_valid()")],
            table="parcels",
            source_epsg=None,
        )
        assert len(result.rules) == 1
        assert result.errors == []
        compiled = result.rules[0]
        assert isinstance(compiled, CompiledValidateRule)
        assert "ST_IsValid" in compiled.rule_sql
        assert compiled.table == "parcels"
        assert compiled.pk_col == "id"

    def test_crs_aware_rule_uses_source_epsg(self) -> None:
        result = compile_validate_rules(
            [_rule(rule="geom_area_m2() >= 50")],
            table="parcels",
            source_epsg="EPSG:4326",
        )
        assert len(result.rules) == 1
        assert "EPSG:4326" in result.rules[0].rule_sql

    def test_disabled_rule_skipped(self) -> None:
        result = compile_validate_rules(
            [_rule(rule="geom_is_valid()", enabled=False)],
            table="parcels",
            source_epsg=None,
        )
        assert result.rules == []
        assert result.errors == []

    def test_compile_error_surfaces_in_errors_list(self) -> None:
        result = compile_validate_rules(
            [_rule(id="bad", rule="__import__('os')")],
            table="parcels",
            source_epsg=None,
        )
        assert result.rules == []
        assert len(result.errors) == 1
        assert isinstance(result.errors[0], CompileError)
        assert result.errors[0].rule_id == "bad"

    def test_mixed_good_and_bad_rules(self) -> None:
        result = compile_validate_rules(
            [
                _rule(id="good", rule="geom_is_valid()"),
                _rule(id="bad", rule="eval('1+1')"),
            ],
            table="parcels",
            source_epsg=None,
        )
        assert len(result.rules) == 1
        assert result.rules[0].id == "good"
        assert len(result.errors) == 1
        assert result.errors[0].rule_id == "bad"

    def test_pk_col_propagates_to_compiled(self) -> None:
        result = compile_validate_rules(
            [_rule(rule="geom_is_valid()")],
            table="parcels",
            source_epsg=None,
            pk_col="fid",
        )
        assert result.rules[0].pk_col == "fid"

    def test_self_subquery_compiles(self) -> None:
        result = compile_validate_rules(
            [_rule(rule="not geom_overlaps_any(layer='self', exclude_self=True)")],
            table="parcels",
            source_epsg=None,
            pk_col="fid",
        )
        assert len(result.rules) == 1
        assert "EXISTS" in result.rules[0].rule_sql
        assert '_L."fid" <> "fid"' in result.rules[0].rule_sql


# ---------------------------------------------------------------------------
# ValidationRunner.evaluate
# ---------------------------------------------------------------------------


def _compiled(
    *,
    id: str = "r",
    table: str = "parcels",
    rule_sql: str = "TRUE",
    mode: str = "warn",
    tag_field: str | None = None,
    message: str | None = None,
    pk_col: str = "id",
) -> CompiledValidateRule:
    return CompiledValidateRule(
        id=id,
        table=table,
        pk_col=pk_col,
        rule_sql=rule_sql,
        mode=mode,
        tag_field=tag_field,
        message=message,
    )


class TestValidationRunner:
    def test_passing_rule_returns_no_failure(self) -> None:
        # SELECT NOT (TRUE) FROM ... → False → no failure
        evaluator = lambda sql, params: [(False,)]
        runner = ValidationRunner([_compiled()], evaluator)
        assert runner.evaluate("parcels", row_id=1) == []

    def test_failing_rule_returns_failure(self) -> None:
        evaluator = lambda sql, params: [(True,)]
        runner = ValidationRunner(
            [_compiled(message="bad", tag_field="validation_status", mode="tag")],
            evaluator,
        )
        failures = runner.evaluate("parcels", row_id=1)
        assert len(failures) == 1
        f = failures[0]
        assert f.rule_id == "r"
        assert f.row_id == 1
        assert f.message == "bad"
        assert f.tag_field == "validation_status"
        assert f.mode == "tag"

    def test_dict_row_shape_supported(self) -> None:
        evaluator = lambda sql, params: [{"failed": True}]
        runner = ValidationRunner([_compiled()], evaluator)
        failures = runner.evaluate("parcels", row_id=1)
        assert len(failures) == 1

    def test_null_row_is_non_failure(self) -> None:
        # Row deleted between change capture and eval — runner returns None
        evaluator = lambda sql, params: [(None,)]
        runner = ValidationRunner([_compiled()], evaluator)
        assert runner.evaluate("parcels", row_id=1) == []

    def test_empty_result_is_non_failure(self) -> None:
        evaluator = lambda sql, params: []
        runner = ValidationRunner([_compiled()], evaluator)
        assert runner.evaluate("parcels", row_id=99) == []

    def test_other_table_rules_skipped(self) -> None:
        evaluator = lambda sql, params: [(True,)]
        runner = ValidationRunner(
            [_compiled(table="other")], evaluator
        )
        assert runner.evaluate("parcels", row_id=1) == []

    def test_evaluator_exception_does_not_abort_batch(self) -> None:
        calls: list[tuple[str, list]] = []

        def boom(sql: str, params: list) -> Any:
            calls.append((sql, params))
            if "rule_a" in sql:
                raise RuntimeError("driver crash")
            return [(True,)]

        rules = [
            _compiled(id="rule_a", rule_sql="rule_a"),
            _compiled(id="rule_b", rule_sql="rule_b"),
        ]
        runner = ValidationRunner(rules, boom)
        failures = runner.evaluate("parcels", row_id=1)
        # rule_a crashed, rule_b succeeded → only one failure surfaces
        assert {f.rule_id for f in failures} == {"rule_b"}

    def test_failure_is_broadcast_on_hub(self) -> None:
        evaluator = lambda sql, params: [(True,)]
        hub = _RecordingHub()
        runner = ValidationRunner(
            [_compiled(message="surface < 50", mode="tag", tag_field="vstatus")],
            evaluator,
            hub=hub,
            dataset_id="ds-42",
        )
        runner.evaluate("parcels", row_id=7)
        assert len(hub.events) == 1
        kind, data = hub.events[0]
        assert kind == "validation.failed"
        assert data["rule_id"] == "r"
        assert data["row_id"] == 7
        assert data["dataset_id"] == "ds-42"
        assert data["mode"] == "tag"
        assert data["message"] == "surface < 50"
        assert data["tag_field"] == "vstatus"

    def test_pk_col_used_in_emitted_sql(self) -> None:
        captured: list[str] = []

        def evaluator(sql: str, params: list) -> Any:
            captured.append(sql)
            return [(False,)]

        runner = ValidationRunner(
            [_compiled(pk_col="fid", rule_sql="TRUE")], evaluator
        )
        runner.evaluate("parcels", row_id=42)
        assert captured, "evaluator should have been called"
        assert '"fid" = ?' in captured[0]

    def test_rule_count_property(self) -> None:
        runner = ValidationRunner(
            [_compiled(id="a"), _compiled(id="b")],
            sql_evaluator=lambda s, p: [(False,)],
        )
        assert runner.rule_count == 2


# ---------------------------------------------------------------------------
# DuckDB E2E
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_conn():
    from gispulse.runtime.duckdb_engine import (
        _reset_cache_for_tests,
        get_spatial_connection,
    )

    _reset_cache_for_tests()
    conn = get_spatial_connection()
    conn.execute(
        "CREATE TABLE parcels (id INTEGER, geom GEOMETRY)"
    )
    conn.execute(
        "INSERT INTO parcels VALUES "
        "(1, ST_GeomFromText('POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))')), "
        "(2, ST_GeomFromText('POLYGON((0 0, 1 1, 0 1, 1 0, 0 0))'))"  # self-int
    )
    yield conn
    conn.close()


class TestDuckDBE2E:
    def test_geom_is_valid_rule_against_real_table(self, duckdb_conn) -> None:
        result = compile_validate_rules(
            [_rule(id="shape_valid", rule="geom_is_valid()")],
            table="parcels",
            source_epsg=None,
        )
        assert len(result.rules) == 1

        def evaluator(sql: str, params: list) -> Any:
            return duckdb_conn.execute(sql.replace("?", "?"), params).fetchall()

        runner = ValidationRunner(result.rules, evaluator)
        # Row 1 = valid square → no failure
        assert runner.evaluate("parcels", row_id=1) == []
        # Row 2 = self-intersecting → failure
        failures = runner.evaluate("parcels", row_id=2)
        assert len(failures) == 1
        assert failures[0].rule_id == "shape_valid"
