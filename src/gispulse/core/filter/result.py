"""
Filter Result Value Object.

Immutable representation of a filter operation result
with execution statistics and error handling.

Ported from FilterMate core/domain/filter_result.py,
adapted for GISPulse (GeoDataFrame-based, not feature-ID-based).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import geopandas as gpd


class FilterStatus(str, Enum):
    """Status of a filter operation."""

    SUCCESS = "success"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    ERROR = "error"
    NO_MATCHES = "no_matches"


@dataclass(frozen=True)
class FilterResult:
    """Immutable result of a filter operation.

    Carries the filtered GeoDataFrame (optional), count, bbox,
    execution stats, and error information.

    Use factory methods for construction: ``success()``, ``error()``,
    ``cancelled()``, ``from_cache()``, ``partial()``.
    """

    feature_count: int
    layer_key: str
    expression_raw: str
    status: FilterStatus = FilterStatus.SUCCESS
    execution_time_ms: float = 0.0
    is_cached: bool = False
    backend_name: str = ""
    bbox: Optional[tuple[float, float, float, float]] = None
    error_message: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    # The filtered GeoDataFrame itself — not included in hash/eq
    gdf: Optional[Any] = field(default=None, compare=False, hash=False)

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def success(
        cls,
        gdf: gpd.GeoDataFrame,
        layer_key: str,
        expression_raw: str,
        execution_time_ms: float = 0.0,
        backend_name: str = "",
    ) -> FilterResult:
        count = len(gdf)
        status = FilterStatus.SUCCESS if count else FilterStatus.NO_MATCHES
        bbox = _extract_bbox(gdf) if count else None
        return cls(
            feature_count=count,
            layer_key=layer_key,
            expression_raw=expression_raw,
            status=status,
            execution_time_ms=execution_time_ms,
            backend_name=backend_name,
            bbox=bbox,
            gdf=gdf,
        )

    @classmethod
    def error(
        cls,
        layer_key: str,
        expression_raw: str,
        error_message: str,
        backend_name: str = "",
    ) -> FilterResult:
        return cls(
            feature_count=0,
            layer_key=layer_key,
            expression_raw=expression_raw,
            status=FilterStatus.ERROR,
            error_message=error_message,
            backend_name=backend_name,
        )

    @classmethod
    def cancelled(
        cls,
        layer_key: str,
        expression_raw: str,
    ) -> FilterResult:
        return cls(
            feature_count=0,
            layer_key=layer_key,
            expression_raw=expression_raw,
            status=FilterStatus.CANCELLED,
        )

    @classmethod
    def from_cache(
        cls,
        gdf: gpd.GeoDataFrame,
        layer_key: str,
        expression_raw: str,
        original_execution_time_ms: float = 0.0,
        backend_name: str = "",
    ) -> FilterResult:
        count = len(gdf)
        status = FilterStatus.SUCCESS if count else FilterStatus.NO_MATCHES
        bbox = _extract_bbox(gdf) if count else None
        return cls(
            feature_count=count,
            layer_key=layer_key,
            expression_raw=expression_raw,
            status=status,
            execution_time_ms=original_execution_time_ms,
            is_cached=True,
            backend_name=backend_name,
            bbox=bbox,
            gdf=gdf,
        )

    @classmethod
    def partial(
        cls,
        gdf: gpd.GeoDataFrame,
        layer_key: str,
        expression_raw: str,
        error_message: str,
        execution_time_ms: float = 0.0,
        backend_name: str = "",
    ) -> FilterResult:
        count = len(gdf)
        bbox = _extract_bbox(gdf) if count else None
        return cls(
            feature_count=count,
            layer_key=layer_key,
            expression_raw=expression_raw,
            status=FilterStatus.PARTIAL,
            execution_time_ms=execution_time_ms,
            error_message=error_message,
            backend_name=backend_name,
            bbox=bbox,
            gdf=gdf,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        return self.feature_count == 0

    @property
    def has_error(self) -> bool:
        return self.status == FilterStatus.ERROR

    @property
    def is_success(self) -> bool:
        return self.status in (FilterStatus.SUCCESS, FilterStatus.NO_MATCHES)

    @property
    def was_cancelled(self) -> bool:
        return self.status == FilterStatus.CANCELLED

    @property
    def is_partial(self) -> bool:
        return self.status == FilterStatus.PARTIAL

    def __str__(self) -> str:
        if self.has_error:
            return f"FilterResult(ERROR: {self.error_message})"
        if self.was_cancelled:
            return "FilterResult(CANCELLED)"
        cache_info = " [cached]" if self.is_cached else ""
        partial_info = " [partial]" if self.is_partial else ""
        return f"FilterResult({self.feature_count} features, {self.execution_time_ms:.1f}ms{cache_info}{partial_info})"


def _extract_bbox(gdf: gpd.GeoDataFrame) -> tuple[float, float, float, float] | None:
    """Extract WGS84 bounding box from a GeoDataFrame."""
    if gdf.empty:
        return None
    try:
        gdf_4326 = gdf.to_crs(epsg=4326) if gdf.crs and not gdf.crs.equals("EPSG:4326") else gdf
        bounds = gdf_4326.total_bounds
        return (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
    except Exception:
        return None
