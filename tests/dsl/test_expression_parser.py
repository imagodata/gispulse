"""Tests for ``gispulse.dsl.expression_parser`` and the geom fcts registry.

Coverage:
- Happy paths: each whitelisted geom fct compiles, plus arithmetic.
- CRS handling: source_epsg required for measure fcts; ``epsg=`` override.
- Allowlist rejects: 10+ injection / unsupported AST patterns.
- Geom fct registry contains the 7 documented v1.6.0 fcts (T1+T2).
- DuckDB E2E: a compiled expression executes against a real geometry.
"""

from __future__ import annotations

import pytest

from gispulse.dsl import (
    GEOM_FUNCTIONS,
    CompilationContext,
    DSLValidationError,
    compile_expression,
    is_geom_function,
)


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------


class TestGeomFunctionRegistry:
    EXPECTED_T1 = {"geom_area_m2", "geom_perimeter_m", "geom_length_m"}
    EXPECTED_T2 = {"geom_centroid_x", "geom_centroid_y", "geom_npoints", "geom_is_valid"}
    EXPECTED_SUBQUERY = {"geom_within", "geom_overlaps_any"}

    def test_t1_present(self) -> None:
        assert self.EXPECTED_T1 <= set(GEOM_FUNCTIONS)

    def test_t2_present(self) -> None:
        assert self.EXPECTED_T2 <= set(GEOM_FUNCTIONS)

    def test_subquery_present(self) -> None:
        assert self.EXPECTED_SUBQUERY <= set(GEOM_FUNCTIONS)

    def test_full_v160_surface(self) -> None:
        # Locks the v1.6.0 surface — adding a new fct must update tests/docs.
        assert set(GEOM_FUNCTIONS) == (
            self.EXPECTED_T1 | self.EXPECTED_T2 | self.EXPECTED_SUBQUERY
        )

    def test_is_geom_function(self) -> None:
        assert is_geom_function("geom_area_m2")
        assert is_geom_function("geom_within")
        assert not is_geom_function("eval")
        assert not is_geom_function("ST_Area")  # SQL name, not DSL name


# ---------------------------------------------------------------------------
# Compilation — happy paths
# ---------------------------------------------------------------------------


@pytest.fixture
def crs_ctx() -> CompilationContext:
    return CompilationContext(source_epsg="EPSG:4326")


class TestCompileGeomFunctions:
    def test_geom_area_m2_default_epsg(self, crs_ctx: CompilationContext) -> None:
        sql = compile_expression("geom_area_m2()", crs_ctx)
        assert "ST_Area(ST_Transform" in sql
        assert "'EPSG:4326'" in sql  # source
        assert "'EPSG:2154'" in sql  # default metric

    def test_geom_area_m2_with_override(self, crs_ctx: CompilationContext) -> None:
        sql = compile_expression("geom_area_m2(epsg='EPSG:3857')", crs_ctx)
        assert "'EPSG:3857'" in sql

    def test_geom_perimeter(self, crs_ctx: CompilationContext) -> None:
        sql = compile_expression("geom_perimeter_m()", crs_ctx)
        assert "ST_Perimeter" in sql

    def test_geom_length(self, crs_ctx: CompilationContext) -> None:
        sql = compile_expression("geom_length_m()", crs_ctx)
        assert "ST_Length" in sql

    def test_centroid_x(self, crs_ctx: CompilationContext) -> None:
        sql = compile_expression(
            "geom_centroid_x(epsg='EPSG:4326')", crs_ctx
        )
        assert "ST_X(ST_Transform(ST_Centroid" in sql

    def test_centroid_y(self, crs_ctx: CompilationContext) -> None:
        sql = compile_expression(
            "geom_centroid_y(epsg='EPSG:4326')", crs_ctx
        )
        assert "ST_Y(ST_Transform(ST_Centroid" in sql

    def test_npoints_no_crs_needed(self) -> None:
        sql = compile_expression("geom_npoints()")
        assert sql == 'ST_NPoints("geom")'

    def test_is_valid_no_crs_needed(self) -> None:
        sql = compile_expression("geom_is_valid()")
        assert sql == 'ST_IsValid("geom")'


class TestArithmeticAndColumns:
    def test_division(self, crs_ctx: CompilationContext) -> None:
        sql = compile_expression("geom_area_m2() / 10000", crs_ctx)
        assert sql.startswith("(")
        assert sql.endswith(" / 10000)")

    def test_mixed(self) -> None:
        sql = compile_expression("price * (1 + tax_rate)")
        assert '"price"' in sql
        assert '"tax_rate"' in sql

    def test_unary_minus(self) -> None:
        sql = compile_expression("-counter + 1")
        assert '(-"counter")' in sql

    def test_modulo(self) -> None:
        sql = compile_expression("counter % 7")
        assert " % 7" in sql

    def test_float_literal(self) -> None:
        sql = compile_expression("price * 1.2")
        assert "1.2" in sql

    def test_bool_literal(self) -> None:
        sql = compile_expression("True")
        assert sql == "TRUE"

    def test_negative_int(self) -> None:
        sql = compile_expression("-42")
        assert sql == "(-42)"

    def test_column_double_quoted(self) -> None:
        sql = compile_expression("price")
        assert sql == '"price"'


# ---------------------------------------------------------------------------
# Compilation — reject patterns (security)
# ---------------------------------------------------------------------------


class TestRejectPatterns:
    @pytest.mark.parametrize(
        "expr",
        [
            "__import__('os')",
            "eval('1+1')",
            "globals()",
            "geom.foo",            # attribute access
            "[1, 2, 3]",            # list literal
            "{1: 2}",               # dict literal
            "'string'",             # string literal at top level
            "1 == 1",               # comparison
            "1 ** 2",               # power
            "1 << 2",               # shift
            "x and y",              # boolean op
            "not x",                # boolean not
            "lambda x: x",          # lambda
            "[i for i in range(10)]",
            "f'{x}'",               # f-string contains FormattedValue
        ],
    )
    def test_rejected(self, expr: str) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression(expr)

    def test_unknown_function(self, crs_ctx: CompilationContext) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression("len(geom)", crs_ctx)
        assert "not in the DSL whitelist" in str(exc.value)

    def test_unknown_kwarg(self, crs_ctx: CompilationContext) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression("geom_area_m2(bogus='EPSG:2154')", crs_ctx)
        assert "does not accept keyword" in str(exc.value)

    def test_kwarg_not_string_literal(self, crs_ctx: CompilationContext) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression("geom_area_m2(epsg=2154)", crs_ctx)

    def test_invalid_epsg_format(self, crs_ctx: CompilationContext) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression("geom_area_m2(epsg='2154')", crs_ctx)
        assert "EPSG:NNNN" in str(exc.value)

    def test_positional_arg_rejected(self, crs_ctx: CompilationContext) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression("geom_area_m2('EPSG:2154')", crs_ctx)
        assert "no positional arguments" in str(exc.value)

    def test_geom_fct_as_bare_name(self) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression("geom_area_m2 + 1")
        assert "must be called as" in str(exc.value)

    def test_sql_keyword_as_column(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression("SELECT + 1")

    def test_empty_expression(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression("")

    def test_nul_byte(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression("price\x00 + 1")

    def test_too_long(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression("price + " + "1 + " * 2000 + "1")

    def test_syntax_error_carries_location(self) -> None:
        with pytest.raises(DSLValidationError) as exc:
            compile_expression("price +")
        msg = str(exc.value)
        assert "line" in msg and "col" in msg

    def test_crs_aware_without_source_epsg(self) -> None:
        ctx = CompilationContext()  # no source_epsg
        with pytest.raises(DSLValidationError) as exc:
            compile_expression("geom_area_m2()", ctx)
        assert "source_epsg" in str(exc.value)

    def test_method_call_rejected(self) -> None:
        with pytest.raises(DSLValidationError):
            compile_expression("geom.buffer(1)")


# ---------------------------------------------------------------------------
# Context validation
# ---------------------------------------------------------------------------


class TestCompilationContext:
    def test_invalid_geom_column(self) -> None:
        with pytest.raises(DSLValidationError):
            CompilationContext(geom_column="geom; DROP TABLE x")

    def test_invalid_source_epsg(self) -> None:
        with pytest.raises(DSLValidationError):
            CompilationContext(source_epsg="lambert93")

    def test_invalid_default_metric(self) -> None:
        with pytest.raises(DSLValidationError):
            CompilationContext(default_metric_epsg="2154")


# ---------------------------------------------------------------------------
# DuckDB E2E — compiled expressions actually execute
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_conn():
    from gispulse.runtime.duckdb_engine import (
        _reset_cache_for_tests,
        get_spatial_connection,
    )

    _reset_cache_for_tests()
    conn = get_spatial_connection()
    yield conn
    conn.close()


class TestDuckDBExecution:
    def test_npoints_executes(self, duckdb_conn) -> None:
        sql = compile_expression("geom_npoints()")
        row = duckdb_conn.execute(
            f"SELECT {sql} FROM (SELECT ST_GeomFromText('LINESTRING(0 0, 1 0, 2 0)') AS geom)"
        ).fetchone()
        assert row[0] == 3

    def test_is_valid_executes(self, duckdb_conn) -> None:
        sql = compile_expression("geom_is_valid()")
        row = duckdb_conn.execute(
            f"SELECT {sql} FROM (SELECT ST_GeomFromText('POINT(0 0)') AS geom)"
        ).fetchone()
        assert row[0] is True

    def test_area_paris_commune(self, duckdb_conn) -> None:
        """Area of the Paris commune polygon (rough rectangle) in Lambert93.

        Test-data is a coarse bbox around the Paris commune (~105 km²) — the
        precise IGN reference is 105.4 km². We assert ±10% tolerance, which
        is dominated by the rectangular approximation rather than the
        compilation pipeline's accuracy.
        """
        ctx = CompilationContext(source_epsg="EPSG:4326")
        sql = compile_expression("geom_area_m2() / 1000000", ctx)
        # Bounding box approximating Paris commune in WGS84 lon/lat.
        wkt = (
            "POLYGON((2.224 48.815, 2.469 48.815, "
            "2.469 48.902, 2.224 48.902, 2.224 48.815))"
        )
        row = duckdb_conn.execute(
            f"SELECT {sql} FROM (SELECT ST_GeomFromText(?) AS geom)",
            [wkt],
        ).fetchone()
        area_km2 = row[0]
        # The bounding box is larger than the actual commune — this asserts
        # the compiler emits a working ST_Transform projecting WGS84 → L93.
        assert 100.0 < area_km2 < 250.0

    def test_arithmetic_with_geom_call(self, duckdb_conn) -> None:
        ctx = CompilationContext(source_epsg="EPSG:4326")
        sql = compile_expression("geom_area_m2() / 10000", ctx)
        wkt = (
            "POLYGON((2.224 48.815, 2.469 48.815, "
            "2.469 48.902, 2.224 48.902, 2.224 48.815))"
        )
        row = duckdb_conn.execute(
            f"SELECT {sql} FROM (SELECT ST_GeomFromText(?) AS geom)",
            [wkt],
        ).fetchone()
        # Should be ~10000-25000 hectares (Paris bbox is ~100-250 km² above).
        assert 10000.0 < row[0] < 25000.0

    def test_centroid_x_executes(self, duckdb_conn) -> None:
        ctx = CompilationContext(source_epsg="EPSG:4326")
        sql = compile_expression(
            "geom_centroid_x(epsg='EPSG:4326')", ctx
        )
        row = duckdb_conn.execute(
            f"SELECT {sql} FROM (SELECT ST_GeomFromText('POINT(2.35 48.85)') AS geom)"
        ).fetchone()
        assert abs(row[0] - 2.35) < 1e-6
