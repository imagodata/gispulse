"""Pydantic models mirroring the GISPulse HTTP API schemas.

These are intentionally duplicated from the server-side schemas so that
the SDK remains a standalone installable package with no backend imports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginatedResponse(BaseModel):
    items: List[Any] = Field(default_factory=list)
    total: int = 0
    limit: int = 100
    offset: int = 0


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    version: str


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------


class CapabilityInfo(BaseModel):
    name: str
    description: str
    json_schema: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    source_path: Optional[str] = None
    data_category: str
    crs: str
    format: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------


class RuleCreate(BaseModel):
    name: str
    description: str = ""
    scope: str = "global"
    capability: str
    config: dict = Field(default_factory=dict)
    enabled: bool = True


class RuleResponse(BaseModel):
    id: UUID
    name: str
    description: str = ""
    scope: str
    capability: str
    config: dict = Field(default_factory=dict)
    enabled: bool


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


class TriggerCreate(BaseModel):
    name: str
    description: str = ""
    event: str = "manual"
    trigger_type: str = "api"
    category: str = "data"
    severity: str = "info"
    rule_id: Optional[UUID] = None
    conditions: dict = Field(default_factory=dict)
    enabled: bool = True
    auto_eval: bool = False


class TriggerResponse(BaseModel):
    id: UUID
    name: str
    description: str = ""
    event: str
    trigger_type: str
    category: str = "data"
    severity: str = "info"
    rule_id: Optional[UUID] = None
    conditions: dict = Field(default_factory=dict)
    predicates: List[dict] = Field(default_factory=list)
    predicate_logic: str = "AND"
    actions: List[dict] = Field(default_factory=list)
    enabled: bool
    auto_eval: bool = False


class FiredTriggerOut(BaseModel):
    id: UUID
    trigger_id: UUID
    change_record_id: Optional[UUID] = None
    matched: bool
    actions_dispatched: List[str] = Field(default_factory=list)
    eval_time_ms: float
    result_summary: dict = Field(default_factory=dict)
    cascade_depth: int = 0
    fired_at: datetime


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


class JobCreate(BaseModel):
    name: str
    dataset_id: Optional[UUID] = None
    parameters: dict = Field(default_factory=dict)


class JobResponse(BaseModel):
    id: UUID
    name: str
    status: str
    dataset_id: Optional[UUID] = None
    parameters: dict = Field(default_factory=dict)
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result_path: Optional[str] = None
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None
    attempts: int = 0


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


class ScenarioCreate(BaseModel):
    name: str
    dataset_id: Optional[UUID] = None
    jobs: List[UUID] = Field(default_factory=list)
    rules: List[UUID] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ScenarioResponse(BaseModel):
    id: UUID
    name: str
    dataset_id: Optional[UUID] = None
    jobs: List[UUID] = Field(default_factory=list)
    rules: List[UUID] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    version: int


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    source_client: Optional[str] = None
    ttl_hours: int = 8


class SessionResponse(BaseModel):
    id: UUID
    schema_name: str
    pg_role: str
    pg_password: str
    pg_dsn: Optional[str] = None
    pg_notify_channel: str
    status: str
    source_client: Optional[str] = None
    ttl_hours: int
    expires_at: Optional[datetime] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# OGC
# ---------------------------------------------------------------------------


class OGCDatasetCreate(BaseModel):
    name: str
    source_type: str
    url: str
    layer_name: str
    version: str = "2.0.0"
    crs: str = "EPSG:4326"
    auth: Optional[dict] = None
    max_features: Optional[int] = None


# ---------------------------------------------------------------------------
# Viewer / Layer
# ---------------------------------------------------------------------------


class LayerFieldInfo(BaseModel):
    name: str
    type: str


class ViewerLayerSummary(BaseModel):
    name: str
    geometry_type: Optional[str] = None
    feature_count: int
    bbox: List[float] = Field(default_factory=list)
    crs: str


class LayerListResponse(BaseModel):
    file: str
    layers: List[ViewerLayerSummary] = Field(default_factory=list)


class LayerDetailResponse(ViewerLayerSummary):
    fields: List[LayerFieldInfo] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Feature editing
# ---------------------------------------------------------------------------


class FeatureCreate(BaseModel):
    type: str = "Feature"
    geometry: dict
    properties: dict = Field(default_factory=dict)


class FeatureUpdate(BaseModel):
    geometry: Optional[dict] = None
    properties: Optional[dict] = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ValidationErrorItem(BaseModel):
    field: str
    message: str


class ValidationErrorResponse(BaseModel):
    valid: bool
    errors: List[ValidationErrorItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    schema_name: Optional[str] = None
    engine_backend: Optional[str] = None
    datasets: List[UUID] = Field(default_factory=list)
    rules: List[UUID] = Field(default_factory=list)
    triggers: List[UUID] = Field(default_factory=list)
    created_at: Optional[datetime] = None
