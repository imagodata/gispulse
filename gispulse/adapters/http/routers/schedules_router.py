"""
Schedules router for the GISPulse HTTP API.

Endpoints (Pro tier only):
    POST   /schedules              -- create a scheduled pipeline
    GET    /schedules              -- list all schedules
    GET    /schedules/{id}         -- detail + run history
    PATCH  /schedules/{id}         -- update cron, enabled, config
    DELETE /schedules/{id}         -- remove a schedule
    POST   /schedules/{id}/run-now -- execute immediately (bypass cron)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gispulse.adapters.http.rate_limit import limiter
from persistence.tier import TierError, check_tier

log = logging.getLogger(__name__)

router = APIRouter(prefix="/schedules", tags=["schedules"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ScheduleCreate(BaseModel):
    """Request body for creating a scheduled pipeline."""

    name: str = Field(..., min_length=1, max_length=200)
    cron_expression: str = Field(
        ...,
        min_length=5,
        description="Standard cron expression, e.g. '0 */6 * * *'",
    )
    pipeline_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Pipeline configuration (rules, input, output).",
    )
    enabled: bool = True
    created_by: str | None = None


class ScheduleUpdate(BaseModel):
    """Request body for patching a scheduled pipeline."""

    name: str | None = None
    cron_expression: str | None = None
    pipeline_config: dict[str, Any] | None = None
    enabled: bool | None = None


class ScheduleResponse(BaseModel):
    """Response model for a scheduled pipeline."""

    id: str
    name: str
    cron_expression: str
    pipeline_config: dict[str, Any]
    enabled: bool
    last_run: datetime | None = None
    next_run: datetime | None = None
    created_by: str | None = None


# ---------------------------------------------------------------------------
# Dependency: get scheduler from app.state
# ---------------------------------------------------------------------------


def _get_scheduler(request: Request):
    """Return the PipelineScheduler from app.state, or raise 503."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(
            status_code=503,
            detail="Scheduler not available. Requires GISPulse Pro tier.",
        )
    return scheduler


def _require_pro():
    """Tier gate: raises 403 if not Pro or above."""
    try:
        check_tier("pro")
    except TierError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


def _schedule_to_response(sp) -> ScheduleResponse:
    return ScheduleResponse(
        id=str(sp.id),
        name=sp.name,
        cron_expression=sp.cron_expression,
        pipeline_config=sp.pipeline_config,
        enabled=sp.enabled,
        last_run=sp.last_run,
        next_run=sp.next_run,
        created_by=sp.created_by,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=201, response_model=ScheduleResponse)
@limiter.limit("15/minute")
async def create_schedule(
    request: Request,
    body: ScheduleCreate,
    scheduler=Depends(_get_scheduler),
):
    """Create a new scheduled pipeline (Pro tier)."""
    _require_pro()

    from orchestration.scheduler import ScheduledPipeline, validate_cron_expression

    # Validate cron expression early for a clear 422 error
    try:
        validate_cron_expression(body.cron_expression)
    except (ValueError, ImportError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    sp = ScheduledPipeline(
        name=body.name,
        cron_expression=body.cron_expression,
        pipeline_config=body.pipeline_config,
        enabled=body.enabled,
        created_by=body.created_by,
    )

    sp = await scheduler.add(sp)
    return _schedule_to_response(sp)


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(
    scheduler=Depends(_get_scheduler),
):
    """List all scheduled pipelines."""
    _require_pro()
    schedules = await scheduler.list_schedules()
    return [_schedule_to_response(s) for s in schedules]


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: str,
    scheduler=Depends(_get_scheduler),
):
    """Get details of a scheduled pipeline."""
    _require_pro()
    sp = scheduler.get(schedule_id)
    if sp is None:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")
    return _schedule_to_response(sp)


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
@limiter.limit("30/minute")
async def update_schedule(
    schedule_id: str,
    body: ScheduleUpdate,
    request: Request,
    scheduler=Depends(_get_scheduler),
):
    """Update a scheduled pipeline (cron, enabled, config)."""
    _require_pro()

    if body.cron_expression is not None:
        from orchestration.scheduler import validate_cron_expression
        try:
            validate_cron_expression(body.cron_expression)
        except (ValueError, ImportError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    updated = await scheduler.update(
        schedule_id,
        cron_expression=body.cron_expression,
        enabled=body.enabled,
        pipeline_config=body.pipeline_config,
        name=body.name,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")
    return _schedule_to_response(updated)


@router.delete("/{schedule_id}", status_code=204)
@limiter.limit("30/minute")
async def delete_schedule(
    schedule_id: str,
    request: Request,
    scheduler=Depends(_get_scheduler),
):
    """Delete a scheduled pipeline."""
    _require_pro()
    removed = await scheduler.remove(schedule_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")


@router.post("/{schedule_id}/run-now")
@limiter.limit("20/minute")
async def run_schedule_now(
    schedule_id: str,
    request: Request,
    scheduler=Depends(_get_scheduler),
):
    """Execute a scheduled pipeline immediately, bypassing the cron schedule."""
    _require_pro()
    job_id = await scheduler.run_now(schedule_id)
    if job_id is None:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")
    return {"job_id": job_id, "message": "Pipeline enqueued for immediate execution."}
