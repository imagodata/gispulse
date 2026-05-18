"""Typed trigger conditions for GISPulse.

Each TriggerType maps to a specific conditions dataclass. The
``parse_conditions`` function converts raw dicts into typed instances.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from gispulse.core.enums import TriggerType


@dataclass
class DMLConditions:
    """Conditions for a DML trigger (INSERT/UPDATE/DELETE on a table)."""
    table: str = ""
    schema: str = "public"
    events: list[str] = field(default_factory=lambda: ["INSERT", "UPDATE", "DELETE"])
    session_id: str | None = None
    operations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ThresholdConditions:
    """Conditions for a threshold trigger (aggregate metric crosses value)."""
    table: str = ""
    metric: str = "feature_count"
    operator: str = "gt"
    threshold_value: float = 0
    field: str | None = None


@dataclass
class ValidationConditions:
    """Conditions for a data validation trigger."""
    table: str = ""
    validation_rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BusinessRuleConditions:
    """Conditions for a business rule trigger (SQL expression)."""
    table: str = ""
    expression: str = ""


@dataclass
class TopologyConditions:
    """Conditions for a topology integrity trigger."""
    table: str = ""
    topo_check: str = ""
    ref_table: str = ""
    tolerance: float = 0.001


@dataclass
class SpatialConstraintConditions:
    """Conditions for a spatial constraint trigger (distance/zone)."""
    table: str = ""
    ref_table: str = ""
    spatial_type: str = "min_distance"
    distance: float = 0


@dataclass
class CompositeConditions:
    """Conditions for a composite trigger (combines sub-triggers)."""
    trigger_ids: list[str] = field(default_factory=list)
    composite_mode: str = "all"


@dataclass
class ScheduleConditions:
    """Conditions for a schedule (CRON) trigger."""
    cron: str = ""
    timezone: str = "UTC"


# Union type for all structured conditions
TriggerConditions = (
    DMLConditions | ThresholdConditions | ValidationConditions |
    BusinessRuleConditions | TopologyConditions | SpatialConstraintConditions |
    CompositeConditions | ScheduleConditions
)

# Map TriggerType → conditions dataclass
_CONDITIONS_TYPE_MAP: dict[str, type] = {
    "dml": DMLConditions,
    "threshold": ThresholdConditions,
    "validation": ValidationConditions,
    "business_rule": BusinessRuleConditions,
    "topology": TopologyConditions,
    "spatial_constraint": SpatialConstraintConditions,
    "composite": CompositeConditions,
    "schedule": ScheduleConditions,
}


def parse_conditions(trigger_type: str | TriggerType, raw: dict[str, Any]) -> TriggerConditions | dict[str, Any]:
    """Parse a raw conditions dict into a typed dataclass.

    Falls back to returning the raw dict for unknown trigger types
    (api, esb_event, webhook_in) to maintain backward compatibility.
    """
    tt = trigger_type.value if isinstance(trigger_type, TriggerType) else str(trigger_type)
    cls = _CONDITIONS_TYPE_MAP.get(tt)
    if cls is None:
        return raw

    # Legacy compatibility: convert "operation" (singular) to "events" list
    adapted = dict(raw)
    if cls is DMLConditions and "operation" in adapted and "events" not in adapted:
        adapted["events"] = [str(adapted.pop("operation")).upper()]

    valid_fields = {f.name for f in dataclasses.fields(cls)}
    filtered = {k: v for k, v in adapted.items() if k in valid_fields}
    return cls(**filtered)
