"""Tests for core.filter.chain.FilterChain — composable filter combination.

FilterChain combines multiple Filter objects into a single SQL expression,
respecting priority and combination strategy. Bugs silently produce
wrong filters.
"""
from __future__ import annotations


from gispulse.core.filter.chain import FilterChain
from gispulse.core.filter.types import CombinationStrategy, Filter, FilterType


def _filt(
    ft: FilterType = FilterType.FIELD_CONDITION,
    expr: str = "x = 1",
    layer: str = "l",
    priority: int | None = None,
    op: str = "AND",
) -> Filter:
    return Filter(
        filter_type=ft,
        expression=expr,
        layer_name=layer,
        priority=priority,
        combine_operator=op,
    )


# ---------------------------------------------------------------------------
# Construction + defaults
# ---------------------------------------------------------------------------


class TestFilterChainInit:
    def test_default_strategy(self):
        chain = FilterChain("my_layer")
        assert chain.target_layer == "my_layer"
        assert chain.combination_strategy == CombinationStrategy.PRIORITY_AND
        assert chain.filters == []

    def test_custom_strategy(self):
        chain = FilterChain("l", CombinationStrategy.PRIORITY_OR)
        assert chain.combination_strategy == CombinationStrategy.PRIORITY_OR

    def test_bool_empty_is_false(self):
        assert bool(FilterChain("l")) is False

    def test_bool_non_empty_is_true(self):
        chain = FilterChain("l")
        chain.add_filter(_filt())
        assert bool(chain) is True

    def test_len(self):
        chain = FilterChain("l")
        assert len(chain) == 0
        chain.add_filter(_filt())
        assert len(chain) == 1
        chain.add_filter(_filt(ft=FilterType.BBOX_FILTER, expr="bbox_expr"))
        assert len(chain) == 2


# ---------------------------------------------------------------------------
# add_filter / remove_filter / get / has / clear
# ---------------------------------------------------------------------------


class TestAddFilter:
    def test_adds_valid_filter(self):
        chain = FilterChain("l")
        assert chain.add_filter(_filt()) is True
        assert len(chain) == 1

    def test_rejects_invalid_filter(self):
        chain = FilterChain("l")
        # Empty expression → validate() rejects
        bad = Filter(filter_type=FilterType.FIELD_CONDITION, expression="", layer_name="l")
        assert chain.add_filter(bad) is False
        assert len(chain) == 0

    def test_replace_existing_removes_prior_same_type(self):
        chain = FilterChain("l")
        chain.add_filter(_filt(expr="old"))
        chain.add_filter(_filt(expr="new"), replace_existing=True)
        assert len(chain) == 1
        assert chain.filters[0].expression == "new"

    def test_cache_cleared_on_add(self):
        chain = FilterChain("l")
        chain.add_filter(_filt(expr="a = 1"))
        chain.build_expression()  # warms cache
        assert chain._cache
        chain.add_filter(_filt(ft=FilterType.BBOX_FILTER, expr="b = 2"))
        assert chain._cache == {}


class TestRemoveFilter:
    def test_removes_matching_type(self):
        chain = FilterChain("l")
        chain.add_filter(_filt(ft=FilterType.FIELD_CONDITION, expr="a"))
        chain.add_filter(_filt(ft=FilterType.BBOX_FILTER, expr="b"))
        removed = chain.remove_filter(FilterType.FIELD_CONDITION)
        assert removed == 1
        assert len(chain) == 1
        assert chain.filters[0].filter_type == FilterType.BBOX_FILTER

    def test_remove_missing_returns_zero(self):
        chain = FilterChain("l")
        assert chain.remove_filter(FilterType.CUSTOM_EXPRESSION) == 0

    def test_cache_cleared_on_remove(self):
        chain = FilterChain("l")
        chain.add_filter(_filt())
        chain.build_expression()
        assert chain._cache
        chain.remove_filter(FilterType.FIELD_CONDITION)
        assert chain._cache == {}


class TestGetHasClear:
    def test_get_filters_by_type(self):
        chain = FilterChain("l")
        chain.add_filter(_filt(ft=FilterType.FIELD_CONDITION, expr="a"))
        chain.add_filter(_filt(ft=FilterType.FIELD_CONDITION, expr="b"))
        chain.add_filter(_filt(ft=FilterType.BBOX_FILTER, expr="c"))
        found = chain.get_filters_by_type(FilterType.FIELD_CONDITION)
        assert len(found) == 2

    def test_has_filter_type(self):
        chain = FilterChain("l")
        chain.add_filter(_filt())
        assert chain.has_filter_type(FilterType.FIELD_CONDITION) is True
        assert chain.has_filter_type(FilterType.BBOX_FILTER) is False

    def test_clear_removes_all_and_empties_cache(self):
        chain = FilterChain("l")
        chain.add_filter(_filt())
        chain.build_expression()
        chain.clear()
        assert len(chain) == 0
        assert chain._cache == {}


# ---------------------------------------------------------------------------
# build_expression — combination strategies
# ---------------------------------------------------------------------------


class TestBuildExpressionEmpty:
    def test_empty_chain_returns_empty(self):
        assert FilterChain("l").build_expression() == ""

    def test_all_empty_expressions_returns_empty(self):
        chain = FilterChain("l")
        # Add a filter then manually set its expression to whitespace
        f = _filt(expr="   ")
        # Bypass validation by direct append — the chain's build must still
        # return "" when all filters have blank expressions
        chain.filters.append(f)
        assert chain.build_expression() == ""


class TestBuildExpressionPriorityAnd:
    def test_single_filter(self):
        chain = FilterChain("l")
        chain.add_filter(_filt(expr="a = 1"))
        assert chain.build_expression() == "a = 1"

    def test_two_filters_combined_with_and(self):
        chain = FilterChain("l")
        chain.add_filter(_filt(expr="a = 1"))
        chain.add_filter(_filt(ft=FilterType.BBOX_FILTER, expr="b = 2"))
        expr = chain.build_expression()
        assert " AND " in expr
        assert "a = 1" in expr
        assert "b = 2" in expr

    def test_sorted_by_priority_descending(self):
        chain = FilterChain("l")
        # FIELD_CONDITION priority=50, BBOX priority=90 → BBOX first
        chain.add_filter(
            _filt(ft=FilterType.FIELD_CONDITION, expr="x = 1")
        )
        chain.add_filter(
            _filt(ft=FilterType.BBOX_FILTER, expr="bbox_query")
        )
        expr = chain.build_expression()
        # Higher priority comes first in the combined expression
        assert expr.index("bbox_query") < expr.index("x = 1")


class TestBuildExpressionPriorityOr:
    def test_two_filters_combined_with_or(self):
        chain = FilterChain("l", CombinationStrategy.PRIORITY_OR)
        chain.add_filter(_filt(expr="a = 1"))
        chain.add_filter(_filt(ft=FilterType.BBOX_FILTER, expr="b = 2"))
        expr = chain.build_expression()
        assert " OR " in expr
        assert "AND" not in expr


class TestBuildExpressionCustom:
    def test_custom_uses_per_filter_operator(self):
        chain = FilterChain("l", CombinationStrategy.CUSTOM)
        chain.add_filter(_filt(ft=FilterType.BBOX_FILTER, expr="a", op="AND"))
        chain.add_filter(_filt(ft=FilterType.FIELD_CONDITION, expr="b", op="OR"))
        expr = chain.build_expression()
        # Per-filter operator threaded through; second filter's OR used
        assert " OR " in expr


class TestBuildExpressionReplace:
    def test_replace_keeps_only_highest_priority(self):
        chain = FilterChain("l", CombinationStrategy.REPLACE)
        chain.add_filter(_filt(ft=FilterType.FIELD_CONDITION, expr="low_pri"))
        chain.add_filter(_filt(ft=FilterType.BBOX_FILTER, expr="high_pri"))
        # BBOX priority=90 > FIELD_CONDITION priority=50
        assert chain.build_expression() == "high_pri"


class TestExpressionCache:
    def test_cache_hit_returns_same_result(self):
        chain = FilterChain("l")
        chain.add_filter(_filt(expr="a = 1"))
        first = chain.build_expression()
        second = chain.build_expression()
        assert first == second
        assert len(chain._cache) == 1

    def test_different_dialects_cached_separately(self):
        chain = FilterChain("l")
        chain.add_filter(_filt(expr="a = 1"))
        chain.build_expression(dialect="postgis")
        chain.build_expression(dialect="duckdb")
        assert len(chain._cache) == 2


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_to_dict_shape(self):
        chain = FilterChain("my_layer", CombinationStrategy.PRIORITY_OR)
        chain.add_filter(_filt(expr="a = 1"))
        d = chain.to_dict()
        assert d["target_layer"] == "my_layer"
        assert d["strategy"] == "priority_or"
        assert d["filter_count"] == 1
        assert len(d["filters"]) == 1
        assert d["filters"][0]["expression"] == "a = 1"

    def test_from_dict_reconstructs_chain(self):
        original = FilterChain("l", CombinationStrategy.PRIORITY_AND)
        original.add_filter(_filt(expr="x = 1"))
        original.add_filter(
            _filt(ft=FilterType.BBOX_FILTER, expr="bbox_expr")
        )
        serialised = original.to_dict()
        restored = FilterChain.from_dict(serialised)
        assert restored.target_layer == "l"
        assert restored.combination_strategy == CombinationStrategy.PRIORITY_AND
        assert len(restored) == 2

    def test_from_dict_preserves_strategy(self):
        original = FilterChain("l", CombinationStrategy.PRIORITY_OR)
        original.add_filter(_filt(expr="a"))
        restored = FilterChain.from_dict(original.to_dict())
        assert restored.combination_strategy == CombinationStrategy.PRIORITY_OR


# ---------------------------------------------------------------------------
# _optimize_expression
# ---------------------------------------------------------------------------


class TestOptimizeExpression:
    def test_empty_returns_empty(self):
        assert FilterChain._optimize_expression("") == ""

    def test_strips_outer_parens(self):
        assert FilterChain._optimize_expression("(a = 1)") == "a = 1"

    def test_does_not_strip_when_parens_are_not_outermost(self):
        # (a=1) OR (b=2) — the first ( is not matched at end
        expr = "(a=1) OR (b=2)"
        assert FilterChain._optimize_expression(expr) == expr

    def test_strips_nested_outer_parens(self):
        assert FilterChain._optimize_expression("((a = 1))") == "a = 1"


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------


class TestRepr:
    def test_empty_chain_repr(self):
        r = repr(FilterChain("my_layer"))
        assert "my_layer" in r
        assert "EMPTY" in r

    def test_non_empty_chain_repr(self):
        chain = FilterChain("l")
        chain.add_filter(_filt(expr="a = 1"))
        r = repr(chain)
        assert "a = 1" in r
        assert "1 filters" in r
