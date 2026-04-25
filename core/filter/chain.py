"""
FilterChain — composable filter combination with explicit priorities.

Ported from FilterMate core/filter/filter_chain.py,
adapted for GISPulse (no QgsVectorLayer, uses layer_key strings).
"""

from __future__ import annotations

from datetime import datetime

from core.filter.types import CombinationStrategy, Filter, FilterType
from core.logging import get_logger

log = get_logger(__name__)


class FilterChain:
    """Chain of filters with explicit combination rules.

    Manages ordering, logical combination, and SQL expression generation.

    Example::

        chain = FilterChain("my_dataset::parcels")
        chain.add_filter(Filter(FilterType.SPATIAL_SELECTION, "pk IN (1,2,3)", "zone"))
        chain.add_filter(Filter(FilterType.FIELD_CONDITION, "status='active'", "parcels"))
        expr = chain.build_expression()
        # => "(pk IN (1,2,3)) AND (status='active')"
    """

    def __init__(
        self,
        target_layer: str,
        combination_strategy: CombinationStrategy = CombinationStrategy.PRIORITY_AND,
    ) -> None:
        self.target_layer = target_layer
        self.filters: list[Filter] = []
        self.combination_strategy = combination_strategy
        self._cache: dict[str, str] = {}
        self._creation_time = datetime.now()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_filter(self, f: Filter, replace_existing: bool = False) -> bool:
        """Add a filter to the chain. Returns False if validation fails."""
        is_valid, error_msg = f.validate()
        if not is_valid:
            log.warning("filter_rejected", error=error_msg)
            return False

        if not self._validate_compatibility(f):
            log.warning("filter_incompatible", filter=repr(f))
            return False

        if replace_existing:
            self.remove_filter(f.filter_type)

        self.filters.append(f)
        self._cache.clear()
        return True

    def remove_filter(self, filter_type: FilterType) -> int:
        before = len(self.filters)
        self.filters = [f for f in self.filters if f.filter_type != filter_type]
        removed = before - len(self.filters)
        if removed:
            self._cache.clear()
        return removed

    def get_filters_by_type(self, filter_type: FilterType) -> list[Filter]:
        return [f for f in self.filters if f.filter_type == filter_type]

    def has_filter_type(self, filter_type: FilterType) -> bool:
        return any(f.filter_type == filter_type for f in self.filters)

    def clear(self) -> None:
        self.filters.clear()
        self._cache.clear()

    def build_expression(self, dialect: str = "postgis") -> str:
        """Build a combined SQL expression from all filters.

        Sorts by priority (descending), converts each filter to SQL,
        and combines using the chain's combination strategy.
        """
        if not self.filters:
            return ""

        cache_key = f"{dialect}_{hash(tuple(self.filters))}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        sorted_filters = sorted(self.filters, key=lambda f: f.priority, reverse=True)
        parts: list[tuple[str, str]] = []
        for flt in sorted_filters:
            sql = flt.expression.strip()
            if sql:
                parts.append((flt.combine_operator, sql))

        if not parts:
            return ""

        result = self._combine_parts(parts)
        result = self._optimize_expression(result)
        self._cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "target_layer": self.target_layer,
            "strategy": self.combination_strategy.value,
            "filter_count": len(self.filters),
            "created_at": self._creation_time.isoformat(),
            "filters": [
                {
                    "type": f.filter_type.value,
                    "expression": f.expression,
                    "layer_name": f.layer_name,
                    "priority": f.priority,
                    "operator": f.combine_operator,
                    "metadata": f.metadata,
                    "is_temporary": f.is_temporary,
                    "created_at": f.created_at.isoformat(),
                }
                for f in self.filters
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> FilterChain:
        strategy = CombinationStrategy(data.get("strategy", "priority_and"))
        chain = cls(data.get("target_layer", ""), strategy)
        for fd in data.get("filters", []):
            f = Filter(
                filter_type=FilterType(fd["type"]),
                expression=fd["expression"],
                layer_name=fd["layer_name"],
                priority=fd.get("priority"),
                combine_operator=fd.get("operator", "AND"),
                metadata=fd.get("metadata", {}),
                is_temporary=fd.get("is_temporary", False),
            )
            chain.add_filter(f)
        return chain

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _combine_parts(self, parts: list[tuple[str, str]]) -> str:
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0][1]

        if self.combination_strategy == CombinationStrategy.PRIORITY_AND:
            return " AND ".join(f"({expr})" for _, expr in parts)

        if self.combination_strategy == CombinationStrategy.PRIORITY_OR:
            return " OR ".join(f"({expr})" for _, expr in parts)

        if self.combination_strategy == CombinationStrategy.CUSTOM:
            result = parts[0][1]
            for operator, expr in parts[1:]:
                result = f"({result}) {operator.upper()} ({expr})"
            return result

        # REPLACE: only keep the highest-priority filter
        return parts[0][1]

    @staticmethod
    def _optimize_expression(expression: str) -> str:
        if not expression:
            return expression
        # Strip redundant outer parentheses
        while expression.startswith("(") and expression.endswith(")"):
            depth = 0
            is_outer = True
            for i, ch in enumerate(expression):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0 and i < len(expression) - 1:
                        is_outer = False
                        break
            if is_outer:
                expression = expression[1:-1].strip()
            else:
                break
        return expression

    def _validate_compatibility(self, new_filter: Filter) -> bool:
        # MV replaces FID_LIST (optimization)
        if new_filter.filter_type == FilterType.MATERIALIZED_VIEW:
            if self.has_filter_type(FilterType.FID_LIST):
                log.info("mv_replaces_fid_list")
        return True

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.filters)

    def __bool__(self) -> bool:
        return len(self.filters) > 0

    def __repr__(self) -> str:
        if not self.filters:
            return f"FilterChain({self.target_layer!r}): EMPTY"
        lines = "\n  ".join(
            f"[{f.priority:3d}] {f.filter_type.value:20s} | {f.combine_operator:3s} | {f.expression[:60]}"
            for f in sorted(self.filters, key=lambda x: x.priority, reverse=True)
        )
        return f"FilterChain({self.target_layer!r}, {len(self.filters)} filters):\n  {lines}"
