"""
Manifests router for the GISPulse HTTP API — vague 3 orchestration.

Endpoints:
    POST /manifests/run      -- submit a v3 manifest for async execution (202)
    POST /manifests/validate -- validate a v3 manifest without executing (200)

Architecture:
    Execution goes through the same job/worker pipeline as POST /jobs.
    The manifest dict is stored in job.parameters["manifest"] so the
    existing worker + JobRunner._execute_manifest picks it up.
    PipelineRun.spec_ref = manifest name, .scope = optional body param.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gispulse.adapters.http.dependencies import (
    get_dataset_repo,
    get_job_queue,
    get_job_repo,
    get_results_dir,
)
from gispulse.adapters.http.rate_limit import limiter
from gispulse.core.logging import get_logger
from gispulse.core.models import Job
from gispulse.orchestration.job_queue import JobQueue
from gispulse.persistence.repository import Repository

log = get_logger(__name__)

router = APIRouter(prefix="/manifests", tags=["manifests"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ManifestRunRequest(BaseModel):
    """Request body for POST /manifests/run."""

    manifest: dict[str, Any] = Field(
        ...,
        description="A v3 manifest dict ({version: 3, sources: {...}, models: {...}}).",
    )
    dataset_id: UUID | None = Field(
        None,
        description="Optional dataset UUID to load as the primary source for this run.",
    )
    scope: str | None = Field(
        None,
        description="Optional scope tag (e.g. department code '63'). "
        "Stored on PipelineRun.scope and used by monitoring platforms to filter runs.",
    )
    steps: list[str] | None = Field(
        None,
        description=(
            "Optional list of step IDs (from the compiled flat PipelineSpec) to "
            "execute. When omitted or empty, all steps run. When provided, only the "
            "listed steps execute. Unknown IDs or capability orphans return 422 "
            "immediately without creating a job. Stored as job.parameters['steps_filter']."
        ),
    )


class ManifestRunResponse(BaseModel):
    """Response for POST /manifests/run — 202 Accepted."""

    job_id: UUID = Field(..., description="UUID of the enqueued job.")
    status: str = Field("pending", description="Initial job status.")
    manifest_name: str = Field("", description="Name field from the submitted manifest.")


class ManifestValidateRequest(BaseModel):
    """Request body for POST /manifests/validate."""

    manifest: dict[str, Any] = Field(
        ...,
        description="A v3 manifest dict to validate without executing.",
    )


class ManifestValidateResponse(BaseModel):
    """Validation result for POST /manifests/validate."""

    valid: bool = Field(..., description="True when the manifest passes all checks.")
    errors: list[str] = Field(
        default_factory=list,
        description="Validation errors (empty when valid=True).",
    )
    compiled_steps_count: int = Field(
        0,
        description="Number of steps the compiled PipelineSpec would contain "
        "(only meaningful when valid=True).",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_manifest_dict(raw: dict[str, Any]) -> tuple[bool, list[str], int]:
    """Full validation pipeline for a raw manifest dict.

    Returns (valid, errors, compiled_steps_count).
    Errors are human-readable strings, not exceptions — caller decides
    whether to 422 (router) or propagate (runner).
    """
    from gispulse.core.pipeline_schema import validate_pipeline_json
    from gispulse.core.manifest_v3 import (
        ManifestV3,
        _parse_sources,
        _parse_staging,
        _parse_models,
        _parse_v3_triggers,
        validate_manifest,
        compile_to_pipeline,
        ManifestValidationError,
    )

    # 1. Type + version guard
    if not isinstance(raw, dict):
        return False, [f"manifest must be a dict, got {type(raw).__name__}"], 0
    received_version = raw.get("version")
    if received_version != 3:
        return False, [
            f"manifest must be version 3, got version={received_version!r}. "
            'Use a v3 manifest ({"version": 3, "sources": {...}, "models": {...}}).'
        ], 0

    # 2. JSON Schema (SCHEMA_V3)
    schema_errors = validate_pipeline_json(raw)
    if schema_errors:
        return False, schema_errors, 0

    # 3. Parse + graph checks (refs, cycles)
    try:
        manifest = ManifestV3(
            name=raw.get("name", ""),
            description=raw.get("description", ""),
            sources=_parse_sources(raw.get("sources", {})),
            staging=_parse_staging(raw.get("staging")),
            models=_parse_models(raw.get("models", {})),
            triggers=_parse_v3_triggers(raw.get("triggers", [])),
            security=dict(raw.get("security") or {}),
            runtime=dict(raw.get("runtime") or {}),
        )
    except Exception as exc:
        return False, [f"manifest parse error: {exc}"], 0

    try:
        validate_manifest(manifest)
    except ManifestValidationError as exc:
        return False, exc.errors, 0

    # 4. Compile + capability existence check
    # Mirrors the check in POST /pipelines/validate (pipelines_router.py:187-207)
    # so unknown capabilities are caught at the manifest layer too.
    try:
        compiled = compile_to_pipeline(manifest)
        steps_count = len(compiled.steps)
    except Exception as exc:
        return False, [f"compile error: {exc}"], 0

    try:
        from gispulse.capabilities import list_all as _list_caps
        known_caps = {c["name"] for c in _list_caps()}
        cap_errors = [
            f"unknown capability '{step.capability}' in step '{step.id}'. "
            f"Available: {sorted(known_caps)}."
            for step in compiled.steps
            # Skip non-capability steps: their kind is dispatched to the step-kind
            # registry at execution time in the worker process. The HTTP boundary
            # cannot know which application kinds are registered there. Validating
            # step.kind existence here would produce false-positive 422s for any
            # custom kind (e.g. "external") that is perfectly valid in the worker.
            if step.kind == "capability"
            and step.type == "capability" and step.capability
            and step.capability not in known_caps
        ]
        if cap_errors:
            return False, cap_errors, steps_count
    except ImportError:
        # capabilities module unavailable (stripped build); skip the check.
        pass

    return True, [], steps_count


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/run", response_model=ManifestRunResponse, status_code=202)
@limiter.limit("10/minute")
async def run_manifest_job(
    request: Request,
    payload: ManifestRunRequest,
    background_tasks: BackgroundTasks,
    job_repo: Repository = Depends(get_job_repo),
    dataset_repo: Repository = Depends(get_dataset_repo),
    job_queue: JobQueue = Depends(get_job_queue),
    results_dir: Path = Depends(get_results_dir),
) -> ManifestRunResponse:
    """Submit a v3 manifest for asynchronous execution.

    Returns immediately with HTTP 202 and the job in ``pending`` status.
    Use ``GET /jobs/{job_id}`` to poll for completion, or
    ``GET /jobs/{job_id}/events`` for SSE streaming.
    Use ``GET /runs`` to find the associated PipelineRun (spec_ref = manifest name).

    The manifest is validated upfront before enqueueing — an invalid manifest
    (bad version, unresolved refs, cycle) returns 422 immediately without
    creating a job.
    """
    # --- Upfront validation (422 before any enqueue) ----------------------
    valid, errors, _ = _validate_manifest_dict(payload.manifest)
    if not valid:
        raise HTTPException(
            status_code=422,
            detail={"valid": False, "errors": errors},
        )

    # --- steps_filter validation (422 before enqueue) ---------------------
    # Two validation passes:
    # 1. _apply_steps_filter on the flat compiled spec → unknown IDs and intra-model
    #    capability orphans (capability step whose step.input is excluded).
    # 2. validate_steps_filter_models → inter-model orphans via select AND with:
    #    (ref_layer) references to excluded models that have capability steps.
    steps_filter: list[str] = payload.steps or []
    if steps_filter:
        try:
            from gispulse.core.manifest_v3 import compile_to_pipeline, ManifestV3, _parse_sources, _parse_staging, _parse_models, _parse_v3_triggers
            from gispulse.orchestration.runner import _apply_steps_filter
            from gispulse.runtime.manifest_runner import validate_steps_filter_models
            raw = payload.manifest
            manifest_obj = ManifestV3(
                name=raw.get("name", ""),
                description=raw.get("description", ""),
                sources=_parse_sources(raw.get("sources", {})),
                staging=_parse_staging(raw.get("staging")),
                models=_parse_models(raw.get("models", {})),
                triggers=_parse_v3_triggers(raw.get("triggers", [])),
                security=dict(raw.get("security") or {}),
                runtime=dict(raw.get("runtime") or {}),
            )
            flat_spec = compile_to_pipeline(manifest_obj)
            _apply_steps_filter(flat_spec, steps_filter, job_id="<manifest_run_validation>")
            # Pass 2: inter-model with: (ref_layer) capability orphan check
            validate_steps_filter_models(manifest_obj, steps_filter)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"valid": False, "errors": [str(exc)]},
            )

    manifest_name = payload.manifest.get("name", "") or "manifest"

    # --- Build job parameters ---------------------------------------------
    parameters: dict[str, Any] = {"manifest": payload.manifest}
    if payload.scope:
        parameters["scope"] = payload.scope
    if steps_filter:
        parameters["steps_filter"] = steps_filter

    job = Job(
        name=manifest_name,
        dataset_id=payload.dataset_id,
        parameters=parameters,
    )
    job_repo.save(job)

    # --- Enqueue ----------------------------------------------------------
    if job_queue is not None:
        await job_queue.enqueue(job)
    else:
        # Fallback: BackgroundTasks (no worker running)
        from gispulse.adapters.http.routers.jobs_router import _run_job_background

        runner = request.app.state.job_runner
        background_tasks.add_task(
            _run_job_background,
            job,
            job_repo,
            dataset_repo,
            runner,
            results_dir,
        )

    log.info(
        "manifest_job_enqueued",
        job_id=str(job.id),
        manifest_name=manifest_name,
        scope=payload.scope or "",
    )

    return ManifestRunResponse(
        job_id=job.id,
        status="pending",
        manifest_name=manifest_name,
    )


@router.post("/validate", response_model=ManifestValidateResponse)
def validate_manifest_endpoint(
    payload: ManifestValidateRequest,
) -> ManifestValidateResponse:
    """Validate a v3 manifest without executing.

    Checks version, JSON Schema (SCHEMA_V3), unresolved source/model
    references, and inter-model cycles. Returns the number of compiled
    steps when the manifest is valid.
    """
    valid, errors, compiled_steps_count = _validate_manifest_dict(payload.manifest)
    return ManifestValidateResponse(
        valid=valid,
        errors=errors,
        compiled_steps_count=compiled_steps_count,
    )
