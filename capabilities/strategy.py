"""
Execution strategy pattern for GISPulse capabilities.

Allows a single Capability to dispatch its work to different backends
(Python/GeoPandas, DuckDB, PostGIS) based on runtime context such as
feature count, available engine, and spatial index presence.

The pattern is: Strategy -> Service -> Tasks, where the strategy with
the highest priority that can execute in the current context wins.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import geopandas as gpd

from persistence.engine import SpatialEngine


class StrategyMode(str, Enum):
    """Identifies the backend a strategy targets."""

    PYTHON = "python"
    DUCKDB = "duckdb"
    POSTGIS = "postgis"


@dataclass
class ExecutionContext:
    """Runtime context passed to strategies for eligibility checks and execution.

    Attributes:
        engine:           The active SpatialEngine (DuckDB or PostGIS).
        feature_count:    Number of features in the input GeoDataFrame.
        has_spatial_index: Whether the source data has a spatial index.
        params:           Capability-specific keyword arguments.
    """

    engine: SpatialEngine
    feature_count: int
    has_spatial_index: bool = False
    params: dict[str, Any] = field(default_factory=dict)


class ExecutionStrategy(ABC):
    """Abstract base for backend-specific execution strategies.

    Each strategy declares:
    - ``mode``:         Which backend it targets.
    - ``can_execute()``: Whether it is eligible given the current context.
    - ``execute()``:    The actual computation.
    - ``priority``:     Higher priority strategies are preferred when eligible.
    """

    mode: StrategyMode

    @abstractmethod
    def can_execute(self, ctx: ExecutionContext) -> bool:
        """Return True if this strategy can run in the given context."""
        ...

    @abstractmethod
    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        """Run the spatial operation using this strategy's backend.

        Args:
            gdf: Input GeoDataFrame (never mutated in-place).
            ctx: Runtime execution context.

        Returns:
            New GeoDataFrame with the operation applied.
        """
        ...

    @property
    @abstractmethod
    def priority(self) -> int:
        """Strategy selection priority. Higher wins.

        Convention:
            - PostGIS : 100  (server-side, most scalable)
            - DuckDB  :  80  (in-process SQL, good for large local datasets)
            - Python  :  10  (GeoPandas fallback, always available)
        """
        ...


def select_strategy(
    strategies: list[ExecutionStrategy],
    ctx: ExecutionContext,
) -> ExecutionStrategy | None:
    """Pick the highest-priority eligible strategy.

    Args:
        strategies: Candidate strategies (order does not matter).
        ctx:        Current execution context.

    Returns:
        The best strategy, or None if none can execute.
    """
    eligible = [s for s in strategies if s.can_execute(ctx)]
    if not eligible:
        return None
    return max(eligible, key=lambda s: s.priority)
