"""
Pydantic schemas for the GISPulse HTTP API.

All request/response models are defined here to keep routers thin and
to decouple the HTTP surface from the internal domain dataclasses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginatedResponse(BaseModel):
    """Generic paginated list response."""

    items: list[Any] = Field(..., description="Page of results.")
    total: int = Field(..., description="Total number of items in the collection.")
    limit: int = Field(..., description="Maximum items per page.")
    offset: int = Field(..., description="Number of items skipped.")


# ---------------------------------------------------------------------------
# Capability schemas
# ---------------------------------------------------------------------------


class CapabilityInfo(BaseModel):
    """Metadata for a registered capability."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "buffer",
                "description": "Buffer geometries by a given distance.",
                "json_schema": {"type": "object", "properties": {"distance": {"type": "number"}}},
            }
        }
    )

    name: str = Field(..., description="Unique identifier of the capability.")
    description: str = Field(..., description="Human-readable description of the capability.")
    json_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema describing the capability's configuration parameters.",
    )


# ---------------------------------------------------------------------------
# Rule schemas
# ---------------------------------------------------------------------------


class RuleCreate(BaseModel):
    """Payload to create a new Rule."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "name": "buffer_50m",
                "description": "Apply a 50 m buffer around all features.",
                "scope": "global",
                "capability": "buffer",
                "config": {"distance": 50},
                "enabled": True,
            }
        },
    )

    name: str = Field(..., description="Unique name for this rule.")
    description: str = Field("", description="Optional human-readable description.")
    scope: str = Field("global", description="Scope to which the rule applies (e.g. 'global').")
    capability: str = Field(..., description="Name of the capability to execute.")
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Capability-specific configuration parameters.",
    )
    enabled: bool = Field(True, description="Whether this rule is active.")

    @field_validator("capability")
    @classmethod
    def _capability_must_be_registered(cls, v: str) -> str:
        from gispulse.capabilities import registry

        registry._ensure_defaults_loaded()
        if v not in registry.REGISTRY:
            known = sorted(registry.REGISTRY.keys())
            raise ValueError(
                f"Unknown capability '{v}'. Registered capabilities: {known}"
            )
        return v


class RuleResponse(BaseModel):
    """Serialised Rule returned by the API."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "name": "buffer_50m",
                "description": "Apply a 50 m buffer around all features.",
                "scope": "global",
                "capability": "buffer",
                "config": {"distance": 50},
                "enabled": True,
            }
        }
    )

    id: UUID = Field(..., description="Unique identifier of the rule.")
    name: str = Field(..., description="Name of the rule.")
    description: str = Field(..., description="Description of the rule.")
    scope: str = Field(..., description="Scope of the rule.")
    capability: str = Field(..., description="Capability executed by this rule.")
    config: dict[str, Any] = Field(..., description="Capability configuration.")
    enabled: bool = Field(..., description="Whether this rule is active.")


# ---------------------------------------------------------------------------
# Trigger schemas
# ---------------------------------------------------------------------------


class TriggerCreate(BaseModel):
    """Payload to create a new Trigger."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "name": "on_dataset_load",
                "description": "Fire when a new dataset is loaded",
                "event": "dataset.loaded",
                "trigger_type": "api",
                "category": "integration",
                "severity": "info",
                "rule_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "conditions": {},
                "enabled": True,
            }
        },
    )

    name: str = Field(..., description="Unique name for this trigger.")
    description: str = Field("", description="Human-readable description.")
    event: str = Field("manual", description="Event that activates this trigger.")
    trigger_type: str = Field("api", description="Trigger type (e.g. 'dml', 'threshold', 'validation').")
    category: str = Field("data", description="Functional category (data, temporal, business_rule, constraint, integration).")
    severity: str = Field("info", description="Severity level (info, warning, error, critical).")
    rule_id: UUID | None = Field(None, description="Optional rule to execute on trigger.")
    conditions: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional conditions that must be met for the trigger to fire.",
    )
    enabled: bool = Field(True, description="Whether this trigger is active.")
    auto_eval: bool = Field(False, description="Auto-evaluate on each changeset and stream results via SSE.")


class TriggerResponse(BaseModel):
    """Serialised Trigger returned by the API."""

    id: UUID = Field(..., description="Unique identifier of the trigger.")
    name: str = Field(..., description="Name of the trigger.")
    description: str = Field("", description="Description of the trigger.")
    event: str = Field(..., description="Event that activates this trigger.")
    trigger_type: str = Field(..., description="Trigger type.")
    category: str = Field("data", description="Functional category.")
    severity: str = Field("info", description="Severity level.")
    rule_id: UUID | None = Field(None, description="Rule executed on trigger.")
    conditions: dict[str, Any] = Field(..., description="Trigger conditions.")
    predicates: list[dict[str, Any]] = Field(default_factory=list, description="Structured predicates.")
    predicate_logic: str = Field("AND", description="Logic for combining predicates (AND/OR).")
    actions: list[dict[str, Any]] = Field(default_factory=list, description="Actions dispatched on match.")
    enabled: bool = Field(..., description="Whether this trigger is active.")
    auto_eval: bool = Field(False, description="Auto-evaluate on each changeset.")


# ---------------------------------------------------------------------------
# Evaluate schemas (P-8 #85)
# ---------------------------------------------------------------------------


class ChangeRecordIn(BaseModel):
    """Inbound ChangeRecord for manual trigger evaluation."""

    session_id: str = Field("", description="Session schema name.")
    table_name: str = Field("", description="Qualified table name.")
    feature_id: str | None = Field(None, description="PK value of the modified feature.")
    operation: str = Field("INSERT", description="DML operation: INSERT | UPDATE | DELETE.")
    old_values: dict[str, Any] = Field(default_factory=dict)
    new_values: dict[str, Any] = Field(default_factory=dict)


class EvaluateRequest(BaseModel):
    """Payload for POST /triggers/{id}/evaluate."""

    records: list[ChangeRecordIn] = Field(
        ..., description="ChangeRecords to evaluate against the trigger."
    )


class FiredTriggerOut(BaseModel):
    """Serialised FiredTrigger returned by the evaluate endpoint."""

    id: UUID
    trigger_id: UUID
    change_record_id: UUID | None
    matched: bool
    actions_dispatched: list[str]
    eval_time_ms: float
    result_summary: dict[str, Any]
    cascade_depth: int
    fired_at: datetime


# ---------------------------------------------------------------------------
# Job schemas
# ---------------------------------------------------------------------------


class JobCreate(BaseModel):
    """Payload to create and run a new Job."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "name": "batch_buffer",
                "dataset_id": None,
                "parameters": {"rule_ids": ["3fa85f64-5717-4562-b3fc-2c963f66afa6"]},
            }
        }
    )

    name: str = Field(..., description="Name of the job.")
    dataset_id: UUID | None = Field(None, description="Optional dataset to process.")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Execution parameters (e.g. rule_ids to apply).",
    )


class JobResponse(BaseModel):
    """Serialised Job returned by the API."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "name": "batch_buffer",
                "status": "completed",
                "dataset_id": None,
                "parameters": {},
                "created_at": "2026-01-01T00:00:00",
                "started_at": "2026-01-01T00:00:01",
                "completed_at": "2026-01-01T00:00:02",
            }
        }
    )

    id: UUID = Field(..., description="Unique identifier of the job.")
    name: str = Field(..., description="Name of the job.")
    status: str = Field(..., description="Current status: pending, running, completed, failed.")
    dataset_id: UUID | None = Field(None, description="Dataset being processed.")
    parameters: dict[str, Any] = Field(..., description="Job parameters.")
    created_at: datetime = Field(..., description="Timestamp when the job was created.")
    started_at: datetime | None = Field(None, description="Timestamp when execution started.")
    completed_at: datetime | None = Field(None, description="Timestamp when execution finished.")
    result_path: str | None = Field(None, description="Path to the result file.")
    error_message: str | None = Field(None, description="Error details if the job failed.")
    duration_seconds: float | None = Field(None, description="Total execution time in seconds.")
    attempts: int = Field(0, description="Number of execution attempts.")


# ---------------------------------------------------------------------------
# Dataset schemas
# ---------------------------------------------------------------------------


class DatasetResponse(BaseModel):
    """Serialised Dataset returned by the API."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "name": "parcels",
                "source_path": "/data/parcels.gpkg",
                "data_category": "vector",
                "crs": "EPSG:4326",
                "format": "GPKG",
                "metadata": {},
                "created_at": "2026-01-01T00:00:00",
            }
        }
    )

    id: UUID = Field(..., description="Unique identifier of the dataset.")
    name: str = Field(..., description="Name of the dataset.")
    source_path: str | None = Field(None, description="Path to the source file.")
    data_category: str = Field(..., description="Category: vector, raster, or pointcloud.")
    crs: str = Field(..., description="Coordinate reference system (e.g. EPSG:4326).")
    format: str | None = Field(None, description="File format (e.g. GPKG, GeoTIFF).")
    metadata: dict[str, Any] = Field(..., description="Additional dataset metadata.")
    created_at: datetime = Field(..., description="Timestamp when the dataset was registered.")


# ---------------------------------------------------------------------------
# OGC source schemas
# ---------------------------------------------------------------------------


class OGCDatasetCreate(BaseModel):
    """Payload to register a remote OGC service as a dataset (lazy, no download)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "name": "cadastre_wfs",
                "source_type": "wfs",
                "url": "https://data.example.com/wfs",
                "layer_name": "cadastre:parcelles",
                "version": "2.0.0",
                "crs": "EPSG:4326",
                "auth": None,
                "max_features": 5000,
            }
        }
    )

    name: str = Field(..., description="Human-readable dataset name.")
    source_type: str = Field(
        ..., description="OGC service type: 'wfs' or 'ogc_api_features'."
    )
    url: str = Field(..., description="Base URL of the OGC service.")
    layer_name: str = Field(..., description="Layer / collection name on the service.")
    version: str = Field("2.0.0", description="WFS version (ignored for OGC API Features).")
    crs: str = Field("EPSG:4326", description="Coordinate reference system.")
    auth: dict[str, str] | None = Field(
        None, description="Optional auth config (apikey, basic, bearer)."
    )
    max_features: int | None = Field(
        None, description="Max features per page (default server-side or 1000)."
    )

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("Only http:// and https:// URLs are accepted")
        return v


class CatalogImportRequest(BaseModel):
    """Import a catalog entry as a local dataset, optionally clipped to a bbox."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "entry_id": "flux:ign:ign-bdtopo-wfs",
                "bbox": [2.2, 48.8, 2.5, 48.9],
                "crs": "EPSG:4326",
                "max_features": 5000,
                "name": "BD TOPO — Paris",
            }
        }
    )

    entry_id: str = Field(..., description="Full catalog entry ID (e.g. 'flux:ign:ign-bdtopo-wfs').")
    bbox: list[float] | None = Field(
        None,
        description="Bounding box [west, south, east, north] in WGS84.",
        min_length=4,
        max_length=4,
    )
    crs: str = Field("EPSG:4326", description="Target CRS for the imported data.")
    max_features: int | None = Field(None, ge=1, le=100000, description="Max features to fetch.")
    name: str | None = Field(None, description="Override name for the dataset.")


# ---------------------------------------------------------------------------
# Worldwide aggregator schemas (EPIC #226 — A10 #236)
# ---------------------------------------------------------------------------


class VirtualDatasetCreate(BaseModel):
    """Create a lazy virtual dataset from one worldwide-catalogue entry."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"entry_id": "overture-places", "source": "worldwide"}
        },
    )

    entry_id: str = Field(..., description="Catalogue entry id (e.g. 'overture-places').")
    source: str = Field("worldwide", description="Data source name the entry belongs to.")


class VirtualDatasetMaterialize(BaseModel):
    """Materialise a virtual dataset into a real local project dataset."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"name": "Overture places — Paris", "bbox": [2.2, 48.8, 2.5, 48.9]}
        },
    )

    name: str | None = Field(None, description="Override name for the created dataset.")
    bbox: list[float] | None = Field(
        None,
        description="Bounding box [west, south, east, north] pushed into the fetch.",
        min_length=4,
        max_length=4,
    )
    crs: str = Field("EPSG:4326", description="Target CRS for the materialised data.")


# ---------------------------------------------------------------------------
# Scenario schemas
# ---------------------------------------------------------------------------


class ScenarioCreate(BaseModel):
    """Payload to create a new Scenario."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "name": "flood_risk_assessment",
                "dataset_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "jobs": [],
                "rules": [],
                "metadata": {"region": "Ile-de-France"},
            }
        }
    )

    name: str = Field(..., description="Name of the scenario.")
    dataset_id: UUID | None = Field(None, description="Dataset associated with this scenario.")
    jobs: list[UUID] = Field(default_factory=list, description="Ordered list of job IDs.")
    rules: list[UUID] = Field(default_factory=list, description="Rules applied in the scenario.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Scenario metadata.")


class ScenarioResponse(BaseModel):
    """Serialised Scenario returned by the API."""

    id: UUID = Field(..., description="Unique identifier of the scenario.")
    name: str = Field(..., description="Name of the scenario.")
    dataset_id: UUID | None = Field(None, description="Dataset associated with this scenario.")
    jobs: list[UUID] = Field(..., description="Ordered list of job IDs.")
    rules: list[UUID] = Field(..., description="Rules applied in the scenario.")
    metadata: dict[str, Any] = Field(..., description="Scenario metadata.")
    created_at: datetime = Field(..., description="Timestamp when the scenario was created.")
    version: int = Field(..., description="Incremental version counter.")


# ---------------------------------------------------------------------------
# Validation schemas
# ---------------------------------------------------------------------------


class ValidationErrorItem(BaseModel):
    """Single validation failure."""

    field: str = Field(..., description="Name of the field that failed validation.")
    message: str = Field(..., description="Human-readable error message.")


class ValidationErrorResponse(BaseModel):
    """Response body for a failed validation."""

    valid: bool = Field(..., description="True when all checks pass, False otherwise.")
    errors: list[ValidationErrorItem] = Field(
        ..., description="List of validation errors (empty when valid=True)."
    )


# ---------------------------------------------------------------------------
# Health schema
# ---------------------------------------------------------------------------


class ComponentHealth(BaseModel):
    """Health status of an individual component."""
    status: str = Field(..., description="'ok' or 'error'")
    detail: str = Field(default="", description="Error detail if unhealthy")


class HealthResponse(BaseModel):
    """Health check response with component-level details."""

    model_config = ConfigDict(
        json_schema_extra={"example": {
            "status": "ok",
            "version": "0.1.0",
            "mode": "full",
            "checks": {"database": {"status": "ok"}, "disk": {"status": "ok"}},
        }}
    )

    status: str = Field(..., description="Service status ('ok' or 'degraded').")
    version: str = Field(..., description="API version string.")
    mode: str = Field(default="full", description="Running mode.")
    checks: dict[str, ComponentHealth] = Field(
        default_factory=dict,
        description="Per-component health checks.",
    )


# ---------------------------------------------------------------------------
# Viewer schemas (Phase 1.5)
# ---------------------------------------------------------------------------


class LayerFieldInfo(BaseModel):
    """Schema field in a spatial layer."""

    name: str = Field(..., description="Column name.")
    type: str = Field(..., description="Data type (int, float, str, date, geometry, etc.).")


class LayerStyleInfo(BaseModel):
    """Parsed style info extracted from GPKG layer_styles table."""

    layer_name: str = Field(..., description="Layer this style applies to.")
    style_name: str = Field("", description="Style name.")
    color: str | None = Field(None, description="Fill color as hex (#rrggbb).")
    opacity: float | None = Field(None, description="Fill opacity (0-1).")
    stroke_color: str | None = Field(None, description="Stroke color as hex.")
    stroke_width: float | None = Field(None, description="Stroke width in pixels.")


class ViewerLayerSummary(BaseModel):
    """Summary metadata for a layer in the viewer."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "parcelles",
                "geometry_type": "Polygon",
                "feature_count": 1234,
                "bbox": [2.2, 48.8, 2.5, 48.9],
                "crs": "EPSG:4326",
                "style": None,
            }
        }
    )

    name: str = Field(..., description="Layer name.")
    geometry_type: str | None = Field(None, description="Geometry type (Point, Polygon, etc.).")
    feature_count: int = Field(..., description="Number of features in the layer.")
    bbox: list[float] = Field(..., description="Bounding box [minx, miny, maxx, maxy].")
    crs: str = Field(..., description="Coordinate reference system.")
    style: LayerStyleInfo | None = Field(None, description="Parsed GPKG style (if available).")


class LayerListResponse(BaseModel):
    """Response for listing all layers."""

    file: str = Field(..., description="Source file path.")
    layers: list[ViewerLayerSummary] = Field(..., description="Available layers.")


class LayerDetailResponse(ViewerLayerSummary):
    """Detailed metadata for a single layer, including field schema."""

    fields: list[LayerFieldInfo] = Field(..., description="Attribute fields.")


# ---------------------------------------------------------------------------
# Feature editing schemas (Phase 2)
# ---------------------------------------------------------------------------


class FeatureCreate(BaseModel):
    """GeoJSON Feature to add to a layer."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [2.35, 48.85]},
                "properties": {"name": "Paris"},
            }
        }
    )

    type: str = Field("Feature", description="Must be 'Feature'.")
    geometry: dict[str, Any] = Field(..., description="GeoJSON geometry object.")
    properties: dict[str, Any] = Field(
        default_factory=dict, description="Feature properties."
    )


class FeatureUpdate(BaseModel):
    """Partial update for an existing feature."""

    geometry: dict[str, Any] | None = Field(
        None, description="Updated GeoJSON geometry (optional)."
    )
    properties: dict[str, Any] | None = Field(
        None, description="Updated properties (merged with existing)."
    )


# ---------------------------------------------------------------------------
# Relation schemas (Hybrid Schema)
# ---------------------------------------------------------------------------


class ComputedFieldIn(BaseModel):
    """A computed field definition attached to a relation."""

    name: str = Field(..., description="Target field name.")
    expression: str = Field(..., description="SQL / aggregation expression.")
    target_field: str = Field("", description="Explicit target column (defaults to name).")
    agg_function: str | None = Field(None, description="Aggregation: COUNT, SUM, AVG, MIN, MAX, STRING_AGG.")
    source_field: str | None = Field(None, description="Field on source layer to aggregate.")
    refresh_mode: str = Field("on_change", description="When to recalculate: on_change, on_schedule, manual.")
    cron: str | None = Field(None, description="Cron expression (if refresh_mode=on_schedule).")


class RelationCreate(BaseModel):
    """Payload to create a new table relation."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "source_layer_name": "batiments",
                "target_layer_name": "parcelles",
                "relation_type": "spatial",
                "spatial_op": "intersects",
                "label": "batiments intersecte parcelles",
            }
        }
    )

    source_layer_id: UUID | None = Field(None, description="Source layer UUID.")
    target_layer_id: UUID | None = Field(None, description="Target layer UUID.")
    source_layer_name: str = Field("", description="Source layer human name.")
    target_layer_name: str = Field("", description="Target layer human name.")
    relation_type: str = Field("spatial", description="fk, spatial, attribute, custom.")
    source_field: str | None = Field(None, description="Source field (None = geometry).")
    target_field: str | None = Field(None, description="Target field (None = geometry).")
    spatial_op: str | None = Field(None, description="Spatial predicate: intersects, within, contains, etc.")
    spatial_config: dict[str, Any] = Field(default_factory=dict, description="buffer_m, distance, etc.")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Detection confidence.")
    confirmed: bool = Field(False, description="User-confirmed relation.")
    label: str = Field("", description="User-facing label.")


class RelationUpdate(BaseModel):
    """Partial update for an existing relation."""

    source_layer_name: str | None = None
    target_layer_name: str | None = None
    relation_type: str | None = None
    source_field: str | None = None
    target_field: str | None = None
    spatial_op: str | None = None
    spatial_config: dict[str, Any] | None = None
    confidence: float | None = None
    confirmed: bool | None = None
    label: str | None = None


class RelationResponse(BaseModel):
    """Serialised TableRelation returned by the API."""

    id: UUID = Field(..., description="Unique identifier.")
    source_layer_id: UUID | None = Field(None, description="Source layer UUID.")
    target_layer_id: UUID | None = Field(None, description="Target layer UUID.")
    source_layer_name: str = Field(..., description="Source layer name.")
    target_layer_name: str = Field(..., description="Target layer name.")
    relation_type: str = Field(..., description="Relation type.")
    source_field: str | None = Field(None, description="Source field.")
    target_field: str | None = Field(None, description="Target field.")
    spatial_op: str | None = Field(None, description="Spatial predicate.")
    spatial_config: dict[str, Any] = Field(default_factory=dict, description="Spatial config.")
    confidence: float = Field(..., description="Detection confidence.")
    confirmed: bool = Field(..., description="User-confirmed.")
    auto_detected: bool = Field(False, description="Auto-detected by relation detector.")
    label: str = Field("", description="Display label.")
    trigger_id: UUID | None = Field(None, description="Attached trigger UUID.")
    computed_fields: list[ComputedFieldIn] = Field(default_factory=list, description="Computed field definitions.")
    created_at: datetime = Field(..., description="Creation timestamp.")
    updated_at: datetime = Field(..., description="Last update timestamp.")


class AttachTriggerRequest(BaseModel):
    """Attach an existing trigger to a relation."""

    trigger_id: UUID = Field(..., description="Trigger UUID to attach.")


class AddComputationRequest(BaseModel):
    """Add a computed field to a relation."""

    name: str = Field(..., description="Target field name.")
    expression: str = Field(..., description="SQL / aggregation expression.")
    target_field: str = Field("", description="Explicit target column.")
    agg_function: str | None = Field(None, description="COUNT, SUM, AVG, etc.")
    source_field: str | None = Field(None, description="Source field to aggregate.")
    refresh_mode: str = Field("on_change", description="on_change, on_schedule, manual.")
    cron: str | None = Field(None, description="Cron if on_schedule.")


class PreviewSQLResponse(BaseModel):
    """Generated SQL preview for a relation's computed fields."""

    relation_id: UUID = Field(..., description="Relation UUID.")
    sql_statements: list[str] = Field(..., description="Generated SQL statements.")


# ---------------------------------------------------------------------------
# Session schemas (P-6 #90)
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    """Payload pour créer une session PostGIS éphémère."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "source_client": "portal",
                "ttl_hours": 8,
            }
        }
    )

    source_client: str | None = Field(
        None, description="Client d'origine: 'qgis', 'arcgis', 'portal', 'cli'."
    )
    ttl_hours: int = Field(8, description="Durée de vie de la session en heures.", ge=1, le=72)


class SessionResponse(BaseModel):
    """Session PostGIS éphémère retournée par l'API."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "schema_name": "sess_3fa85f645717",
                "pg_role": "sess_3fa85f645717",
                "pg_password": "s3cr3t",
                "pg_dsn": None,
                "pg_notify_channel": "gispulse_sess_sess_3fa85f645717",
                "status": "active",
                "source_client": "portal",
                "ttl_hours": 8,
                "expires_at": "2026-03-29T00:00:00Z",
                "created_at": "2026-03-28T16:00:00Z",
            }
        }
    )

    id: UUID = Field(..., description="Identifiant unique de la session.")
    schema_name: str = Field(..., description="Nom du schéma PostgreSQL dédié.")
    pg_role: str = Field(..., description="Rôle PostgreSQL éphémère.")
    pg_password: str = Field(..., description="Mot de passe du rôle.")
    pg_dsn: str | None = Field(None, description="DSN de connexion pour les clients.")
    pg_notify_channel: str = Field(..., description="Canal pg_notify de la session.")
    status: str = Field(..., description="Statut: provisioning, active, expired, torn_down.")
    source_client: str | None = Field(None, description="Client d'origine.")
    ttl_hours: int = Field(..., description="Durée de vie en heures.")
    expires_at: datetime | None = Field(None, description="Date d'expiration.")
    created_at: datetime = Field(..., description="Date de création.")
