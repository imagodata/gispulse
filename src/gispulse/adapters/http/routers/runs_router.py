"""Runs router — pipeline run history + run control surface (vague 5).

Endpoints:
    GET  /runs                    -- paginated list of PipelineRun objects
    GET  /runs/{run_id}           -- single run by UUID
    POST /runs/{run_id}/cancel    -- cancel a RUNNING run via its job_id
    POST /runs/{run_id}/resume    -- create a new job resuming a FAILED run
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from gispulse.core.logging import get_logger
from gispulse.core.models import JobStatus

log = get_logger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


def _get_run_repo(request: Request):
    """Return the RunRepository from app state."""
    repo = getattr(request.app.state, "run_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Run repository not available")
    return repo


def _get_job_queue(request: Request):
    """Return the JobQueue from app state (may be None)."""
    return getattr(request.app.state, "job_queue", None)


def _get_job_repo(request: Request):
    """Return the job repository from app state (may be None)."""
    return getattr(request.app.state, "job_repo", None)


def _run_to_dict(run) -> dict:
    return {
        "run_id": str(run.run_id),
        "source": run.source,
        "spec_ref": run.spec_ref,
        "scope": run.scope,
        "status": run.status.value,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "error": run.error,
        "job_id": getattr(run, "job_id", ""),
        "resumed_from_run_id": getattr(run, "resumed_from_run_id", ""),
        "steps_filter": getattr(run, "steps_filter", []),
        "steps": [
            {
                "step_id": s.step_id,
                "status": s.status.value,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "attempt": s.attempt,
                "error": s.error,
            }
            for s in run.steps
        ],
    }


@router.get("")
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    run_repo=Depends(_get_run_repo),
) -> dict:
    """Return paginated pipeline runs, most recent first."""
    items = run_repo.list_all(limit=limit, offset=offset)
    total = run_repo.count()
    return {
        "items": [_run_to_dict(r) for r in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{run_id}")
def get_run(
    run_id: UUID,
    run_repo=Depends(_get_run_repo),
) -> dict:
    """Return a single pipeline run by UUID."""
    run = run_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return _run_to_dict(run)


@router.post("/{run_id}/cancel", status_code=202)
async def cancel_run(
    run_id: UUID,
    request: Request,
    run_repo=Depends(_get_run_repo),
) -> dict:
    """Cancel a RUNNING pipeline run.

    Resolves run → job_id → delegates to job_queue.cancel(job_id).
    The cancel is asynchronous: the worker detects the cancellation via
    cancel_check() and transitions the run to FAILED error="cancelled"
    through the single state writer (RecordingSink). This endpoint never
    writes run state itself.

    Status codes:
        202  -- cancel request accepted (terminal state arrives via worker).
        404  -- run not found.
        409  -- run is already in a terminal state (COMPLETED or FAILED).
        409  -- run has no backing job (scenario/in-process or pre-existing runs).
        409  -- no job queue active, or the job is no longer cancellable.
    """
    run = run_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    if run.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "run_already_terminal",
                "status": run.status.value,
                "message": (
                    f"Run '{run_id}' is already in terminal state "
                    f"'{run.status.value}' and cannot be cancelled."
                ),
            },
        )

    job_id = getattr(run, "job_id", "")
    if not job_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "run_not_cancellable_no_job",
                "message": (
                    f"Run '{run_id}' has no job_id and cannot be cancelled via this "
                    "endpoint. This applies to scenario/in-process runs and to runs "
                    "created before the job-backed orchestration was deployed."
                ),
            },
        )

    job_queue = _get_job_queue(request)
    if job_queue is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "no_job_queue_active",
                "message": (
                    "No job queue is active on this instance. "
                    "Cancel requires a running job queue (InMemoryJobQueue or RedisJobQueue)."
                ),
            },
        )

    # Delegate to the queue cancel mechanism.
    # The worker detects the cancellation via cancel_check(), sets the run FAILED
    # with error='cancelled', and persists it via RecordingSink. The router must
    # NOT write run_repo here — that would race with the worker and violate
    # the single-writer contract (RecordingSink owns run state).
    cancelled = await job_queue.cancel(job_id)
    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "job_not_cancellable",
                "job_id": job_id,
                "message": (
                    f"Job '{job_id}' could not be cancelled — it may have already "
                    "reached a terminal state or is no longer in the queue."
                ),
            },
        )

    log.info("run_cancel_accepted", run_id=str(run_id), job_id=job_id)
    # 202 Accepted: the cancel is asynchronous. The worker will transition
    # the run to FAILED once it detects the cancellation flag.
    return {"run_id": str(run_id), "job_id": job_id, "status": "cancel_accepted"}


@router.post("/{run_id}/resume", status_code=202)
async def resume_run(
    run_id: UUID,
    request: Request,
    run_repo=Depends(_get_run_repo),
) -> dict:
    """Resume a FAILED pipeline run by creating a new job.

    Pre-conditions:
    - Run must be FAILED (409 otherwise).
    - Run must have a non-empty job_id (409 for legacy runs).
    - The original job must exist in the job repository (409 otherwise).

    The new job copies name and parameters from the original job and adds
    ``resume_from_run_id=<run_id>`` in its parameters.

    ``resume_from_run_id`` is a reserved parameter key — it cannot be set
    via POST /jobs directly (returns 422).

    Contract: resume assumes steps are idempotent. Non-capability steps
    (external, …) COMPLETED in the source run are replicated in the new run
    with ``skipped_resume=True`` in their artifacts and are not re-executed
    (their side effects persist outside the process). Capability steps are
    always re-executed — their in-memory GeoDataFrame output does not
    survive the source run, and skipping them would corrupt downstream data.

    Status codes:
        202  -- new job created and enqueued.
        404  -- run not found.
        409  -- run is not FAILED.
        409  -- run has no job_id (legacy).
        409  -- original job not found in job repository.
    """
    run = run_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    if run.status != JobStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "run_not_failed",
                "status": run.status.value,
                "message": (
                    f"Run '{run_id}' has status '{run.status.value}'. "
                    "Only FAILED runs can be resumed."
                ),
            },
        )

    job_id = getattr(run, "job_id", "")
    if not job_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "run_not_cancellable_no_job",
                "message": (
                    f"Run '{run_id}' has no job_id and cannot be resumed via this "
                    "endpoint. This applies to scenario/in-process runs and to runs "
                    "created before the job-backed orchestration was deployed."
                ),
            },
        )

    job_repo = _get_job_repo(request)
    if job_repo is None:
        raise HTTPException(status_code=503, detail="Job repository not available.")

    try:
        origin_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "run_invalid_job_id",
                "job_id": job_id,
                "message": f"Run '{run_id}' has malformed job_id '{job_id}'.",
            },
        )

    origin_job = job_repo.get(origin_uuid)
    if origin_job is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "origin_job_not_found",
                "job_id": job_id,
                "message": (
                    f"Original job '{job_id}' for run '{run_id}' was not found "
                    "in the job repository. Cannot resume without the original job parameters."
                ),
            },
        )

    # Build new job parameters: copy origin + inject resume_from_run_id.
    # Exclude any prior resume_from_run_id to avoid chaining confusion.
    from gispulse.core.models import Job

    new_params = {
        k: v
        for k, v in origin_job.parameters.items()
        if k != "resume_from_run_id"
    }
    new_params["resume_from_run_id"] = str(run_id)

    new_job = Job(
        name=origin_job.name,
        dataset_id=origin_job.dataset_id,
        parameters=new_params,
    )
    job_repo.save(new_job)

    job_queue = _get_job_queue(request)
    if job_queue is not None:
        await job_queue.enqueue(new_job)

    log.info(
        "run_resume_created",
        source_run_id=str(run_id),
        origin_job_id=job_id,
        new_job_id=str(new_job.id),
    )

    return {
        "job_id": str(new_job.id),
        "status": "pending",
        "resume_from_run_id": str(run_id),
    }
