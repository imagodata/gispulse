"""
FilterService — high-level orchestration for filter operations.

Coordinates expression validation, cache lookup, strategy selection,
execution, and cache storage. This is the main entry point for
programmatic filtering in GISPulse.

Inspired by FilterMate core/services/filter_service.py.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from gispulse.core.filter.cache import CacheStats, FilterCache, NullCache
from gispulse.core.filter.chain import FilterChain
from gispulse.core.filter.expression import FilterExpression
from gispulse.core.filter.expression_converter import ExpressionConverter
from gispulse.core.filter.result import FilterResult
from gispulse.core.logging import get_logger

if TYPE_CHECKING:
    import geopandas as gpd

    from gispulse.persistence.engine import SpatialEngine

log = get_logger(__name__)


class FilterService:
    """High-level filter orchestration service.

    Args:
        engine:  Active SpatialEngine (DuckDB or PostGIS).
        cache:   Optional FilterCache (NullCache if None).
    """

    def __init__(
        self,
        engine: SpatialEngine,
        cache: Optional[FilterCache] = None,
    ) -> None:
        self._engine = engine
        self._cache = cache or NullCache()
        self._converter = ExpressionConverter()

    # ------------------------------------------------------------------
    # Preview (count + bbox, no features returned)
    # ------------------------------------------------------------------

    def preview(
        self,
        expression: FilterExpression,
        target_gdf: gpd.GeoDataFrame,
        ref_gdf: Optional[gpd.GeoDataFrame] = None,
    ) -> FilterResult:
        """Count matching features and compute bbox without returning them."""
        result = self.apply(expression, target_gdf, ref_gdf)
        # Strip the GeoDataFrame from the result to save memory
        return FilterResult(
            feature_count=result.feature_count,
            layer_key=result.layer_key,
            expression_raw=result.expression_raw,
            status=result.status,
            execution_time_ms=result.execution_time_ms,
            is_cached=result.is_cached,
            backend_name=result.backend_name,
            bbox=result.bbox,
            error_message=result.error_message,
            timestamp=result.timestamp,
            gdf=None,
        )

    # ------------------------------------------------------------------
    # Apply (full execution with GeoDataFrame result)
    # ------------------------------------------------------------------

    def apply(
        self,
        expression: FilterExpression,
        target_gdf: gpd.GeoDataFrame,
        ref_gdf: Optional[gpd.GeoDataFrame] = None,
    ) -> FilterResult:
        """Apply a filter expression and return the full result."""
        layer_key = expression.target_layer or expression.source_layer or "unknown"

        # Validate
        is_valid, errors = self._converter.validate(expression.raw)
        if not is_valid and not expression.raw.startswith("Spatial filter:"):
            return FilterResult.error(
                layer_key=layer_key,
                expression_raw=expression.raw,
                error_message="; ".join(errors),
            )

        # Cache check
        cache_key = FilterCache.make_key(
            layer_key,
            expression.raw,
            spatial=str(expression.spatial_predicates),
            buffer=str(expression.buffer_value),
        )
        cached = self._cache.get(cache_key)
        if cached is not None and cached.gdf is not None:
            log.info("filter_cache_hit", layer_key=layer_key, key=cache_key[:8])
            return FilterResult.from_cache(
                gdf=cached.gdf,
                layer_key=layer_key,
                expression_raw=expression.raw,
                original_execution_time_ms=cached.execution_time_ms,
                backend_name=cached.backend_name,
            )

        # Execute
        t0 = time.monotonic()
        try:
            filtered_gdf = self._execute(expression, target_gdf, ref_gdf)
        except Exception as exc:
            log.error("filter_execution_error", error=str(exc), layer_key=layer_key)
            return FilterResult.error(
                layer_key=layer_key,
                expression_raw=expression.raw,
                error_message=str(exc),
                backend_name=self._engine.backend_name,
            )
        elapsed_ms = (time.monotonic() - t0) * 1000

        result = FilterResult.success(
            gdf=filtered_gdf,
            layer_key=layer_key,
            expression_raw=expression.raw,
            execution_time_ms=elapsed_ms,
            backend_name=self._engine.backend_name,
        )

        # Cache store
        self._cache.set(cache_key, result)
        log.info(
            "filter_applied",
            layer_key=layer_key,
            count=result.feature_count,
            time_ms=f"{elapsed_ms:.1f}",
            backend=self._engine.backend_name,
        )
        return result

    # ------------------------------------------------------------------
    # Apply chain (multi-step filtering)
    # ------------------------------------------------------------------

    def apply_chain(
        self,
        chain: FilterChain,
        target_gdf: gpd.GeoDataFrame,
        ref_gdf: Optional[gpd.GeoDataFrame] = None,
    ) -> FilterResult:
        """Apply a FilterChain (multi-step) and return the combined result."""
        combined_sql = chain.build_expression(dialect=self._engine.backend_name)
        if not combined_sql:
            return FilterResult.success(
                gdf=target_gdf,
                layer_key=chain.target_layer,
                expression_raw="(empty chain)",
                backend_name=self._engine.backend_name,
            )

        expr = FilterExpression.create(
            combined_sql,
            target_layer=chain.target_layer,
        )
        return self.apply(expr, target_gdf, ref_gdf)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, expression: str) -> tuple[bool, list[str]]:
        return self._converter.validate(expression)

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def get_cache_stats(self) -> CacheStats:
        return self._cache.get_stats()

    def invalidate_cache(self, layer_key: Optional[str] = None) -> int:
        if layer_key:
            return self._cache.invalidate_layer(layer_key)
        return self._cache.clear()

    # ------------------------------------------------------------------
    # Internal execution
    # ------------------------------------------------------------------

    def _execute(
        self,
        expression: FilterExpression,
        target_gdf: gpd.GeoDataFrame,
        ref_gdf: Optional[gpd.GeoDataFrame],
    ) -> gpd.GeoDataFrame:
        """Execute filter using the capability + strategy system."""
        from gispulse.capabilities.registry import get as get_capability
        from gispulse.capabilities.strategy import ExecutionContext

        params: dict = {
            "expression": expression.raw if expression.is_simple else expression.raw,
            "ref_gdf": ref_gdf,
            "ref_wkt": expression.ref_wkt,
            "buffer_distance": expression.buffer_value,
        }

        # Set attribute expression (only if not pure spatial)
        if not expression.raw.startswith("Spatial filter:"):
            params["expression"] = expression.raw
        else:
            params["expression"] = ""

        # Set spatial predicate
        if expression.is_spatial and expression.spatial_predicates:
            params["spatial_predicate"] = expression.spatial_predicates[0].value

        cap = get_capability("filter")
        ctx = ExecutionContext(
            engine=self._engine,
            feature_count=len(target_gdf),
            params=params,
        )
        return cap.execute_with_context(target_gdf, ctx)
