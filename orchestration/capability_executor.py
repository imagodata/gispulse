"""
CapabilityExecutor — bridge between the orchestration layer and capabilities.

Builds an ExecutionContext from the current engine state and delegates to
the capability's strategy-aware ``execute_with_context()`` when available,
falling back to the plain ``execute()`` for backward compatibility.
"""

from __future__ import annotations

import geopandas as gpd

from capabilities import registry
from capabilities.strategy import ExecutionContext
from core.logging import get_logger
from persistence.engine import SpatialEngine

log = get_logger(__name__)


class CapabilityExecutor:
    """Orchestrates capability execution with automatic strategy selection.

    Args:
        engine: The active SpatialEngine (DuckDB or PostGIS).
    """

    def __init__(self, engine: SpatialEngine) -> None:
        self._engine = engine

    def run(
        self,
        capability_name: str,
        gdf: gpd.GeoDataFrame,
        params: dict | None = None,
    ) -> gpd.GeoDataFrame:
        """Execute a capability by name, choosing the best backend strategy.

        1. Resolves the capability from the registry.
        2. Builds an ``ExecutionContext`` from the engine and input data.
        3. If the capability declares strategies, calls ``execute_with_context()``.
        4. Otherwise falls back to the plain ``execute(**params)``.

        Args:
            capability_name: Registered capability name (e.g. 'buffer').
            gdf:             Input GeoDataFrame.
            params:          Capability-specific keyword arguments.

        Returns:
            Resulting GeoDataFrame.
        """
        params = params or {}
        capability = registry.get(capability_name)

        ctx = ExecutionContext(
            engine=self._engine,
            feature_count=len(gdf),
            has_spatial_index=_has_spatial_index(gdf),
            params=params,
        )

        # Strategy-aware path
        if capability._strategies:
            log.info(
                "capability_executor_dispatch",
                capability=capability_name,
                feature_count=ctx.feature_count,
                backend=self._engine.backend_name,
                strategies_available=len(capability._strategies),
            )
            return capability.execute_with_context(gdf, ctx)

        # Legacy fallback — plain execute()
        log.debug(
            "capability_executor_fallback",
            capability=capability_name,
            reason="no_strategies",
        )
        return capability.execute(gdf, **params)


def _has_spatial_index(gdf: gpd.GeoDataFrame) -> bool:
    """Best-effort check for spatial index on a GeoDataFrame."""
    try:
        return gdf.sindex is not None and len(gdf.sindex) > 0
    except Exception:
        return False
