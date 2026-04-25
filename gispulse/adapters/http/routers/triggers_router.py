"""
Triggers router for the GISPulse HTTP API.

Endpoints:
    POST   /triggers                  — create a trigger
    GET    /triggers                  — list all triggers
    GET    /triggers/eval-stream      — SSE stream of trigger_fired events
    GET    /triggers/{id}             — detail for a single trigger
    PUT    /triggers/{id}             — update a trigger
    DELETE /triggers/{id}             — delete a trigger
    POST   /triggers/{id}/toggle      — enable/disable a trigger
    POST   /triggers/{id}/evaluate    — evaluate trigger against ChangeRecords (→ SSE)
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from gispulse.adapters.http.dependencies import get_trigger_repo
from gispulse.adapters.http.event_hub import get_event_hub
from gispulse.adapters.http.rate_limit import limiter
from gispulse.adapters.http.schemas import (
    ChangeRecordIn,
    EvaluateRequest,
    FiredTriggerOut,
    TriggerCreate,
    TriggerResponse,
)
from core.models import (
    ChangeOperation,
    ChangeRecord,
    Trigger,
    TriggerEvent,
    TriggerType,
)
from persistence.repository import Repository
from persistence.tier import TierError, check_tier, enforce_feature, get_current_tier
from rules.trigger_evaluator import TriggerEvaluator


# ---------------------------------------------------------------------------
# Tier gating
# ---------------------------------------------------------------------------
#
# Triggers come in two flavours, gated independently:
#
#   * ``local_triggers``  — Community + Pro. In-process bus + WebSocket
#     dispatch, single-process, best-effort durability. Capped at 5 active
#     triggers per process and forbids advanced action types (webhook,
#     cron, cascade>1, DLQ).
#   * ``esb_triggers``    — Pro only. Backed by PostgreSQL ``pg_notify``,
#     DLQ, circuit breakers, cron schedules, outbound webhooks. The
#     enforcement of this gate lives in :mod:`gispulse.adapters.esb`
#     (workers, ``TriggerManager.install``, ``esb_router``) — not here.
#
# The router only owns CRUD on ``Trigger`` model objects; persisting a
# Trigger does not by itself install a pg_notify trigger function. So
# every endpoint here is gated at the ``local_triggers`` level (Community
# OK), and the per-tier caps for Community accounts are enforced in
# :func:`_enforce_community_trigger_caps` at create/update time.
# ---------------------------------------------------------------------------


def _require_local_triggers() -> None:
    """Triggers CRUD requires the ``local_triggers`` feature (Community + Pro)."""
    try:
        enforce_feature("local_triggers")
    except TierError as exc:
        raise HTTPException(status_code=402, detail=str(exc))


# Cap rules for Community-tier ``local_triggers``. Pro has no caps
# (esb_triggers feature, full pg_notify pipeline).
_LOCAL_TRIGGER_MAX_ACTIVE = 5
_LOCAL_TRIGGER_MAX_CASCADE_DEPTH = 1
# Forbidden top-level keys / action shapes in trigger.conditions for Community.
# Anything that requires the ESB pipeline (DLQ, cron, outbound HTTP) lives
# behind the esb_triggers feature.
_LOCAL_TRIGGER_FORBIDDEN_KEYS = frozenset(
    {
        "webhook",
        "outbound_action",
        "outbound_webhook",
        "cron_schedule",
        "cron",
        "schedule",
        "dlq_enabled",
        "dlq",
    }
)


def _is_pro_or_above() -> bool:
    """Return True if the current tier has the ``esb_triggers`` feature."""
    try:
        check_tier("pro")
    except TierError:
        return False
    return True


def _enforce_community_trigger_caps(
    payload: "TriggerCreate",
    repo: "Repository",
    *,
    exclude_id: UUID | None = None,
) -> None:
    """Enforce the Community caps for ``local_triggers`` at create/update time.

    Pro+ skips all caps (it has the richer ``esb_triggers`` feature).

    The structural cap ``single_process_only`` is **not** enforced here:
    Community has no PostGIS engine and therefore no ``pg_notify`` —
    durability is single-process by construction. See pricing_catalog.yml
    `features.local_triggers.caps.single_process_only`.

    Raises:
        HTTPException(402): On any Community cap violation.
    """
    if _is_pro_or_above():
        return

    # 1. max_active_triggers cap (only counts enabled triggers, excludes
    #    the trigger being updated to allow toggling without 402).
    if payload.enabled:
        try:
            current_triggers = repo.list_all()
        except Exception:
            current_triggers = []
        active = sum(
            1
            for t in current_triggers
            if getattr(t, "enabled", False)
            and (exclude_id is None or getattr(t, "id", None) != exclude_id)
        )
        if active >= _LOCAL_TRIGGER_MAX_ACTIVE:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Community tier is capped at {_LOCAL_TRIGGER_MAX_ACTIVE} "
                    "active local triggers. Upgrade to Pro for unlimited "
                    "triggers (esb_triggers feature)."
                ),
            )

    # 2. Forbidden config shapes (webhook / cron / DLQ / cascade > 1).
    conditions = payload.conditions or {}
    if isinstance(conditions, dict):
        bad = _LOCAL_TRIGGER_FORBIDDEN_KEYS.intersection(conditions.keys())
        if bad:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Trigger config keys {sorted(bad)} require the "
                    "esb_triggers feature (Pro). "
                    "Community local_triggers do not support webhooks, "
                    "cron, DLQ, or cascade>1."
                ),
            )
        cascade = conditions.get("cascade_depth") or conditions.get("cascade")
        if isinstance(cascade, int) and cascade > _LOCAL_TRIGGER_MAX_CASCADE_DEPTH:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"cascade_depth={cascade} exceeds the Community cap of "
                    f"{_LOCAL_TRIGGER_MAX_CASCADE_DEPTH}. Upgrade to Pro."
                ),
            )

        # Inline action defs may also carry a forbidden shape.
        actions = conditions.get("actions") or []
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                action_type = str(action.get("action_type", "")).lower()
                if action_type in {"webhook", "http", "outbound_webhook"}:
                    raise HTTPException(
                        status_code=402,
                        detail=(
                            "Outbound webhook actions require the "
                            "esb_triggers feature (Pro)."
                        ),
                    )


router = APIRouter(
    prefix="/triggers",
    tags=["triggers"],
    dependencies=[Depends(_require_local_triggers)],
)


def _trigger_to_response(trigger: Trigger) -> TriggerResponse:
    return TriggerResponse(
        id=trigger.id,
        name=trigger.name,
        description=getattr(trigger, "description", ""),
        event=trigger.event.value if isinstance(trigger.event, TriggerEvent) else trigger.event,
        trigger_type=trigger.trigger_type.value if isinstance(trigger.trigger_type, TriggerType) else trigger.trigger_type,
        category=getattr(trigger, "category", "data") if not hasattr(trigger.category, "value") else trigger.category.value,
        severity=getattr(trigger, "severity", "info"),
        rule_id=trigger.rule_id,
        conditions=trigger.conditions,
        predicates=[_predicate_to_dict(p) for p in trigger.predicates] if trigger.predicates else [],
        predicate_logic=trigger.predicate_logic,
        actions=[{"action_type": a.action_type.value if hasattr(a.action_type, "value") else a.action_type, "config": a.config} for a in trigger.actions] if trigger.actions else [],
        enabled=trigger.enabled,
        auto_eval=trigger.auto_eval,
    )


def _predicate_to_dict(pred) -> dict:
    """Serialize a predicate (Attr/Geom/Compound) to a dict."""
    from dataclasses import asdict
    try:
        return asdict(pred)
    except Exception:
        return {"type": str(type(pred).__name__), "raw": str(pred)}


def _change_record_in_to_model(r: ChangeRecordIn) -> ChangeRecord:
    op_map = {
        "INSERT": ChangeOperation.INSERT,
        "UPDATE": ChangeOperation.UPDATE,
        "DELETE": ChangeOperation.DELETE,
    }
    return ChangeRecord(
        session_id=r.session_id,
        table_name=r.table_name,
        feature_id=r.feature_id,
        operation=op_map.get(r.operation.upper(), ChangeOperation.INSERT),
        old_values=r.old_values,
        new_values=r.new_values,
    )


@router.post("", response_model=TriggerResponse, status_code=201)
@limiter.limit("30/minute")
def create_trigger(
    request: Request,
    payload: TriggerCreate,
    repo: Repository = Depends(get_trigger_repo),
) -> TriggerResponse:
    """Create a new Trigger and persist it."""
    _enforce_community_trigger_caps(payload, repo)

    from core.models import TriggerCategory
    trigger = Trigger(
        name=payload.name,
        description=payload.description,
        event=TriggerEvent(payload.event) if payload.event in TriggerEvent._value2member_map_ else TriggerEvent.MANUAL,
        trigger_type=TriggerType(payload.trigger_type) if payload.trigger_type in TriggerType._value2member_map_ else TriggerType.API,
        category=TriggerCategory(payload.category) if payload.category in TriggerCategory._value2member_map_ else TriggerCategory.DATA,
        severity=payload.severity,
        rule_id=payload.rule_id,
        conditions=payload.conditions,
        enabled=payload.enabled,
        auto_eval=payload.auto_eval,
    )
    repo.save(trigger)
    return _trigger_to_response(trigger)


@router.get("")
def list_triggers(
    limit: int = 50,
    offset: int = 0,
    repo: Repository = Depends(get_trigger_repo),
) -> dict:
    """Return paginated triggers."""
    all_items = repo.list_all()
    total = len(all_items)
    page = all_items[offset : offset + limit]
    return {
        "items": [_trigger_to_response(t) for t in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/eval-stream")
async def eval_stream(
    request: Request,
    trigger_id: UUID | None = None,
) -> StreamingResponse:
    """SSE stream of ``trigger_fired`` events.

    Clients connect once and receive a real-time stream of FiredTrigger
    payloads as Server-Sent Events.  Optional ``trigger_id`` query param
    filters the stream to a single trigger.

    Protocol::

        data: {"type": "trigger_fired", "data": {...}, "timestamp": "..."}\\n\\n
        : heartbeat\\n\\n   (every 15 s to keep the connection alive)
    """
    hub = get_event_hub()
    queue = hub.subscribe()
    filter_id = str(trigger_id) if trigger_id else None

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    raw = await asyncio.wait_for(queue.get(), timeout=15.0)
                    event = json.loads(raw)
                    # Filter by trigger_id if requested
                    if filter_id and event.get("data", {}).get("trigger_id") != filter_id:
                        continue
                    if event.get("type") == "trigger_fired":
                        yield f"data: {raw}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            hub.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{trigger_id}", response_model=TriggerResponse)
def get_trigger(
    trigger_id: UUID,
    repo: Repository = Depends(get_trigger_repo),
) -> TriggerResponse:
    """Return a single trigger by UUID."""
    trigger = repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found.")
    return _trigger_to_response(trigger)


@router.put("/{trigger_id}", response_model=TriggerResponse)
def update_trigger(
    trigger_id: UUID,
    payload: TriggerCreate,
    repo: Repository = Depends(get_trigger_repo),
) -> TriggerResponse:
    """Update an existing trigger."""
    trigger = repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found.")

    _enforce_community_trigger_caps(payload, repo, exclude_id=trigger_id)

    from core.models import TriggerCategory
    trigger.name = payload.name
    trigger.description = payload.description
    trigger.event = TriggerEvent(payload.event) if payload.event in TriggerEvent._value2member_map_ else TriggerEvent.MANUAL
    trigger.trigger_type = TriggerType(payload.trigger_type) if payload.trigger_type in TriggerType._value2member_map_ else TriggerType.API
    trigger.category = TriggerCategory(payload.category) if payload.category in TriggerCategory._value2member_map_ else TriggerCategory.DATA
    trigger.severity = payload.severity
    trigger.rule_id = payload.rule_id
    trigger.conditions = payload.conditions
    trigger.enabled = payload.enabled
    trigger.auto_eval = payload.auto_eval
    repo.save(trigger)
    return _trigger_to_response(trigger)


@router.delete("/{trigger_id}", status_code=204)
def delete_trigger(
    trigger_id: UUID,
    repo: Repository = Depends(get_trigger_repo),
) -> None:
    """Delete a trigger by UUID."""
    deleted = repo.delete(trigger_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found.")


@router.post("/{trigger_id}/toggle", response_model=TriggerResponse)
def toggle_trigger(
    trigger_id: UUID,
    repo: Repository = Depends(get_trigger_repo),
) -> TriggerResponse:
    """Toggle the enabled state of a trigger."""
    trigger = repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found.")

    trigger.enabled = not trigger.enabled
    repo.save(trigger)
    return _trigger_to_response(trigger)


# ---------------------------------------------------------------------------
# P-8 #85 — auto_eval + SSE feedback
# ---------------------------------------------------------------------------


@router.post("/{trigger_id}/evaluate", response_model=list[FiredTriggerOut])
@limiter.limit("60/minute")
def evaluate_trigger(
    request: Request,
    trigger_id: UUID,
    payload: EvaluateRequest,
    repo: Repository = Depends(get_trigger_repo),
) -> list[FiredTriggerOut]:
    """Evaluate a trigger against a list of ChangeRecords.

    Runs TriggerEvaluator synchronously and broadcasts each FiredTrigger
    to the EventHub (type ``trigger_fired``) so SSE clients receive
    real-time feedback. Returns the full list of FiredTrigger objects.
    """
    trigger = repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found.")

    records = [_change_record_in_to_model(r) for r in payload.records]
    evaluator = TriggerEvaluator()
    fired = evaluator.evaluate_changeset_records(records, [trigger])

    hub = get_event_hub()
    for ft in fired:
        hub.broadcast(
            "trigger_fired",
            {
                "trigger_id": str(ft.trigger_id),
                "change_record_id": str(ft.change_record_id) if ft.change_record_id else None,
                "matched": ft.matched,
                "actions_dispatched": ft.actions_dispatched,
                "eval_time_ms": ft.eval_time_ms,
                "result_summary": ft.result_summary,
                "cascade_depth": ft.cascade_depth,
            },
        )

    return [
        FiredTriggerOut(
            id=ft.id,
            trigger_id=ft.trigger_id,
            change_record_id=ft.change_record_id,
            matched=ft.matched,
            actions_dispatched=ft.actions_dispatched,
            eval_time_ms=ft.eval_time_ms,
            result_summary=ft.result_summary,
            cascade_depth=ft.cascade_depth,
            fired_at=ft.fired_at,
        )
        for ft in fired
    ]


# ---------------------------------------------------------------------------
# Trigger Operations CRUD (#404)
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field
from typing import Any


class TriggerOperationIn(BaseModel):
    """Payload to create/update a trigger operation (Forge spatial ops)."""

    phase: str = Field(..., description="'before' or 'after'.")
    operation: str = Field(..., description="Operation type (st_within, st_intersects, count_st_contains, etc.).")
    field: str = Field("", description="Target field to populate (BEFORE) or aggregate field (AFTER).")
    distant_table: str = Field("", description="Reference table for spatial lookup.")
    distant_field: str = Field("", description="Field in distant table to retrieve.")
    distant_filter: str | None = Field(None, description="Optional SQL filter on distant table.")
    order: int = Field(0, description="Execution order within phase.")
    enabled: bool = Field(True, description="Whether this operation is active.")
    coalesce: bool = Field(False, description="Skip if target field already has a value.")
    extra: dict[str, Any] = Field(default_factory=dict, description="Additional operation-specific params.")


class TriggerOperationOut(BaseModel):
    """A single trigger operation with its index (acts as op_id)."""

    op_id: int = Field(..., description="Index in the operations list (0-based).")
    phase: str
    operation: str
    field: str = ""
    distant_table: str = ""
    distant_field: str = ""
    distant_filter: str | None = None
    order: int = 0
    enabled: bool = True
    coalesce: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


def _get_operations(trigger: Trigger) -> list[dict[str, Any]]:
    """Extract the operations list from trigger.conditions."""
    conditions = trigger.conditions
    if isinstance(conditions, dict):
        return conditions.get("operations", [])
    # Typed conditions (DMLConditions etc.)
    return getattr(conditions, "operations", [])


def _set_operations(trigger: Trigger, operations: list[dict[str, Any]]) -> None:
    """Set the operations list in trigger.conditions."""
    if isinstance(trigger.conditions, dict):
        trigger.conditions["operations"] = operations
    elif hasattr(trigger.conditions, "operations"):
        trigger.conditions.operations = operations
    else:
        trigger.conditions = {"operations": operations}


def _op_to_response(index: int, op: dict[str, Any]) -> TriggerOperationOut:
    """Convert a raw operation dict to response model."""
    known_keys = {"phase", "operation", "field", "distant_table", "distant_field",
                  "distant_filter", "order", "enabled", "coalesce"}
    extra = {k: v for k, v in op.items() if k not in known_keys}
    return TriggerOperationOut(
        op_id=index,
        phase=op.get("phase", "before"),
        operation=op.get("operation", ""),
        field=op.get("field", ""),
        distant_table=op.get("distant_table", ""),
        distant_field=op.get("distant_field", ""),
        distant_filter=op.get("distant_filter"),
        order=op.get("order", 0),
        enabled=op.get("enabled", True),
        coalesce=op.get("coalesce", False),
        extra=extra,
    )


@router.get("/{trigger_id}/operations", response_model=list[TriggerOperationOut])
def list_operations(
    trigger_id: UUID,
    repo: Repository = Depends(get_trigger_repo),
) -> list[TriggerOperationOut]:
    """List all operations for a trigger."""
    trigger = repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found.")

    operations = _get_operations(trigger)
    return [_op_to_response(i, op) for i, op in enumerate(operations)]


@router.post("/{trigger_id}/operations", response_model=TriggerOperationOut, status_code=201)
def add_operation(
    trigger_id: UUID,
    payload: TriggerOperationIn,
    repo: Repository = Depends(get_trigger_repo),
) -> TriggerOperationOut:
    """Add an operation to a trigger's conditions.operations list."""
    trigger = repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found.")

    operations = _get_operations(trigger)

    # Build operation dict
    op_dict: dict[str, Any] = {
        "phase": payload.phase,
        "operation": payload.operation,
        "field": payload.field,
        "distant_table": payload.distant_table,
        "distant_field": payload.distant_field,
        "order": payload.order,
        "enabled": payload.enabled,
        "coalesce": payload.coalesce,
    }
    if payload.distant_filter:
        op_dict["distant_filter"] = payload.distant_filter
    if payload.extra:
        op_dict.update(payload.extra)

    operations.append(op_dict)
    _set_operations(trigger, operations)
    repo.save(trigger)

    return _op_to_response(len(operations) - 1, op_dict)


@router.put("/{trigger_id}/operations/{op_id}", response_model=TriggerOperationOut)
def update_operation(
    trigger_id: UUID,
    op_id: int,
    payload: TriggerOperationIn,
    repo: Repository = Depends(get_trigger_repo),
) -> TriggerOperationOut:
    """Update a specific operation by index."""
    trigger = repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found.")

    operations = _get_operations(trigger)
    if op_id < 0 or op_id >= len(operations):
        raise HTTPException(status_code=404, detail=f"Operation {op_id} not found (trigger has {len(operations)} operations).")

    op_dict: dict[str, Any] = {
        "phase": payload.phase,
        "operation": payload.operation,
        "field": payload.field,
        "distant_table": payload.distant_table,
        "distant_field": payload.distant_field,
        "order": payload.order,
        "enabled": payload.enabled,
        "coalesce": payload.coalesce,
    }
    if payload.distant_filter:
        op_dict["distant_filter"] = payload.distant_filter
    if payload.extra:
        op_dict.update(payload.extra)

    operations[op_id] = op_dict
    _set_operations(trigger, operations)
    repo.save(trigger)

    return _op_to_response(op_id, op_dict)


@router.delete("/{trigger_id}/operations/{op_id}", status_code=204)
def delete_operation(
    trigger_id: UUID,
    op_id: int,
    repo: Repository = Depends(get_trigger_repo),
) -> None:
    """Delete an operation by index."""
    trigger = repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found.")

    operations = _get_operations(trigger)
    if op_id < 0 or op_id >= len(operations):
        raise HTTPException(status_code=404, detail=f"Operation {op_id} not found (trigger has {len(operations)} operations).")

    operations.pop(op_id)
    _set_operations(trigger, operations)
    repo.save(trigger)
