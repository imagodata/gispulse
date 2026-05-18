"""Table relations and computed fields for GISPulse Hybrid Schema.

Relations connect two layers (source → target) with optional triggers
and computed fields for reactive/active data propagation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from gispulse.core.enums import ComputationRefreshMode, RelationType


@dataclass
class ComputedFieldDef:
    """A field on a target layer whose value is derived from a relation.

    Example: ``nb_batiments`` on *parcelles* computed as
    ``COUNT(*) FROM batiments WHERE ST_Within(bat.geom, parcelle.geom)``.
    """

    name: str
    expression: str
    target_field: str = ""
    agg_function: str | None = None
    source_field: str | None = None
    refresh_mode: ComputationRefreshMode = ComputationRefreshMode.ON_CHANGE
    cron: str | None = None


@dataclass
class TableRelation:
    """A persisted relationship between two layers, optionally carrying
    a trigger and computed fields.

    Three levels:
    - **Passive** — detected or declared relation (no trigger)
    - **Reactive** — relation + attached trigger (fires on events)
    - **Active** — relation + trigger + computed fields (auto-calculation)
    """

    id: UUID = field(default_factory=uuid4)

    # Source & target layers
    source_layer_id: UUID | None = None
    target_layer_id: UUID | None = None
    source_layer_name: str = ""
    target_layer_name: str = ""

    # Relation definition
    relation_type: str = RelationType.SPATIAL.value
    source_field: str | None = None
    target_field: str | None = None
    spatial_op: str | None = None
    spatial_config: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    confirmed: bool = False
    auto_detected: bool = False
    label: str = ""

    # Attached trigger (reactive level)
    trigger_id: UUID | None = None

    # Computed fields (active level)
    computed_fields: list[ComputedFieldDef] = field(default_factory=list)

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
