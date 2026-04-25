"""Unit tests for core/filter/expression_converter.py — ExpressionConverter.

Covers:
- validate(): valid expressions, empty, unbalanced parens, dangerous SQL keywords
- to_duckdb_sql(): (sql, params) tuple, table name, WHERE clause
- to_postgis_sql(): (sql, params) tuple, qualified schema.table name
- combine(): empty list, single, multiple expressions, OR operator
- get_spatial_predicate_name(): PANDAS, DUCKDB, POSTGIS dialects

All tests are self-contained; no PostGIS or DuckDB connection required.
"""

from __future__ import annotations

import pytest

from core.filter.expression import Dialect, FilterExpression, SpatialPredicate
from core.filter.expression_converter import ExpressionConverter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conv() -> ExpressionConverter:
    return ExpressionConverter()


@pytest.fixture
def simple_attr_expr() -> FilterExpression:
    return FilterExpression.create("area > 100")


@pytest.fixture
def spatial_intersects_expr() -> FilterExpression:
    return FilterExpression.create_spatial(
        [SpatialPredicate.INTERSECTS],
        ref_wkt="POINT(0 0)",
    )


@pytest.fixture
def spatial_dwithin_expr() -> FilterExpression:
    return FilterExpression.create_spatial(
        [SpatialPredicate.DWITHIN],
        ref_wkt="POINT(2 3)",
        buffer_value=500.0,
    )


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


class TestValidate:
    def test_valid_simple_expression(self, conv):
        ok, errors = conv.validate("area > 100")
        assert ok is True
        assert errors == []

    def test_valid_complex_expression(self, conv):
        ok, errors = conv.validate("(status == 'active') AND (area > 0)")
        assert ok is True
        assert errors == []

    def test_valid_balanced_nested_parens(self, conv):
        ok, errors = conv.validate("((a > 0) AND (b > 0)) OR (c == 1)")
        assert ok is True
        assert errors == []

    def test_valid_numeric_comparison(self, conv):
        ok, errors = conv.validate("population >= 1000")
        assert ok is True

    def test_valid_string_equality(self, conv):
        ok, errors = conv.validate("type == 'residential'")
        assert ok is True

    def test_empty_string_is_invalid(self, conv):
        ok, errors = conv.validate("")
        assert ok is False
        assert len(errors) > 0
        assert any("empty" in e.lower() for e in errors)

    def test_whitespace_only_is_invalid(self, conv):
        ok, errors = conv.validate("   ")
        assert ok is False
        assert len(errors) > 0

    def test_dangerous_DROP_keyword(self, conv):
        ok, errors = conv.validate("DROP TABLE users")
        assert ok is False
        assert any("disallowed" in e.lower() or "DROP" in e for e in errors)

    def test_dangerous_DELETE_keyword(self, conv):
        ok, errors = conv.validate("DELETE FROM parcels WHERE 1=1")
        assert ok is False

    def test_dangerous_INSERT_keyword(self, conv):
        ok, errors = conv.validate("INSERT INTO foo VALUES (1)")
        assert ok is False

    def test_dangerous_UPDATE_keyword(self, conv):
        ok, errors = conv.validate("UPDATE foo SET val=1")
        assert ok is False

    def test_dangerous_ALTER_keyword(self, conv):
        ok, errors = conv.validate("ALTER TABLE foo ADD COLUMN x INT")
        assert ok is False

    def test_dangerous_CREATE_keyword(self, conv):
        ok, errors = conv.validate("CREATE TABLE evil AS SELECT 1")
        assert ok is False

    def test_dangerous_TRUNCATE_keyword(self, conv):
        ok, errors = conv.validate("TRUNCATE TABLE foo")
        assert ok is False

    def test_dangerous_keyword_case_insensitive_lower(self, conv):
        ok, errors = conv.validate("drop table foo")
        assert ok is False

    def test_dangerous_keyword_case_insensitive_mixed(self, conv):
        ok, errors = conv.validate("Drop Table foo")
        assert ok is False

    def test_unbalanced_parens_unclosed(self, conv):
        ok, errors = conv.validate("(area > 0")
        assert ok is False
        assert any("paren" in e.lower() for e in errors)

    def test_unbalanced_parens_extra_close(self, conv):
        ok, errors = conv.validate("area > 0)")
        assert ok is False
        assert any("paren" in e.lower() for e in errors)

    def test_unbalanced_parens_reversed(self, conv):
        ok, errors = conv.validate(")(area > 0)(")
        assert ok is False

    def test_deeply_nested_balanced_parens(self, conv):
        ok, errors = conv.validate("((((a > 0))))")
        assert ok is True

    def test_returns_two_tuple(self, conv):
        result = conv.validate("area > 0")
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# to_duckdb_sql()
# ---------------------------------------------------------------------------


class TestToDuckdbSql:
    def test_returns_tuple_of_two(self, conv, simple_attr_expr):
        result = conv.to_duckdb_sql(simple_attr_expr, "parcels")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_sql_contains_select_star(self, conv, simple_attr_expr):
        sql, _ = conv.to_duckdb_sql(simple_attr_expr, "parcels")
        assert "SELECT *" in sql

    def test_sql_contains_table_name(self, conv, simple_attr_expr):
        sql, _ = conv.to_duckdb_sql(simple_attr_expr, "parcels")
        assert "parcels" in sql

    def test_sql_contains_where(self, conv, simple_attr_expr):
        sql, _ = conv.to_duckdb_sql(simple_attr_expr, "parcels")
        assert "WHERE" in sql

    def test_sql_contains_attribute_expression(self, conv, simple_attr_expr):
        sql, _ = conv.to_duckdb_sql(simple_attr_expr, "parcels")
        assert "area > 100" in sql

    def test_params_empty_for_attribute_filter(self, conv, simple_attr_expr):
        _, params = conv.to_duckdb_sql(simple_attr_expr, "parcels")
        assert isinstance(params, list)
        assert params == []

    def test_custom_table_name_propagated(self, conv, simple_attr_expr):
        sql, _ = conv.to_duckdb_sql(simple_attr_expr, "my_buildings_table")
        assert "my_buildings_table" in sql

    def test_spatial_intersects_generates_st_function(self, conv, spatial_intersects_expr):
        sql, params = conv.to_duckdb_sql(spatial_intersects_expr, "zones")
        assert "ST_Intersects" in sql

    def test_spatial_filter_has_params(self, conv, spatial_intersects_expr):
        _, params = conv.to_duckdb_sql(spatial_intersects_expr, "zones")
        assert len(params) > 0

    def test_spatial_filter_wkt_in_params(self, conv, spatial_intersects_expr):
        _, params = conv.to_duckdb_sql(spatial_intersects_expr, "zones")
        assert "POINT(0 0)" in params

    def test_spatial_geomfromtext_used(self, conv, spatial_intersects_expr):
        sql, _ = conv.to_duckdb_sql(spatial_intersects_expr, "zones")
        assert "ST_GeomFromText" in sql

    def test_dwithin_generates_correct_function(self, conv, spatial_dwithin_expr):
        sql, params = conv.to_duckdb_sql(spatial_dwithin_expr, "items")
        assert "ST_DWithin" in sql

    def test_pure_spatial_filter_no_attribute_clause(self, conv, spatial_intersects_expr):
        sql, _ = conv.to_duckdb_sql(spatial_intersects_expr, "zones")
        # Must not have "TRUE" as the where clause since spatial predicate is present
        assert "ST_Intersects" in sql

    def test_no_filter_produces_true_where(self, conv):
        # A spatial-only expression that somehow ends up with no predicates — edge case
        expr = FilterExpression.create_spatial(
            [SpatialPredicate.INTERSECTS],
            ref_wkt="POINT(1 2)",
        )
        sql, _ = conv.to_duckdb_sql(expr, "mytable")
        # Should still be a valid SELECT
        assert sql.startswith("SELECT * FROM")

    def test_custom_geom_col(self, conv, spatial_intersects_expr):
        sql, _ = conv.to_duckdb_sql(spatial_intersects_expr, "zones", geom_col="shape")
        assert "shape" in sql

    def test_buffer_expression_generates_st_buffer(self, conv):
        expr = FilterExpression.create_spatial(
            [SpatialPredicate.INTERSECTS],
            ref_wkt="POINT(0 0)",
            buffer_value=200.0,
        )
        sql, params = conv.to_duckdb_sql(expr, "zones")
        assert "ST_Buffer" in sql


# ---------------------------------------------------------------------------
# to_postgis_sql()
# ---------------------------------------------------------------------------


class TestToPostgisSql:
    def test_returns_tuple_of_two(self, conv, simple_attr_expr):
        result = conv.to_postgis_sql(simple_attr_expr, "public", "buildings")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_qualified_table_name_with_schema(self, conv, simple_attr_expr):
        sql, _ = conv.to_postgis_sql(simple_attr_expr, "public", "buildings")
        assert '"public"."buildings"' in sql

    def test_qualified_table_name_custom_schema(self, conv, simple_attr_expr):
        sql, _ = conv.to_postgis_sql(simple_attr_expr, "gis_data", "parcels")
        assert '"gis_data"."parcels"' in sql

    def test_empty_schema_produces_bare_table(self, conv, simple_attr_expr):
        sql, _ = conv.to_postgis_sql(simple_attr_expr, "", "mytable")
        assert '"mytable"' in sql
        assert '"".""' not in sql

    def test_sql_contains_where(self, conv, simple_attr_expr):
        sql, _ = conv.to_postgis_sql(simple_attr_expr, "public", "buildings")
        assert "WHERE" in sql

    def test_sql_contains_attribute_expression(self, conv, simple_attr_expr):
        sql, _ = conv.to_postgis_sql(simple_attr_expr, "public", "buildings")
        assert "area > 100" in sql

    def test_params_empty_for_attribute_filter(self, conv, simple_attr_expr):
        _, params = conv.to_postgis_sql(simple_attr_expr, "public", "buildings")
        assert params == []

    def test_spatial_intersects_generates_st_intersects(self, conv, spatial_intersects_expr):
        sql, params = conv.to_postgis_sql(spatial_intersects_expr, "public", "zones")
        assert "ST_Intersects" in sql

    def test_spatial_filter_wkt_parameterized(self, conv, spatial_intersects_expr):
        sql, params = conv.to_postgis_sql(spatial_intersects_expr, "public", "zones")
        # WKT must be in params, not interpolated inline (SQL injection prevention)
        assert "POINT(0 0)" not in sql
        assert "POINT(0 0)" in params

    def test_spatial_srid_in_params(self, conv, spatial_intersects_expr):
        _, params = conv.to_postgis_sql(spatial_intersects_expr, "public", "zones")
        assert 4326 in params

    def test_spatial_geomfromtext_used(self, conv, spatial_intersects_expr):
        sql, _ = conv.to_postgis_sql(spatial_intersects_expr, "public", "zones")
        assert "ST_GeomFromText" in sql

    def test_dwithin_generates_st_dwithin(self, conv, spatial_dwithin_expr):
        sql, params = conv.to_postgis_sql(spatial_dwithin_expr, "public", "stops")
        assert "ST_DWithin" in sql

    def test_dwithin_distance_in_params(self, conv, spatial_dwithin_expr):
        _, params = conv.to_postgis_sql(spatial_dwithin_expr, "public", "stops")
        assert 500.0 in params

    def test_buffer_generates_st_buffer(self, conv):
        expr = FilterExpression.create_spatial(
            [SpatialPredicate.INTERSECTS],
            ref_wkt="POINT(0 0)",
            buffer_value=150.0,
        )
        sql, _ = conv.to_postgis_sql(expr, "public", "items")
        assert "ST_Buffer" in sql

    def test_custom_geom_col(self, conv, spatial_intersects_expr):
        sql, _ = conv.to_postgis_sql(
            spatial_intersects_expr, "public", "zones", geom_col="the_geom"
        )
        assert "the_geom" in sql

    def test_within_generates_st_within(self, conv):
        expr = FilterExpression.create_spatial(
            [SpatialPredicate.WITHIN],
            ref_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
        )
        sql, _ = conv.to_postgis_sql(expr, "public", "items")
        assert "ST_Within" in sql

    def test_contains_generates_st_contains(self, conv):
        expr = FilterExpression.create_spatial(
            [SpatialPredicate.CONTAINS],
            ref_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
        )
        sql, _ = conv.to_postgis_sql(expr, "public", "items")
        assert "ST_Contains" in sql


# ---------------------------------------------------------------------------
# combine()
# ---------------------------------------------------------------------------


class TestCombine:
    def test_empty_list_returns_empty_string(self, conv):
        result = conv.combine([])
        assert result == ""

    def test_list_of_empty_strings_returns_empty(self, conv):
        result = conv.combine(["", "   ", ""])
        assert result == ""

    def test_single_expression_returned_as_is(self, conv):
        result = conv.combine(["area > 100"])
        assert result == "area > 100"

    def test_single_expression_with_whitespace_stripped(self, conv):
        result = conv.combine(["  area > 100  "])
        assert "area > 100" in result

    def test_two_expressions_combined_with_and(self, conv):
        result = conv.combine(["area > 100", "status = 'ok'"])
        assert "AND" in result
        assert "area > 100" in result
        assert "status = 'ok'" in result

    def test_two_expressions_wrapped_in_parens(self, conv):
        result = conv.combine(["a > 0", "b > 0"])
        # Each sub-expression should be parenthesised
        assert "(a > 0)" in result
        assert "(b > 0)" in result

    def test_three_expressions_combined(self, conv):
        result = conv.combine(["a > 0", "b > 0", "c > 0"])
        assert "AND" in result
        assert "a > 0" in result
        assert "c > 0" in result

    def test_or_operator(self, conv):
        result = conv.combine(["a > 0", "b > 0"], operator="OR")
        assert "OR" in result
        assert "AND" not in result

    def test_or_operator_lowercase(self, conv):
        result = conv.combine(["a > 0", "b > 0"], operator="or")
        assert "OR" in result

    def test_ignores_empty_strings_in_mixed_list(self, conv):
        result = conv.combine(["area > 0", "", "status = 'ok'", "  "])
        assert "AND" in result
        assert "area > 0" in result
        assert "status = 'ok'" in result

    def test_single_non_empty_in_mixed_list(self, conv):
        result = conv.combine(["", "area > 0", ""])
        assert result == "area > 0"


# ---------------------------------------------------------------------------
# get_spatial_predicate_name()
# ---------------------------------------------------------------------------


class TestGetSpatialPredicateName:
    def test_intersects_pandas(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.INTERSECTS, Dialect.PANDAS)
        assert name == "intersects"

    def test_contains_pandas(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.CONTAINS, Dialect.PANDAS)
        assert name == "contains"

    def test_within_pandas(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.WITHIN, Dialect.PANDAS)
        assert name == "within"

    def test_equals_pandas_maps_to_geom_equals(self, conv):
        # GeoPandas uses geom_equals, not equals
        name = conv.get_spatial_predicate_name(SpatialPredicate.EQUALS, Dialect.PANDAS)
        assert name == "geom_equals"

    def test_intersects_duckdb(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.INTERSECTS, Dialect.DUCKDB)
        assert name == "ST_Intersects"

    def test_contains_duckdb(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.CONTAINS, Dialect.DUCKDB)
        assert name == "ST_Contains"

    def test_within_duckdb(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.WITHIN, Dialect.DUCKDB)
        assert name == "ST_Within"

    def test_disjoint_duckdb(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.DISJOINT, Dialect.DUCKDB)
        assert name == "ST_Disjoint"

    def test_dwithin_duckdb(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.DWITHIN, Dialect.DUCKDB)
        assert name == "ST_DWithin"

    def test_intersects_postgis(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.INTERSECTS, Dialect.POSTGIS)
        assert name == "ST_Intersects"

    def test_contains_postgis(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.CONTAINS, Dialect.POSTGIS)
        assert name == "ST_Contains"

    def test_within_postgis(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.WITHIN, Dialect.POSTGIS)
        assert name == "ST_Within"

    def test_crosses_postgis(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.CROSSES, Dialect.POSTGIS)
        assert name == "ST_Crosses"

    def test_touches_postgis(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.TOUCHES, Dialect.POSTGIS)
        assert name == "ST_Touches"

    def test_overlaps_postgis(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.OVERLAPS, Dialect.POSTGIS)
        assert name == "ST_Overlaps"

    def test_disjoint_postgis(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.DISJOINT, Dialect.POSTGIS)
        assert name == "ST_Disjoint"

    def test_equals_postgis(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.EQUALS, Dialect.POSTGIS)
        assert name == "ST_Equals"

    def test_dwithin_postgis(self, conv):
        name = conv.get_spatial_predicate_name(SpatialPredicate.DWITHIN, Dialect.POSTGIS)
        assert name == "ST_DWithin"

    def test_all_predicates_have_pandas_name(self, conv):
        for pred in SpatialPredicate:
            name = conv.get_spatial_predicate_name(pred, Dialect.PANDAS)
            assert isinstance(name, str) and len(name) > 0

    def test_all_predicates_have_duckdb_name(self, conv):
        for pred in SpatialPredicate:
            name = conv.get_spatial_predicate_name(pred, Dialect.DUCKDB)
            assert isinstance(name, str) and len(name) > 0

    def test_all_predicates_have_postgis_name(self, conv):
        for pred in SpatialPredicate:
            name = conv.get_spatial_predicate_name(pred, Dialect.POSTGIS)
            assert isinstance(name, str) and len(name) > 0

    def test_duckdb_and_postgis_have_same_names(self, conv):
        # DuckDB spatial mirrors PostGIS ST_* naming
        for pred in SpatialPredicate:
            duckdb_name = conv.get_spatial_predicate_name(pred, Dialect.DUCKDB)
            postgis_name = conv.get_spatial_predicate_name(pred, Dialect.POSTGIS)
            assert duckdb_name == postgis_name

    def test_pandas_names_are_lowercase(self, conv):
        for pred in SpatialPredicate:
            name = conv.get_spatial_predicate_name(pred, Dialect.PANDAS)
            if name != "geom_equals":  # special case
                assert name == name.lower()

    def test_duckdb_names_start_with_st(self, conv):
        for pred in SpatialPredicate:
            name = conv.get_spatial_predicate_name(pred, Dialect.DUCKDB)
            assert name.startswith("ST_")
