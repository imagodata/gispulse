"""Tests for capabilities/strategy.py — multi-backend dispatch."""

from __future__ import annotations

from unittest.mock import MagicMock

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.capabilities.strategy import (
    ExecutionContext,
    ExecutionStrategy,
    StrategyMode,
    select_strategy,
)
from gispulse.persistence.engine import SpatialEngine


# ---------------------------------------------------------------------------
# Helpers — minimal engine stubs
# ---------------------------------------------------------------------------


def _make_engine(backend: str) -> SpatialEngine:
    """Return a MagicMock that quacks like a SpatialEngine."""
    engine = MagicMock(spec=SpatialEngine)
    engine.backend_name = backend
    engine.is_persistent = backend == "postgis"
    return engine


def _make_gdf(n: int = 5) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"value": list(range(n))},
        geometry=[Point(i, i) for i in range(n)],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# Concrete strategy stubs
# ---------------------------------------------------------------------------


class PythonStrategy(ExecutionStrategy):
    mode = StrategyMode.PYTHON

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return True  # always available

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        return gdf.copy()

    @property
    def priority(self) -> int:
        return 10


class DuckDBStrategy(ExecutionStrategy):
    mode = StrategyMode.DUCKDB

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return ctx.engine.backend_name == "duckdb"

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        return gdf.copy()

    @property
    def priority(self) -> int:
        return 80


class PostGISStrategy(ExecutionStrategy):
    mode = StrategyMode.POSTGIS

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return ctx.engine.backend_name == "postgis"

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        return gdf.copy()

    @property
    def priority(self) -> int:
        return 100


ALL_STRATEGIES: list[ExecutionStrategy] = [
    PythonStrategy(),
    DuckDBStrategy(),
    PostGISStrategy(),
]


# ---------------------------------------------------------------------------
# Tests — StrategyMode
# ---------------------------------------------------------------------------


class TestStrategyMode:
    def test_values(self):
        assert StrategyMode.PYTHON == "python"
        assert StrategyMode.DUCKDB == "duckdb"
        assert StrategyMode.POSTGIS == "postgis"

    def test_value(self):
        assert StrategyMode.PYTHON.value == "python"


# ---------------------------------------------------------------------------
# Tests — ExecutionContext
# ---------------------------------------------------------------------------


class TestExecutionContext:
    def test_defaults(self):
        engine = _make_engine("duckdb")
        ctx = ExecutionContext(engine=engine, feature_count=10)
        assert ctx.feature_count == 10
        assert ctx.has_spatial_index is False
        assert ctx.params == {}

    def test_custom_params(self):
        engine = _make_engine("duckdb")
        ctx = ExecutionContext(engine=engine, feature_count=100, params={"buffer": 50})
        assert ctx.params["buffer"] == 50


# ---------------------------------------------------------------------------
# Tests — select_strategy (dispatch logic)
# ---------------------------------------------------------------------------


class TestSelectStrategy:
    def test_selects_duckdb_for_duckdb_backend(self):
        engine = _make_engine("duckdb")
        ctx = ExecutionContext(engine=engine, feature_count=10)
        chosen = select_strategy(ALL_STRATEGIES, ctx)
        assert chosen is not None
        assert chosen.mode == StrategyMode.DUCKDB

    def test_selects_postgis_for_postgis_backend(self):
        engine = _make_engine("postgis")
        ctx = ExecutionContext(engine=engine, feature_count=10)
        chosen = select_strategy(ALL_STRATEGIES, ctx)
        assert chosen is not None
        assert chosen.mode == StrategyMode.POSTGIS

    def test_falls_back_to_python_when_only_python_eligible(self):
        """With a backend that has no specialised strategy, Python wins."""
        engine = _make_engine("unknown_backend")
        ctx = ExecutionContext(engine=engine, feature_count=10)
        chosen = select_strategy(ALL_STRATEGIES, ctx)
        assert chosen is not None
        assert chosen.mode == StrategyMode.PYTHON

    def test_returns_none_when_no_eligible_strategy(self):
        engine = _make_engine("unknown_backend")
        ctx = ExecutionContext(engine=engine, feature_count=10)
        # Only specialised strategies — none eligible
        chosen = select_strategy([DuckDBStrategy(), PostGISStrategy()], ctx)
        assert chosen is None

    def test_empty_strategy_list(self):
        engine = _make_engine("duckdb")
        ctx = ExecutionContext(engine=engine, feature_count=10)
        assert select_strategy([], ctx) is None

    def test_highest_priority_wins_among_eligible(self):
        """When multiple strategies are eligible, the one with highest priority wins."""

        class HighPriorityPython(PythonStrategy):
            @property
            def priority(self) -> int:
                return 200

        engine = _make_engine("duckdb")
        ctx = ExecutionContext(engine=engine, feature_count=10)
        chosen = select_strategy(
            [DuckDBStrategy(), PythonStrategy(), HighPriorityPython()], ctx
        )
        # HighPriorityPython (200) > DuckDB (80)
        assert chosen is not None
        assert chosen.priority == 200

    def test_single_strategy_always_selected_when_eligible(self):
        engine = _make_engine("duckdb")
        ctx = ExecutionContext(engine=engine, feature_count=5)
        chosen = select_strategy([DuckDBStrategy()], ctx)
        assert chosen is not None
        assert chosen.mode == StrategyMode.DUCKDB


# ---------------------------------------------------------------------------
# Tests — execute round-trip
# ---------------------------------------------------------------------------


class TestExecuteRoundTrip:
    def test_duckdb_strategy_returns_copy(self):
        engine = _make_engine("duckdb")
        ctx = ExecutionContext(engine=engine, feature_count=5)
        gdf = _make_gdf(5)
        strategy = DuckDBStrategy()
        result = strategy.execute(gdf, ctx)
        assert len(result) == len(gdf)
        assert result is not gdf  # must be a copy

    def test_postgis_strategy_returns_copy(self):
        engine = _make_engine("postgis")
        ctx = ExecutionContext(engine=engine, feature_count=5)
        gdf = _make_gdf(5)
        strategy = PostGISStrategy()
        result = strategy.execute(gdf, ctx)
        assert len(result) == len(gdf)
        assert result is not gdf

    def test_python_strategy_returns_copy(self):
        engine = _make_engine("python")
        ctx = ExecutionContext(engine=engine, feature_count=5)
        gdf = _make_gdf(5)
        strategy = PythonStrategy()
        result = strategy.execute(gdf, ctx)
        assert len(result) == len(gdf)
        assert result is not gdf


# ---------------------------------------------------------------------------
# Tests — strategy ABCs enforce interface
# ---------------------------------------------------------------------------


class TestStrategyABC:
    def test_cannot_instantiate_abstract_strategy(self):
        with pytest.raises(TypeError):
            ExecutionStrategy()  # type: ignore[abstract]

    def test_abstract_methods_must_be_implemented(self):
        """Partial implementation raises TypeError."""
        class PartialStrategy(ExecutionStrategy):
            mode = StrategyMode.PYTHON

            def can_execute(self, ctx):
                return True

            # Missing execute and priority

        with pytest.raises(TypeError):
            PartialStrategy()  # type: ignore[abstract]
