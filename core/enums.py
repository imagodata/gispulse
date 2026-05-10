"""Core enumerations for GISPulse domain models."""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TriggerEvent(str, Enum):
    DATA_CHANGED = "data_changed"
    GEOMETRY_CHANGED = "geometry_changed"
    STATUS_CHANGED = "status_changed"
    MANUAL = "manual"
    THRESHOLD_CROSSED = "threshold_crossed"
    CONSTRAINT_VIOLATED = "constraint_violated"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    FEATURE_CREATED = "feature_created"
    FEATURE_UPDATED = "feature_updated"
    FEATURE_DELETED = "feature_deleted"
    LAYER_ADDED = "layer_added"
    LAYER_REMOVED = "layer_removed"


class DataCategory(str, Enum):
    """Catégorie de données spatiales supportées par GISPulse."""

    VECTOR = "vector"
    RASTER = "raster"
    POINT_CLOUD = "point_cloud"
    MESH_3D = "mesh_3d"
    NETWORK = "network"
    TABULAR_GEO = "tabular_geo"
    SPATIO_TEMPORAL = "spatio_temporal"


class ProcessingMode(str, Enum):
    """Mode de traitement des jobs et des triggers."""

    SYNC = "SYNC"
    ASYNC = "ASYNC"
    HYBRID = "HYBRID"


class MessageStatus(str, Enum):
    """État d'un message dans le bus ESB de GISPulse."""

    NEW = "NEW"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DLQ = "DLQ"


class ExecutionMode(str, Enum):
    """Mode d'exécution d'un job ou d'un pipeline."""

    SESSION = "session"  # PostGIS éphémère spin-up/tear-down
    PERSISTENT = "persistent"  # PostGIS central permanent


class TriggerCategory(str, Enum):
    """Catégorie fonctionnelle de trigger."""

    DATA = "data"
    TEMPORAL = "temporal"
    BUSINESS_RULE = "business_rule"
    CONSTRAINT = "constraint"
    INTEGRATION = "integration"


class TriggerType(str, Enum):
    """Type de déclencheur."""

    # --- Data triggers ---
    DML = "dml"
    THRESHOLD = "threshold"
    COMPOSITE = "composite"
    # --- Temporal triggers ---
    SCHEDULE = "schedule"
    # --- Business rule triggers ---
    VALIDATION = "validation"
    BUSINESS_RULE = "business_rule"
    # --- Constraint triggers ---
    TOPOLOGY = "topology"
    SPATIAL_CONSTRAINT = "spatial_constraint"
    # --- Integration triggers ---
    API = "api"
    ESB_EVENT = "esb_event"
    WEBHOOK_IN = "webhook_in"


class ChangeOperation(str, Enum):
    """DML operation that produced a ChangeRecord.

    v1.6.0 (#119) introduced ``UPDATE_GEOM`` / ``UPDATE_ATTR`` / ``BULK`` as
    granular variants. ``UPDATE`` remains accepted for backward compatibility
    on the config surface — the watcher resolves a coarse ``UPDATE`` row from
    the change log to the matching granular value via the row's
    ``geom_changed`` flag before handing the :class:`ChangeRecord` to the
    evaluator.
    """

    INSERT = "INSERT"
    UPDATE = "UPDATE"
    UPDATE_GEOM = "UPDATE_GEOM"
    UPDATE_ATTR = "UPDATE_ATTR"
    DELETE = "DELETE"
    BULK = "BULK"


class RuleScope(str, Enum):
    """Hierarchical scope for rule application.

    Rules are resolved in cascade: global → project → dataset → layer.
    """

    GLOBAL = "global"
    PROJECT = "project"
    DATASET = "dataset"
    LAYER = "layer"


class RelationType(str, Enum):
    """Type of relationship between two layers."""

    FK = "fk"
    SPATIAL = "spatial"
    ATTRIBUTE = "attribute"
    CUSTOM = "custom"


class ComputationRefreshMode(str, Enum):
    """When a computed field is recalculated."""

    ON_CHANGE = "on_change"
    ON_SCHEDULE = "on_schedule"
    MANUAL = "manual"


class SessionStatus(str, Enum):
    """Status d'une session PostGIS éphémère."""

    PROVISIONING = "provisioning"
    ACTIVE = "active"
    EXPIRED = "expired"
    TORN_DOWN = "torn_down"
    FAILED = "failed"


class SessionBackend(str, Enum):
    """Backend moteur d'une session GISPulse."""

    POSTGIS = "postgis"
    SPATIALITE = "spatialite"


class MapVisibility(str, Enum):
    """Visibilité d'une carte Cocarte publiée ou en brouillon."""

    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"
