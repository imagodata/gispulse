"""
GISPulse Filter System — ported from FilterMate's hexagonal core.

Provides composable, multi-backend spatial and attribute filtering
with caching, expression conversion, and filter chaining.
"""

from gispulse.core.filter.expression import FilterExpression, SpatialPredicate
from gispulse.core.filter.result import FilterResult, FilterStatus
from gispulse.core.filter.types import (
    CombinationStrategy,
    Filter,
    FilterType,
)

__all__ = [
    "CombinationStrategy",
    "Filter",
    "FilterExpression",
    "FilterResult",
    "FilterStatus",
    "FilterType",
    "SpatialPredicate",
]
