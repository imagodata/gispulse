"""Runs router — read-only access to pipeline run history.

Endpoints:
    GET /runs          -- paginated list of PipelineRun objects
    GET /runs/{run_id} -- single run by UUID
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from gispulse.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


def _get_run_repo(request: Request):
    """Return the RunRepository from app state."""
    repo = getattr(request.app.state, "run_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Run repository not available")
    return repo


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
