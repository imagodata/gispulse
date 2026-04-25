"""Unit tests for core/filter/ — 7 modules covered.

Covers: expression, expression_converter, types, result, cache, chain, service.
"""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# core/filter/expression.py
# ---------------------------------------------------------------------------


class TestFilterExpression:
    def test_create_simple(self):
        from core.filter.expression import Dialect, FilterExpression

        expr = FilterExpression.create("area > 100")
        assert expr.raw == "area > 100"
        assert expr.dialect == Dialect.PANDAS
        assert not expr.is_spatial
        assert expr.is_simple

    def test_create_empty_raises(self):
        from core.filter.expression import FilterExpression

        with pytest.raises(ValueError, match="empty"):
            FilterExpression.create("")

    def test_create_whitespace_raises(self):
        from core.filter.expression import FilterExpression

        with pytest.raises(ValueError):
            FilterExpression("   ")

    def test_create_negative_buffer_raises(self):
        from core.filter.expression import FilterExpression

        with pytest.raises(ValueError, match="negative"):
            FilterExpression.create("area > 0", buffer_value=-1.0)

    def test_create_bad_segments_raises(self):
        from core.filter.expression import FilterExpression

        with pytest.raises(ValueError, match="segments"):
            FilterExpression("area > 0", buffer_segments=0)

    def test_create_spatial_auto_detect(self):
        from core.filter.expression import FilterExpression, SpatialPredicate

        expr = FilterExpression.create("intersects(geom, ref)")
        assert expr.is_spatial
        assert SpatialPredicate.INTERSECTS in expr.spatial_predicates

    def test_create_spatial_factory(self):
        from core.filter.expression import FilterExpression, SpatialPredicate

        expr = FilterExpression.create_spatial(
            [SpatialPredicate.WITHIN],
            buffer_value=50.0,
            ref_wkt="POINT(1 2)",
        )
        assert expr.is_spatial
        assert expr.has_buffer
        assert expr.buffer_value == 50.0
        assert "within" in expr.raw
        assert "buffer" in expr.raw

    def test_create_spatial_no_buffer(self):
        from core.filter.expression import FilterExpression, SpatialPredicate

        expr = FilterExpression.create_spatial([SpatialPredicate.CONTAINS])
        assert not expr.has_buffer
        assert expr.buffer_value is None

    def test_with_sql(self):
        from core.filter.expression import FilterExpression

        expr = FilterExpression.create("area > 0")
        updated = expr.with_sql("SELECT * FROM t WHERE area > 0")
        assert updated.sql == "SELECT * FROM t WHERE area > 0"
        assert updated.raw == expr.raw  # immutable

    def test_with_buffer(self):
        from core.filter.expression import FilterExpression

        expr = FilterExpression.create("area > 0")
        buffered = expr.with_buffer(100.0, segments=8)
        assert buffered.buffer_value == 100.0
        assert buffered.buffer_segments == 8
        assert buffered.is_spatial

    def test_with_dialect(self):
        from core.filter.expression import Dialect, FilterExpression

        expr = FilterExpression.create("area > 0")
        pg_expr = expr.with_dialect(Dialect.POSTGIS)
        assert pg_expr.dialect == Dialect.POSTGIS
        assert expr.dialect == Dialect.PANDAS  # original unchanged

    def test_predicate_names(self):
        from core.filter.expression import FilterExpression, SpatialPredicate

        expr = FilterExpression.create_spatial(
            [SpatialPredicate.INTERSECTS, SpatialPredicate.WITHIN]
        )
        assert "intersects" in expr.predicate_names
        assert "within" in expr.predicate_names

    def test_str_representation(self):
        from core.filter.expression import FilterExpression

        expr = FilterExpression.create("area > 100")
        s = str(expr)
        assert "FilterExpression" in s
        assert "area > 100" in s

    def test_is_simple_false_for_spatial(self):
        from core.filter.expression import FilterExpression, SpatialPredicate

        expr = FilterExpression.create_spatial([SpatialPredicate.INTERSECTS])
        assert not expr.is_simple


# ---------------------------------------------------------------------------
# core/filter/expression_converter.py
# ---------------------------------------------------------------------------


class TestExpressionConverter:
    def setup_method(self):
        from core.filter.expression_converter import ExpressionConverter

        self.conv = ExpressionConverter()

    def test_validate_valid(self):
        ok, errors = self.conv.validate("area > 100")
        assert ok
        assert errors == []

    def test_validate_empty(self):
        ok, errors = self.conv.validate("")
        assert not ok
        assert errors

    def test_validate_whitespace(self):
        ok, errors = self.conv.validate("   ")
        assert not ok

    def test_validate_dangerous_keyword_drop(self):
        ok, errors = self.conv.validate("DROP TABLE foo")
        assert not ok
        assert any("DROP" in e or "disallowed" in e for e in errors)

    def test_validate_dangerous_keyword_delete(self):
        ok, errors = self.conv.validate("DELETE FROM foo")
        assert not ok

    def test_validate_unbalanced_parens_open(self):
        ok, errors = self.conv.validate("(area > 0")
        assert not ok
        assert any("paren" in e.lower() for e in errors)

    def test_validate_unbalanced_parens_close(self):
        ok, errors = self.conv.validate("area > 0)")
        assert not ok

    def test_to_pandas_simple(self):
        from core.filter.expression import FilterExpression

        expr = FilterExpression.create("area > 100")
        result = self.conv.to_pandas(expr)
        assert result == "area > 100"

    def test_to_pandas_pure_spatial_empty(self):
        from core.filter.expression import FilterExpression, SpatialPredicate

        expr = FilterExpression.create_spatial([SpatialPredicate.INTERSECTS])
        result = self.conv.to_pandas(expr)
        assert result == ""

    def test_to_duckdb_sql_simple(self):
        from core.filter.expression import FilterExpression

        expr = FilterExpression.create("area > 100")
        sql, params = self.conv.to_duckdb_sql(expr, "parcels")
        assert "SELECT * FROM parcels WHERE" in sql
        assert "area > 100" in sql

    def test_to_duckdb_sql_no_filter(self):
        from core.filter.expression import FilterExpression, SpatialPredicate

        expr = FilterExpression.create_spatial(
            [SpatialPredicate.INTERSECTS],
            ref_wkt="POINT(0 0)",
        )
        sql, params = self.conv.to_duckdb_sql(expr, "parcels")
        assert "ST_Intersects" in sql

    def test_to_postgis_sql_simple(self):
        from core.filter.expression import FilterExpression

        expr = FilterExpression.create("status = 'active'")
        sql, params = self.conv.to_postgis_sql(expr, "public", "buildings")
        assert '"public"."buildings"' in sql
        assert "status = 'active'" in sql

    def test_to_postgis_sql_no_schema(self):
        from core.filter.expression import FilterExpression

        expr = FilterExpression.create("val > 0")
        sql, params = self.conv.to_postgis_sql(expr, "", "mytable")
        assert '"mytable"' in sql

    def test_to_postgis_sql_spatial_intersects(self):
        from core.filter.expression import FilterExpression, SpatialPredicate

        expr = FilterExpression.create_spatial(
            [SpatialPredicate.INTERSECTS],
            ref_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
            ref_srid=4326,
        )
        sql, params = self.conv.to_postgis_sql(expr, "public", "zones")
        assert "ST_Intersects" in sql
        assert "ST_GeomFromText" in sql
        assert params  # WKT should be in params, not interpolated in SQL

    def test_to_postgis_sql_dwithin(self):
        from core.filter.expression import FilterExpression, SpatialPredicate

        expr = FilterExpression.create_spatial(
            [SpatialPredicate.DWITHIN],
            ref_wkt="POINT(0 0)",
            buffer_value=500.0,
        )
        sql, params = self.conv.to_postgis_sql(expr, "public", "items")
        assert "ST_DWithin" in sql

    def test_to_postgis_sql_with_buffer(self):
        from core.filter.expression import FilterExpression, SpatialPredicate

        expr = FilterExpression.create_spatial(
            [SpatialPredicate.INTERSECTS],
            ref_wkt="POINT(0 0)",
            buffer_value=100.0,
        )
        sql, params = self.conv.to_postgis_sql(expr, "public", "items")
        assert "ST_Buffer" in sql

    def test_combine_empty(self):
        result = self.conv.combine([])
        assert result == ""

    def test_combine_single(self):
        result = self.conv.combine(["area > 0"])
        assert result == "area > 0"

    def test_combine_and(self):
        result = self.conv.combine(["area > 0", "status = 'ok'"], operator="AND")
        assert "AND" in result
        assert "area > 0" in result

    def test_combine_or(self):
        result = self.conv.combine(["a > 0", "b > 0"], operator="OR")
        assert "OR" in result

    def test_combine_ignores_empty_strings(self):
        result = self.conv.combine(["area > 0", "", "  "])
        assert result == "area > 0"

    def test_get_spatial_predicate_name_pandas(self):
        from core.filter.expression import Dialect, SpatialPredicate

        name = self.conv.get_spatial_predicate_name(SpatialPredicate.INTERSECTS, Dialect.PANDAS)
        assert name == "intersects"

    def test_get_spatial_predicate_name_duckdb(self):
        from core.filter.expression import Dialect, SpatialPredicate

        name = self.conv.get_spatial_predicate_name(SpatialPredicate.CONTAINS, Dialect.DUCKDB)
        assert name == "ST_Contains"

    def test_get_spatial_predicate_name_postgis(self):
        from core.filter.expression import Dialect, SpatialPredicate

        name = self.conv.get_spatial_predicate_name(SpatialPredicate.WITHIN, Dialect.POSTGIS)
        assert name == "ST_Within"


# ---------------------------------------------------------------------------
# core/filter/types.py
# ---------------------------------------------------------------------------


class TestFilterTypes:
    def test_filter_default_priority(self):
        from core.filter.types import Filter, FilterType

        f = Filter(
            filter_type=FilterType.FIELD_CONDITION,
            expression="area > 0",
            layer_name="parcels",
        )
        assert f.priority == 50

    def test_filter_materialized_view_priority(self):
        from core.filter.types import Filter, FilterType

        f = Filter(FilterType.MATERIALIZED_VIEW, "SELECT 1", "mv_layer")
        assert f.priority == 100

    def test_filter_validate_ok(self):
        from core.filter.types import Filter, FilterType

        f = Filter(FilterType.FIELD_CONDITION, "x > 0", "layer")
        ok, err = f.validate()
        assert ok
        assert err is None

    def test_filter_validate_empty_expression(self):
        from core.filter.types import Filter, FilterType

        f = Filter(FilterType.FIELD_CONDITION, "  ", "layer")
        ok, err = f.validate()
        assert not ok
        assert "empty" in err.lower()

    def test_filter_validate_empty_layer(self):
        from core.filter.types import Filter, FilterType

        f = Filter(FilterType.FIELD_CONDITION, "x > 0", "")
        ok, err = f.validate()
        assert not ok
        assert "layer" in err.lower()

    def test_filter_validate_bad_priority(self):
        from core.filter.types import Filter, FilterType

        f = Filter(FilterType.FIELD_CONDITION, "x > 0", "layer", priority=0)
        ok, err = f.validate()
        assert not ok

    def test_filter_validate_bad_operator(self):
        from core.filter.types import Filter, FilterType

        f = Filter(FilterType.FIELD_CONDITION, "x > 0", "layer", combine_operator="XOR")
        ok, err = f.validate()
        assert not ok

    def test_filter_hash(self):
        from core.filter.types import Filter, FilterType

        f1 = Filter(FilterType.FIELD_CONDITION, "x > 0", "layer", priority=50)
        f2 = Filter(FilterType.FIELD_CONDITION, "x > 0", "layer", priority=50)
        assert hash(f1) == hash(f2)

    def test_combination_strategy_values(self):
        from core.filter.types import CombinationStrategy

        assert CombinationStrategy.PRIORITY_AND.value == "priority_and"
        assert CombinationStrategy.REPLACE.value == "replace"

    def test_filter_type_enum(self):
        from core.filter.types import FilterType

        assert FilterType.SPATIAL_SELECTION.value == "spatial_selection"
        assert FilterType.BBOX_FILTER.value == "bbox_filter"


# ---------------------------------------------------------------------------
# core/filter/result.py
# ---------------------------------------------------------------------------


class TestFilterResult:
    def _make_gdf(self, n=3):
        import geopandas as gpd
        from shapely.geometry import Point

        return gpd.GeoDataFrame(
            {"val": list(range(n))},
            geometry=[Point(i, i) for i in range(n)],
            crs="EPSG:4326",
        )

    def test_success_factory(self):
        from core.filter.result import FilterResult, FilterStatus

        gdf = self._make_gdf(3)
        result = FilterResult.success(gdf, "ds::layer", "val > 0", execution_time_ms=5.0)
        assert result.status == FilterStatus.SUCCESS
        assert result.feature_count == 3
        assert result.is_success
        assert not result.is_empty

    def test_success_no_matches(self):
        from core.filter.result import FilterResult, FilterStatus

        gdf = self._make_gdf(0)
        result = FilterResult.success(gdf, "ds::layer", "val > 99")
        assert result.status == FilterStatus.NO_MATCHES
        assert result.is_empty
        assert result.is_success  # NO_MATCHES is still considered success

    def test_error_factory(self):
        from core.filter.result import FilterResult, FilterStatus

        result = FilterResult.error("ds::layer", "bad expr", "syntax error")
        assert result.status == FilterStatus.ERROR
        assert result.has_error
        assert result.error_message == "syntax error"
        assert result.feature_count == 0

    def test_cancelled_factory(self):
        from core.filter.result import FilterResult, FilterStatus

        result = FilterResult.cancelled("ds::layer", "val > 0")
        assert result.status == FilterStatus.CANCELLED
        assert result.was_cancelled

    def test_from_cache_factory(self):
        from core.filter.result import FilterResult

        gdf = self._make_gdf(2)
        result = FilterResult.from_cache(gdf, "ds::layer", "val > 0", original_execution_time_ms=10.0)
        assert result.is_cached
        assert result.feature_count == 2

    def test_partial_factory(self):
        from core.filter.result import FilterResult, FilterStatus

        gdf = self._make_gdf(1)
        result = FilterResult.partial(gdf, "ds::layer", "val > 0", error_message="partial failure")
        assert result.status == FilterStatus.PARTIAL
        assert result.is_partial
        assert result.error_message == "partial failure"

    def test_str_success(self):
        from core.filter.result import FilterResult

        gdf = self._make_gdf(5)
        result = FilterResult.success(gdf, "ds::layer", "val > 0", execution_time_ms=12.3)
        s = str(result)
        assert "5" in s
        assert "12.3" in s

    def test_str_error(self):
        from core.filter.result import FilterResult

        result = FilterResult.error("ds::layer", "expr", "bad input")
        s = str(result)
        assert "ERROR" in s
        assert "bad input" in s

    def test_str_cancelled(self):
        from core.filter.result import FilterResult

        result = FilterResult.cancelled("ds::layer", "expr")
        s = str(result)
        assert "CANCELLED" in s

    def test_bbox_computed(self):
        from core.filter.result import FilterResult

        gdf = self._make_gdf(2)
        result = FilterResult.success(gdf, "ds::layer", "val > 0")
        assert result.bbox is not None
        assert len(result.bbox) == 4

    def test_bbox_none_when_empty(self):
        from core.filter.result import FilterResult

        gdf = self._make_gdf(0)
        result = FilterResult.success(gdf, "ds::layer", "val > 0")
        assert result.bbox is None


# ---------------------------------------------------------------------------
# core/filter/cache.py
# ---------------------------------------------------------------------------


class TestFilterCache:
    def _make_result(self, layer_key="ds::layer"):
        from core.filter.result import FilterResult

        return FilterResult.error(layer_key, "val > 0", "test result")

    def test_get_miss(self):
        from core.filter.cache import FilterCache

        cache = FilterCache()
        assert cache.get("nonexistent") is None

    def test_set_and_get(self):
        from core.filter.cache import FilterCache

        cache = FilterCache()
        result = self._make_result()
        cache.set("key1", result)
        retrieved = cache.get("key1")
        assert retrieved is result

    def test_ttl_expiry(self):
        from core.filter.cache import FilterCache

        cache = FilterCache(default_ttl_seconds=0.01)
        result = self._make_result()
        cache.set("key1", result)
        time.sleep(0.05)
        assert cache.get("key1") is None

    def test_lru_eviction(self):
        from core.filter.cache import FilterCache

        cache = FilterCache(max_size=2)
        r1 = self._make_result("l1")
        r2 = self._make_result("l2")
        r3 = self._make_result("l3")
        cache.set("k1", r1)
        cache.set("k2", r2)
        cache.set("k3", r3)  # should evict k1
        assert cache.get("k1") is None
        assert cache.get("k2") is not None
        assert cache.get("k3") is not None

    def test_invalidate_layer(self):
        from core.filter.cache import FilterCache

        cache = FilterCache()
        r1 = self._make_result("layer_a")
        r2 = self._make_result("layer_b")
        cache.set("k1", r1)
        cache.set("k2", r2)
        removed = cache.invalidate_layer("layer_a")
        assert removed == 1
        assert cache.get("k1") is None
        assert cache.get("k2") is not None

    def test_clear(self):
        from core.filter.cache import FilterCache

        cache = FilterCache()
        cache.set("k1", self._make_result())
        cache.set("k2", self._make_result())
        count = cache.clear()
        assert count == 2
        assert cache.get("k1") is None

    def test_stats(self):
        from core.filter.cache import FilterCache

        cache = FilterCache(max_size=10)
        result = self._make_result()
        cache.set("k1", result)
        cache.get("k1")  # hit
        cache.get("k_miss")  # miss
        stats = cache.get_stats()
        assert stats.hits == 1
        assert stats.misses == 1
        assert stats.size == 1
        assert stats.hit_rate == 0.5

    def test_stats_utilization(self):
        from core.filter.cache import FilterCache

        cache = FilterCache(max_size=10)
        cache.set("k", self._make_result())
        stats = cache.get_stats()
        assert stats.utilization == pytest.approx(0.1)

    def test_make_key_deterministic(self):
        from core.filter.cache import FilterCache

        k1 = FilterCache.make_key("layer", "expr", extra="x")
        k2 = FilterCache.make_key("layer", "expr", extra="x")
        assert k1 == k2

    def test_make_key_different_inputs(self):
        from core.filter.cache import FilterCache

        k1 = FilterCache.make_key("layer_a", "expr")
        k2 = FilterCache.make_key("layer_b", "expr")
        assert k1 != k2

    def test_get_or_compute_hit(self):
        from core.filter.cache import FilterCache

        cache = FilterCache()
        result = self._make_result()
        cache.set("k", result)
        called = []

        def compute():
            called.append(1)
            return self._make_result()

        got = cache.get_or_compute("k", compute)
        assert got is result
        assert called == []  # not called

    def test_get_or_compute_miss(self):
        from core.filter.cache import FilterCache

        cache = FilterCache()
        result = self._make_result()

        def compute():
            return result

        got = cache.get_or_compute("k_new", compute)
        assert got is result
        assert cache.get("k_new") is result


class TestNullCache:
    def test_always_miss(self):
        from core.filter.cache import NullCache
        from core.filter.result import FilterResult

        cache = NullCache()
        cache.set("k", FilterResult.error("l", "e", "err"))
        assert cache.get("k") is None

    def test_get_or_compute_always_calls(self):
        from core.filter.cache import NullCache
        from core.filter.result import FilterResult

        cache = NullCache()
        expected = FilterResult.error("l", "e", "err")
        called = []

        def compute():
            called.append(1)
            return expected

        result = cache.get_or_compute("k", compute)
        assert result is expected
        assert len(called) == 1

    def test_stats_empty(self):
        from core.filter.cache import NullCache

        cache = NullCache()
        stats = cache.get_stats()
        assert stats.hits == 0
        assert stats.misses == 0


# ---------------------------------------------------------------------------
# core/filter/chain.py
# ---------------------------------------------------------------------------


class TestFilterChain:
    def _make_filter(self, expr="x > 0", layer="l", ftype=None):
        from core.filter.types import Filter, FilterType

        return Filter(
            filter_type=ftype or FilterType.FIELD_CONDITION,
            expression=expr,
            layer_name=layer,
        )

    def test_empty_chain(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        assert len(chain) == 0
        assert not chain

    def test_add_and_len(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter())
        assert len(chain) == 1
        assert bool(chain)

    def test_add_invalid_filter_returns_false(self):
        from core.filter.chain import FilterChain
        from core.filter.types import Filter, FilterType

        chain = FilterChain("ds::layer")
        bad = Filter(FilterType.FIELD_CONDITION, "", "layer")  # empty expression
        result = chain.add_filter(bad)
        assert result is False
        assert len(chain) == 0

    def test_remove_filter(self):
        from core.filter.chain import FilterChain
        from core.filter.types import FilterType

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter("x > 0"))
        chain.add_filter(self._make_filter("y > 0", ftype=FilterType.SPATIAL_SELECTION))
        removed = chain.remove_filter(FilterType.SPATIAL_SELECTION)
        assert removed == 1
        assert len(chain) == 1

    def test_has_filter_type(self):
        from core.filter.chain import FilterChain
        from core.filter.types import FilterType

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter())
        assert chain.has_filter_type(FilterType.FIELD_CONDITION)
        assert not chain.has_filter_type(FilterType.BBOX_FILTER)

    def test_get_filters_by_type(self):
        from core.filter.chain import FilterChain
        from core.filter.types import FilterType

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter("a > 0"))
        chain.add_filter(self._make_filter("b > 0"))
        filters = chain.get_filters_by_type(FilterType.FIELD_CONDITION)
        assert len(filters) == 2

    def test_build_expression_empty(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        assert chain.build_expression() == ""

    def test_build_expression_single(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter("area > 100"))
        expr = chain.build_expression()
        assert "area > 100" in expr

    def test_build_expression_and(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter("a > 0"))
        chain.add_filter(self._make_filter("b > 0"))
        expr = chain.build_expression()
        assert "AND" in expr

    def test_build_expression_or_strategy(self):
        from core.filter.chain import FilterChain
        from core.filter.types import CombinationStrategy

        chain = FilterChain("ds::layer", CombinationStrategy.PRIORITY_OR)
        chain.add_filter(self._make_filter("a > 0"))
        chain.add_filter(self._make_filter("b > 0"))
        expr = chain.build_expression()
        assert "OR" in expr

    def test_build_expression_replace_strategy(self):
        from core.filter.chain import FilterChain
        from core.filter.types import CombinationStrategy, Filter, FilterType

        chain = FilterChain("ds::layer", CombinationStrategy.REPLACE)
        chain.add_filter(Filter(FilterType.FIELD_CONDITION, "low_prio", "l", priority=10))
        chain.add_filter(Filter(FilterType.SPATIAL_SELECTION, "high_prio", "l", priority=80))
        expr = chain.build_expression()
        assert expr == "high_prio"

    def test_expression_caching(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter("a > 0"))
        expr1 = chain.build_expression()
        expr2 = chain.build_expression()
        assert expr1 == expr2

    def test_cache_cleared_on_add(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter("a > 0"))
        expr1 = chain.build_expression()
        chain.add_filter(self._make_filter("b > 0"))
        expr2 = chain.build_expression()
        assert "a > 0" in expr2
        assert "b > 0" in expr2

    def test_clear(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter())
        chain.clear()
        assert len(chain) == 0
        assert chain.build_expression() == ""

    def test_to_dict(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter("area > 0"))
        d = chain.to_dict()
        assert d["target_layer"] == "ds::layer"
        assert d["filter_count"] == 1
        assert len(d["filters"]) == 1

    def test_from_dict_roundtrip(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter("area > 0"))
        d = chain.to_dict()
        restored = FilterChain.from_dict(d)
        assert len(restored) == 1
        assert restored.target_layer == "ds::layer"
        assert chain.build_expression() == restored.build_expression()

    def test_repr(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter("area > 0"))
        r = repr(chain)
        assert "ds::layer" in r
        assert "area > 0" in r

    def test_repr_empty(self):
        from core.filter.chain import FilterChain

        chain = FilterChain("ds::layer")
        r = repr(chain)
        assert "EMPTY" in r

    def test_replace_existing(self):
        from core.filter.chain import FilterChain
        from core.filter.types import FilterType

        chain = FilterChain("ds::layer")
        chain.add_filter(self._make_filter("x > 0"))
        chain.add_filter(self._make_filter("x > 10"), replace_existing=True)
        filters = chain.get_filters_by_type(FilterType.FIELD_CONDITION)
        assert len(filters) == 1
        assert filters[0].expression == "x > 10"
