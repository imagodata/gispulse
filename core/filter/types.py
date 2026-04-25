"""
Filter types — FilterType, CombinationStrategy, Filter, and priority defaults.

Ported from FilterMate core/filter/filter_chain.py,
adapted for GISPulse (no QGIS dependencies).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class FilterType(str, Enum):
    """Distinct filter types with clear semantics."""

    SPATIAL_SELECTION = "spatial_selection"
    FIELD_CONDITION = "field_condition"
    FID_LIST = "fid_list"
    CUSTOM_EXPRESSION = "custom_expression"
    USER_SELECTION = "user_selection"
    BUFFER_INTERSECT = "buffer_intersect"
    SPATIAL_RELATION = "spatial_relation"
    BBOX_FILTER = "bbox_filter"
    MATERIALIZED_VIEW = "materialized_view"


# Higher value = applied first (most restrictive first)
DEFAULT_PRIORITIES: dict[FilterType, int] = {
    FilterType.MATERIALIZED_VIEW: 100,
    FilterType.BBOX_FILTER: 90,
    FilterType.SPATIAL_SELECTION: 80,
    FilterType.FID_LIST: 70,
    FilterType.BUFFER_INTERSECT: 60,
    FilterType.SPATIAL_RELATION: 60,
    FilterType.FIELD_CONDITION: 50,
    FilterType.USER_SELECTION: 40,
    FilterType.CUSTOM_EXPRESSION: 30,
}


class CombinationStrategy(str, Enum):
    """How multiple filters in a chain are combined."""

    PRIORITY_AND = "priority_and"
    PRIORITY_OR = "priority_or"
    CUSTOM = "custom"
    REPLACE = "replace"


@dataclass
class Filter:
    """A single filter with metadata.

    Attributes:
        filter_type:      Category of filter.
        expression:       SQL / pandas expression string.
        layer_name:       Source layer name (for traceability).
        priority:         Application priority (1-100). Auto-assigned if None.
        combine_operator: Logical operator when combined (AND/OR).
        metadata:         Free-form metadata (name, description, ...).
        is_temporary:     If True the filter is not persisted.
        created_at:       Creation timestamp.
    """

    filter_type: FilterType
    expression: str
    layer_name: str
    priority: Optional[int] = None
    combine_operator: str = "AND"
    metadata: dict[str, Any] = field(default_factory=dict)
    is_temporary: bool = False
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if self.priority is None:
            self.priority = DEFAULT_PRIORITIES.get(self.filter_type, 50)

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.expression or not self.expression.strip():
            return False, "Expression is empty"
        if not self.layer_name:
            return False, "Layer name is required"
        if self.priority < 1 or self.priority > 100:
            return False, f"Priority must be between 1-100, got {self.priority}"
        if self.combine_operator.upper() not in ("AND", "OR"):
            return False, f"Invalid combine_operator: {self.combine_operator}"
        return True, None

    def __hash__(self) -> int:
        return hash((self.filter_type, self.expression, self.priority))

    def __repr__(self) -> str:
        preview = self.expression[:50] + "..." if len(self.expression) > 50 else self.expression
        return f"Filter({self.filter_type.value}, priority={self.priority}, expr='{preview}')"
