"""Core domain models for GISPulse.

This module defines the primary domain dataclasses (Dataset, Layer, Job,
Rule, Trigger, Scenario, Project) and re-exports all supporting types
from their dedicated modules for backward compatibility.

Submodules:
    core.enums       — All enumerations
    core.conditions  — Typed trigger conditions + parse_conditions()
    core.predicates  — GeomPredicate, AttrPredicate, CompoundPredicate
    core.graph       — NodeDef, EdgeDef, ActionDef, EvalResult
    core.relations   — TableRelation, ComputedFieldDef
    core.session     — EphemeralSession
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

# ---------------------------------------------------------------------------
# Re-exports for backward compatibility — all public names remain importable
# from core.models as before.
# ---------------------------------------------------------------------------

# Enumerations
from core.enums import (  # noqa: F401
    JobStatus,
    TriggerEvent,
    DataCategory,
    ProcessingMode,
    MessageStatus,
    ExecutionMode,
    TriggerCategory,
    TriggerType,
    ChangeOperation,
    RuleScope,
    RelationType,
    ComputationRefreshMode,
    SessionStatus,
    SessionBackend,
)

# Typed trigger conditions
from core.conditions import (  # noqa: F401
    DMLConditions,
    ThresholdConditions,
    ValidationConditions,
    BusinessRuleConditions,
    TopologyConditions,
    SpatialConstraintConditions,
    CompositeConditions,
    ScheduleConditions,
    TriggerConditions,
    parse_conditions,
    _CONDITIONS_TYPE_MAP,
)

# Predicate types
from core.predicates import (  # noqa: F401
    GeomPredicate,
    AttrPredicate,
    CompoundPredicate,
    AnyPredicate,
)

# Graph / Node types
from core.graph import (  # noqa: F401
    NodeType,
    NodePort,
    NodeDef,
    EdgeDef,
    ActionType,
    ActionDef,
    SpatialState,
    Transition,
    EvalResult,
    ObjectState,
)

# Relations
from core.relations import (  # noqa: F401
    ComputedFieldDef,
    TableRelation,
)

# Session
from core.session import EphemeralSession  # noqa: F401


# ---------------------------------------------------------------------------
# Raster models
# ---------------------------------------------------------------------------


@dataclass
class RasterBand:
    """Bande d'un raster (index 1-based, conforme GDAL)."""

    index: int
    name: str | None = None
    nodata: float | None = None
    min: float | None = None
    max: float | None = None
    dtype: str | None = None


@dataclass
class RasterLayer:
    """Représentation d'une couche raster dans GISPulse."""

    id: UUID = field(default_factory=uuid4)
    dataset_id: UUID | None = None
    name: str = ""
    source: str = ""
    crs: str = "EPSG:4326"
    resolution: tuple[float, float] = (1.0, 1.0)
    bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    bands: list[RasterBand] = field(default_factory=list)
    nodata: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class OGCSourceConfig:
    """Configuration for a remote OGC service (WFS or OGC API Features)."""

    source_type: str  # "wfs" | "ogc_api_features"
    url: str
    layer_name: str
    version: str = "2.0.0"
    crs: str = "EPSG:4326"
    auth: dict[str, str] | None = None
    max_features: int | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Dataset:
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    source_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data_category: str = DataCategory.VECTOR.value
    crs: str = "EPSG:4326"
    format: str | None = None
    ogc_source: OGCSourceConfig | None = None


@dataclass
class Layer:
    id: UUID = field(default_factory=uuid4)
    dataset_id: UUID | None = None
    name: str = ""
    geometry_type: str | None = None
    srid: int = 4326
    metadata: dict[str, Any] = field(default_factory=dict)
    layer_type: str = "vector"
    has_z: bool = False
    has_m: bool = False
    feature_count: int | None = None


@dataclass
class Job:
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    status: JobStatus = JobStatus.PENDING
    dataset_id: UUID | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_path: str | None = None
    error_message: str | None = None
    attempts: int = 0
    max_retries: int = 3


@dataclass
class Artifact:
    id: UUID = field(default_factory=uuid4)
    job_id: UUID | None = None
    name: str = ""
    artifact_type: str = ""
    path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Rule:
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    scope: str = "global"
    scope_target_id: UUID | None = None
    capability: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    order: int = 0


@dataclass
class RefLayerDef:
    """Definition of a reference layer usable in rules and trigger predicates."""

    id: UUID = field(default_factory=uuid4)
    name: str = ""
    source_type: str = "gpkg_layer"
    source_path: str = ""
    layer_name: str = ""
    geom_col: str = "geom"
    srid: int = 4326
    cacheable: bool = True
    ttl_minutes: int = 60
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Trigger:
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    event: TriggerEvent = TriggerEvent.MANUAL
    trigger_type: TriggerType = TriggerType.DML
    category: TriggerCategory = TriggerCategory.DATA
    severity: str = "info"
    rule_id: UUID | None = None
    conditions: dict[str, Any] = field(default_factory=dict)
    predicates: list[AnyPredicate] = field(default_factory=list)
    predicate_logic: Literal["AND", "OR"] = "AND"
    actions: list[ActionDef] = field(default_factory=list)
    enabled: bool = True
    auto_eval: bool = False


@dataclass
class Scenario:
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    dataset_id: UUID | None = None
    jobs: list[UUID] = field(default_factory=list)
    rules: list[UUID] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    locked_by: str | None = None
    locked_at: datetime | None = None
    version: int = 1
    graph: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ChangeRecord / ChangeSet / FiredTrigger
# ---------------------------------------------------------------------------


@dataclass
class ChangeRecord:
    """A single DML change captured from a pg_notify payload (or polling)."""

    id: UUID = field(default_factory=uuid4)
    session_id: str = ""
    table_name: str = ""
    feature_id: str | None = None
    operation: ChangeOperation = ChangeOperation.INSERT
    old_values: dict[str, Any] = field(default_factory=dict)
    new_values: dict[str, Any] = field(default_factory=dict)
    old_geom_wkt: str | None = None
    new_geom_wkt: str | None = None
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ChangeSet:
    """A collection of ChangeRecords grouped by client transaction."""

    id: UUID = field(default_factory=uuid4)
    session_id: str = ""
    source_client: str | None = None
    records: list[ChangeRecord] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    committed_at: datetime | None = None


@dataclass
class FiredTrigger:
    """Records the evaluation (and optional firing) of a Trigger for a ChangeRecord."""

    id: UUID = field(default_factory=uuid4)
    trigger_id: UUID = field(default_factory=uuid4)
    change_record_id: UUID | None = None
    changeset_id: UUID | None = None
    matched: bool = False
    actions_dispatched: list[str] = field(default_factory=list)
    eval_time_ms: float = 0.0
    result_summary: dict[str, Any] = field(default_factory=dict)
    fired_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cascade_depth: int = 1


# ---------------------------------------------------------------------------
# Persistent project
# ---------------------------------------------------------------------------


@dataclass
class Project:
    """A persistent project grouping multiple datasets, rules, and triggers."""

    id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    schema_name: str = "public"
    engine_backend: str = "duckdb"
    dsn: str | None = None
    datasets: list[UUID] = field(default_factory=list)
    rules: list[UUID] = field(default_factory=list)
    triggers: list[UUID] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
