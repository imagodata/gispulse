"""Node Graph types for GISPulse pipeline DAGs (Phase 3A).

Defines the declarative graph model: NodeDef, EdgeDef, ActionDef,
and supporting types for spatial state tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class NodeType(str, Enum):
    """Type of node in a pipeline graph."""
    DATASET    = "dataset"
    CAPABILITY = "capability"
    RULE       = "rule"
    TRIGGER    = "trigger"
    ARTIFACT   = "artifact"
    LOOP       = "loop"
    BRANCH     = "branch"
    PARALLEL   = "parallel"
    CALCULATE  = "calculate"
    AGGREGATE  = "aggregate"


@dataclass
class NodePort:
    """Typed port on a node (input or output)."""
    name: str
    port_type: str = "gdf"   # gdf | scalar | config
    required: bool = True


@dataclass
class NodeDef:
    """Definition of a node in a pipeline graph."""
    id: str
    node_type: NodeType
    capability: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    bind: str | None = None
    body: list["NodeDef"] = field(default_factory=list)
    body_edges: list["EdgeDef"] = field(default_factory=list)


@dataclass
class EdgeDef:
    """Directed edge between two nodes in the graph."""
    source: str
    target: str
    handle: str = ""


class ActionType(str, Enum):
    """Types of actions dispatched after a predicate match."""
    NOTIFY           = "notify"
    SET_FIELD        = "set_field"
    UPDATE_AGGREGATE = "update_aggregate"
    RUN_JOB          = "run_job"
    RUN_GRAPH        = "run_graph"
    WEBHOOK          = "webhook"
    ENQUEUE          = "enqueue"
    LOG_EVENT        = "log_event"
    SEND_EMAIL       = "send_email"
    APPROVE          = "approve"
    REJECT           = "reject"
    FLAG_FEATURE     = "flag_feature"
    BLOCK_COMMIT     = "block_commit"
    RUN_SQL          = "run_sql"
    # v1.6.0 (#123): write a validation status onto the row, with
    # auto-create of the target columns on first use. Distinct from
    # SET_FIELD because the dispatcher manages the schema migration.
    TAG_FIELD        = "tag_field"


@dataclass
class ActionDef:
    """Declarative action triggered by ESB events."""
    action_type: ActionType
    config: dict[str, Any] = field(default_factory=dict)
    async_mode: bool = False


class SpatialState(str, Enum):
    """Spatial state of an object relative to a zone."""
    INSIDE  = "INSIDE"
    OUTSIDE = "OUTSIDE"
    UNKNOWN = "UNKNOWN"


class Transition(str, Enum):
    """Spatial transition events."""
    ENTER            = "ENTER"
    EXIT             = "EXIT"
    CROSS            = "CROSS"
    DWELL            = "DWELL"
    PROXIMITY_BREACH = "PROXIMITY_BREACH"


@dataclass
class EvalResult:
    """Result of predicate evaluation."""
    matched: bool
    transition: Transition | None = None
    matched_refs: list[UUID] = field(default_factory=list)
    eval_time_ms: float = 0.0


@dataclass
class ObjectState:
    """State Store entry for a tracked (object, zone) pair."""
    object_id: UUID
    predicate_id: UUID
    zone_id: UUID | None = None
    state: SpatialState = SpatialState.UNKNOWN
    entered_at: datetime | None = None
    last_evaluated: datetime | None = None
    last_geom_hash: int | None = None
