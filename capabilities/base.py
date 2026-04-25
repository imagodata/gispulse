"""
Base class for all GISPulse capabilities.

Each capability encapsulates a single, reusable spatial operation.
Concrete implementations live in capabilities/vector.py (and future
capabilities/raster.py, capabilities/network.py, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import geopandas as gpd

if TYPE_CHECKING:
    from capabilities.strategy import ExecutionContext, ExecutionStrategy

from core.logging import get_logger

log = get_logger(__name__)


class Capability(ABC):
    """Abstract base for all GISPulse spatial capabilities.

    Subclasses must declare:
    - ``name``        : unique snake_case identifier used in the registry.
    - ``description`` : human-readable one-liner for Studio/API.

    And implement:
    - ``execute()``   : the spatial operation itself.
    - ``get_schema()`` (optional): JSON Schema of accepted **params.

    Optionally, subclasses can populate ``_strategies`` with
    :class:`ExecutionStrategy` instances to enable backend-aware
    dispatching via ``execute_with_context()``.
    """

    name: str
    description: str
    # Subclasses override with a list of strategies; empty tuple prevents
    # accidental mutation of the shared class-level default.
    _strategies: list[ExecutionStrategy] | tuple[()] = ()

    @abstractmethod
    def execute(self, gdf: gpd.GeoDataFrame, **params) -> gpd.GeoDataFrame:
        """Run the capability on a GeoDataFrame.

        Args:
            gdf:    Input GeoDataFrame (never mutated in-place).
            **params: Capability-specific keyword arguments.

        Returns:
            New GeoDataFrame with the operation applied.
        """
        ...

    def execute_with_context(
        self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext,
    ) -> gpd.GeoDataFrame:
        """Run the capability using the best available execution strategy.

        Selects the highest-priority strategy that can execute in the
        given context.  Falls back to the plain ``execute()`` method
        (Python/GeoPandas) if no strategy is eligible or none are declared.

        Args:
            gdf: Input GeoDataFrame (never mutated in-place).
            ctx: Runtime execution context with engine info and params.

        Returns:
            New GeoDataFrame with the operation applied.
        """
        from capabilities.strategy import select_strategy

        if self._strategies:
            strategy = select_strategy(self._strategies, ctx)
            if strategy is not None:
                log.info(
                    "strategy_selected",
                    capability=self.name,
                    strategy=strategy.mode.value,
                    priority=strategy.priority,
                )
                return strategy.execute(gdf, ctx)
            log.debug(
                "no_eligible_strategy",
                capability=self.name,
                fallback="python",
            )

        # Fallback: plain execute() with params from context
        return self.execute(gdf, **ctx.params)

    def get_schema(self) -> dict:
        """Return the JSON Schema for this capability's **params.

        Used by GISPulse Studio to build dynamic forms.
        Subclasses should override this to expose their parameters.

        Returns:
            JSON Schema dict (``{"type": "object", "properties": {...}}``)
            or an empty dict when no parameters are needed.
        """
        return {}
