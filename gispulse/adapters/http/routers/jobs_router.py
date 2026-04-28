"""
Jobs router for the GISPulse HTTP API.

Endpoints:
    POST  /jobs              -- submit a job (enqueues via JobQueue, returns 202)
    GET   /jobs              -- list all jobs
    GET   /jobs/{id}         -- detail with current status (polling)
    GET   /jobs/{id}/events  -- SSE stream of status updates
    GET   /jobs/{id}/download -- download the result file
    POST  /jobs/{id}/cancel  -- cancel a running/pending job

Recovery:
    recover_stale_jobs()    -- called at startup to re-enqueue PENDING/RUNNING jobs
                               (max 3 attempts before marking FAILED)
"""

from __future__ import annotations

_MAX_JOB_ATTEMPTS = 3

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.responses import StreamingResponse

from gispulse.adapters.http.rate_limit import limiter
from gispulse.adapters.http.dependencies import (
    get_dataset_repo,
    get_job_queue,
    get_job_repo,
    get_job_runner,
    get_results_dir,
)
from gispulse.adapters.http.schemas import JobCreate, JobResponse
from core.logging import get_logger
from core.models import Job, JobStatus
from orchestration.job_queue import JobQueue
from orchestration.runner import JobRunner
from persistence.repository import Repository

log = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job_to_response(job: Job) -> JobResponse:
    duration = None
    if job.started_at and job.completed_at:
        duration = (job.completed_at - job.started_at).total_seconds()

    return JobResponse(
        id=job.id,
        name=job.name,
        status=job.status.value,
        dataset_id=job.dataset_id,
        parameters=job.parameters,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result_path=job.result_path,
        error_message=job.error_message,
        duration_seconds=duration,
        attempts=getattr(job, "attempts", 0),
    )


def recover_stale_jobs(
    job_repo: "Repository",
    dataset_repo: "Repository",
    runner: "JobRunner",
    results_dir: Path,
    background_tasks: "BackgroundTasks | None" = None,
) -> int:
    """Re-enqueue PENDING/RUNNING jobs after a process restart.

    Each call increments ``attempts``.  Jobs that exceed ``_MAX_JOB_ATTEMPTS``
    are marked ``FAILED`` immediately instead of being retried.

    Returns the number of jobs re-enqueued (not counting those failed out).
    """
    stale = [
        j for j in job_repo.list_all()
        if j.status in (JobStatus.PENDING, JobStatus.RUNNING)
    ]
    requeued = 0
    for job in stale:
        job.attempts = getattr(job, "attempts", 0) + 1
        if job.attempts > _MAX_JOB_ATTEMPTS:
            job.status = JobStatus.FAILED
            job.error_message = "max retries exceeded"
            job.completed_at = datetime.now(timezone.utc)
            job_repo.save(job)
            log.warning("job_max_retries_exceeded", job_id=str(job.id))
        else:
            job.status = JobStatus.PENDING
            job_repo.save(job)
            if background_tasks is not None:
                background_tasks.add_task(
                    _run_job_background, job, job_repo, dataset_repo, runner, results_dir
                )
            requeued += 1
            log.info("job_requeued_after_restart", job_id=str(job.id), attempt=job.attempts)
    return requeued


def _run_job_background(
    job: Job,
    job_repo: Repository,
    dataset_repo: Repository,
    runner: JobRunner,
    results_dir: Path,
) -> None:
    """Background task that loads the dataset, runs the job, and persists results.

    This function is executed by FastAPI's BackgroundTasks after the HTTP 202
    response has been sent to the client.  All state mutations go through
    ``job_repo.save()`` so the polling endpoint ``GET /jobs/{id}`` reflects
    progress in real time.
    """
    import geopandas as gpd

    from persistence.io import read_vector, write_vector

    # -- Guard: if the job was cancelled before background execution starts --
    refreshed = job_repo.get(job.id)
    if refreshed and refreshed.status == JobStatus.FAILED:
        log.info("job_skip_cancelled", job_id=str(job.id))
        return

    # -- Load dataset -------------------------------------------------------
    gdf = gpd.GeoDataFrame()
    if job.dataset_id:
        dataset = dataset_repo.get(job.dataset_id)
        if dataset is None:
            job.status = JobStatus.FAILED
            job.error_message = f"Dataset '{job.dataset_id}' not found."
            job.completed_at = datetime.now(timezone.utc)
            job_repo.save(job)
            return
        try:
            layer_name = job.parameters.get("layer")
            gdf = read_vector(dataset.source_path, layer=layer_name)
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error_message = f"Failed to load dataset: {exc}"
            job.completed_at = datetime.now(timezone.utc)
            job_repo.save(job)
            return

    # -- Build layer_resolver for cross-layer rules -------------------------
    def _layer_resolver(name: str) -> gpd.GeoDataFrame:
        """Resolve sibling layers from the same dataset file."""
        if job.dataset_id:
            ds = dataset_repo.get(job.dataset_id)
            if ds and ds.source_path:
                return read_vector(ds.source_path, layer=name)
        raise ValueError(f"Cannot resolve layer '{name}'")

    # -- Execute via JobRunner ----------------------------------------------
    try:
        updated_job, result_gdf = runner.run(job, gdf, layer_resolver=_layer_resolver)

        # Write result if we have data
        if not result_gdf.empty:
            result_path = results_dir / f"{job.id}_result.gpkg"
            write_vector(result_gdf, str(result_path))
            job.result_path = str(result_path)

    except Exception as exc:
        job.status = JobStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = datetime.now(timezone.utc)

    job_repo.save(job)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=JobResponse, status_code=202)
@limiter.limit("10/minute")
async def create_job(
    request: Request,
    payload: JobCreate,
    background_tasks: BackgroundTasks,
    job_repo: Repository = Depends(get_job_repo),
    dataset_repo: Repository = Depends(get_dataset_repo),
    runner: JobRunner = Depends(get_job_runner),
    results_dir: Path = Depends(get_results_dir),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobResponse:
    """Submit a Job for asynchronous execution.

    Returns immediately with HTTP 202 and the job in ``pending`` status.
    Use ``GET /jobs/{id}`` to poll for completion, or
    ``GET /jobs/{id}/events`` for SSE streaming.

    When a JobQueue is configured, the job is enqueued and processed by
    the worker.  Otherwise, falls back to FastAPI BackgroundTasks.
    """
    job = Job(
        name=payload.name,
        dataset_id=payload.dataset_id,
        parameters=payload.parameters,
    )
    job_repo.save(job)

    if job_queue is not None:
        await job_queue.enqueue(job)
    else:
        background_tasks.add_task(
            _run_job_background,
            job,
            job_repo,
            dataset_repo,
            runner,
            results_dir,
        )

    return _job_to_response(job)


@router.get("")
def list_jobs(
    limit: int = 50,
    offset: int = 0,
    job_repo: Repository = Depends(get_job_repo),
) -> dict:
    """Return paginated jobs."""
    all_items = job_repo.list_all()
    total = len(all_items)
    page = all_items[offset : offset + limit]
    return {
        "items": [_job_to_response(j) for j in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: UUID,
    job_repo: Repository = Depends(get_job_repo),
) -> JobResponse:
    """Return a single job by UUID (polling endpoint)."""
    job = job_repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return _job_to_response(job)  # type: ignore[arg-type]


@router.get("/{job_id}/events")
async def job_events_sse(
    job_id: UUID,
    job_queue: JobQueue = Depends(get_job_queue),
) -> StreamingResponse:
    """Stream job status updates as Server-Sent Events (SSE).

    The client connects and receives events as the job progresses through
    PENDING -> RUNNING -> COMPLETED/FAILED.  The stream closes automatically
    once a terminal status is reached.
    """
    job_id_str = str(job_id)

    async def event_generator():
        cursor = 0
        while True:
            events = await job_queue.get_events(job_id_str, since=cursor)
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"
                cursor += 1
                if event.get("status") in (
                    JobStatus.COMPLETED.value,
                    JobStatus.FAILED.value,
                ):
                    return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: UUID,
    job_repo: Repository = Depends(get_job_repo),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobResponse:
    """Cancel a pending or running job.

    Sets the job status to FAILED with ``error_message: "Cancelled by user"``.
    This is a soft cancel -- no thread interruption is performed.
    """
    job = job_repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' is already {job.status.value}. Cannot cancel.",
        )

    if job_queue is not None:
        await job_queue.cancel(str(job_id))

    job.status = JobStatus.FAILED
    job.error_message = "Cancelled by user"
    job.completed_at = datetime.now(timezone.utc)
    job_repo.save(job)

    return _job_to_response(job)  # type: ignore[arg-type]




@router.get("/{job_id}/features")
def get_job_features(
    job_id: UUID,
    limit: int = 10000,
    simplify: float | None = None,
    job_repo: Repository = Depends(get_job_repo),
) -> dict:
    """Return job result as GeoJSON FeatureCollection.

    Used by the playground frontend to display pipeline results on the map.
    """
    from persistence.io import read_vector

    job = job_repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail=f"Job status: {job.status.value}")
    if not job.result_path or not Path(job.result_path).exists():
        raise HTTPException(status_code=404, detail="No result file.")

    gdf = read_vector(job.result_path)
    if simplify and not gdf.empty and hasattr(gdf, "geometry"):
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.simplify(simplify)
    gdf = gdf.head(limit)
    fc = json.loads(gdf.to_json())
    fc["total_count"] = len(gdf)
    return fc

@router.get("/{job_id}/download")
def download_result(
    job_id: UUID,
    job_repo: Repository = Depends(get_job_repo),
) -> FileResponse:
    """Download the result file of a completed job.

    Returns the GPKG result file as an attachment.
    """
    job = job_repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if not job.result_path:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' has no result file (status: {job.status.value}).",
        )

    result_path = Path(job.result_path)
    if not result_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Result file no longer exists on disk.",
        )

    return FileResponse(
        path=str(result_path),
        filename=result_path.name,
        media_type="application/geopackage+sqlite3",
    )
