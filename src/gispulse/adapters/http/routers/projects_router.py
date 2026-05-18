"""REST router for Phase 3 persistent projects.

Endpoints:
    POST   /projects                    — create a project
    GET    /projects                    — list projects
    GET    /projects/{id}               — get project details
    DELETE /projects/{id}               — delete a project
    GET    /projects/{id}/layers        — list layers in project schema
    GET    /projects/{id}/stats         — aggregated stats for the project
    GET    /projects/{id}/activity      — recent activity timeline for the project
"""

from __future__ import annotations

import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from gispulse.adapters.http.dependencies import get_event_hub, get_project_repo, get_spatial_engine
from gispulse.adapters.http.event_hub import EventHub
from gispulse.adapters.http.rate_limit import limiter
from gispulse.core.models import Project
from gispulse.persistence.engine import SpatialEngine
from gispulse.persistence.repository import Repository
from gispulse.persistence.tier import get_current_tier

router = APIRouter(prefix="/projects", tags=["projects"])


# Mirrors `core/pricing_catalog.yml` (`tiers.*.limits.projects`). `None` = unlimited.
# Keep in sync if the catalog changes.
_PROJECT_LIMITS: dict[str, int | None] = {
    "community": 1,
    "pro": 5,
    "team": None,
    "enterprise": None,
}


def _enforce_project_limit(repo: Repository) -> None:
    tier = get_current_tier()
    limit = _PROJECT_LIMITS.get(tier, _PROJECT_LIMITS["community"])
    if limit is None:
        return
    count = len(repo.list_all())
    if count >= limit:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Project limit reached for tier '{tier}' ({count}/{limit}). "
                "Upgrade to Team for unlimited projects."
            ),
        )


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    schema_name: str = "public"
    engine_backend: str = "duckdb"
    dsn: str | None = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    schema_name: str
    engine_backend: str
    datasets: list[str]
    rules: list[str]
    triggers: list[str]
    created_at: str


def _to_response(p: Project) -> ProjectResponse:
    return ProjectResponse(
        id=str(p.id),
        name=p.name,
        description=p.description,
        schema_name=p.schema_name,
        engine_backend=p.engine_backend,
        datasets=[str(d) for d in p.datasets],
        rules=[str(r) for r in p.rules],
        triggers=[str(t) for t in p.triggers],
        created_at=p.created_at.isoformat(),
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("", response_model=ProjectResponse, status_code=201)
@limiter.limit("20/minute")
def create_project(
    request: Request,
    body: ProjectCreate,
    repo: Repository = Depends(get_project_repo),
) -> ProjectResponse:
    _enforce_project_limit(repo)
    project = Project(
        name=body.name,
        description=body.description,
        schema_name=body.schema_name,
        engine_backend=body.engine_backend,
        dsn=body.dsn,
    )
    repo.save(project)
    return _to_response(project)


class PaginatedProjects(BaseModel):
    items: list[ProjectResponse]
    total: int
    limit: int
    offset: int


@router.get("", response_model=PaginatedProjects)
def list_projects(
    limit: int = 50,
    offset: int = 0,
    repo: Repository = Depends(get_project_repo),
) -> PaginatedProjects:
    all_projects = [_to_response(p) for p in repo.list_all()]
    total = len(all_projects)
    return PaginatedProjects(
        items=all_projects[offset: offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: UUID,
    repo: Repository = Depends(get_project_repo),
) -> ProjectResponse:
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _to_response(project)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: UUID,
    repo: Repository = Depends(get_project_repo),
) -> None:
    if not repo.delete(project_id):
        raise HTTPException(status_code=404, detail="Project not found")


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    body: ProjectCreate,
    repo: Repository = Depends(get_project_repo),
) -> ProjectResponse:
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project.name = body.name
    project.description = body.description
    repo.save(project)
    return _to_response(project)


@router.post("/{project_id}/datasets/{dataset_id}", status_code=204)
def add_dataset_to_project(
    project_id: UUID,
    dataset_id: UUID,
    repo: Repository = Depends(get_project_repo),
) -> None:
    """Associate an existing dataset with a project."""
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if dataset_id not in project.datasets:
        project.datasets.append(dataset_id)
        repo.save(project)


@router.delete("/{project_id}/datasets/{dataset_id}", status_code=204)
def remove_dataset_from_project(
    project_id: UUID,
    dataset_id: UUID,
    repo: Repository = Depends(get_project_repo),
) -> None:
    """Remove a dataset association from a project."""
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if dataset_id in project.datasets:
        project.datasets.remove(dataset_id)
        repo.save(project)


@router.get("/{project_id}/layers", response_model=list[str])
def list_project_layers(
    project_id: UUID,
    repo: Repository = Depends(get_project_repo),
    engine: SpatialEngine = Depends(get_spatial_engine),
) -> list[str]:
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return engine.list_layers(schema=project.schema_name)


@router.post("/{project_id}/detect-relations")
async def detect_relations(
    project_id: UUID,
    request: Request,
    repo: Repository = Depends(get_project_repo),
    hub: EventHub = Depends(get_event_hub),
) -> dict:
    """Detect spatial and attribute relations between project layers.

    Loads the GeoDataFrames for each dataset associated with the project
    and runs the ``SpatialRelationDetector`` to find attribute and spatial
    relations between all layer pairs.
    """
    from dataclasses import asdict
    from gispulse.capabilities.relation_detector import SpatialRelationDetector

    project = repo.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Collect layers from the portal layer cache (populated by dataset upload)
    layer_cache: dict = getattr(request.app.state, "layer_cache", {})
    layers: dict = {}
    for ds_id in project.datasets:
        ds_key = str(ds_id)
        cached = layer_cache.get(ds_key)
        if isinstance(cached, dict):
            layers.update(cached)

    relations: list[dict] = []
    if len(layers) >= 2:
        detector = SpatialRelationDetector()
        detected = detector.analyze_all(layers)
        relations = [
            {k: v for k, v in asdict(r).items() if k != "suggested_rule"}
            for r in detected
        ]

    # Broadcast WebSocket event for live relation detection updates
    hub.broadcast("relation.detected", {
        "project_id": str(project_id),
        "count": len(relations),
    })

    return {
        "project_id": str(project_id),
        "relations": relations,
    }


@router.get("/{project_id}/relations")
def get_relations(
    project_id: UUID,
    repo: Repository = Depends(get_project_repo),
) -> dict:
    """Return confirmed (persisted) relations for a project.

    Relations are stored in project.metadata['relations'] and populated
    via POST /{project_id}/relations (issue #156, Sprint R-5).
    """
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    relations: list[dict] = []
    if project.metadata and "relations" in project.metadata:
        relations = project.metadata["relations"]

    return {"project_id": str(project_id), "relations": relations}


# ------------------------------------------------------------------
# Relations persistence — Issue #156 (Sprint R-5)
# POST /projects/{id}/relations
# ------------------------------------------------------------------


class RelationItem(BaseModel):
    """A single spatial or attribute relation between two layers."""

    layer_a: str
    layer_b: str
    relation_type: str  # "spatial" | "attribute"
    confidence: float = 1.0
    sample_stats: dict = {}
    suggested_name: str | None = None


class RelationsPersistRequest(BaseModel):
    """Request body to persist one or more confirmed relations."""

    relations: list[RelationItem]
    replace: bool = False  # If True, replaces existing relations entirely


@router.post("/{project_id}/relations", status_code=201)
@limiter.limit("30/minute")
def persist_relations(
    project_id: UUID,
    body: RelationsPersistRequest,
    request: Request,
    repo: Repository = Depends(get_project_repo),
    hub: EventHub = Depends(get_event_hub),
) -> dict:
    """Persist confirmed relations for a project.

    Relations are stored in project.metadata['relations'].
    By default merges with existing relations (deduplicating by layer_a+layer_b+relation_type).
    Set ``replace=true`` to overwrite all existing relations.
    """
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    existing: list[dict] = []
    if not body.replace and project.metadata and "relations" in project.metadata:
        existing = list(project.metadata["relations"])

    incoming = [r.model_dump() for r in body.relations]

    if body.replace:
        merged = incoming
    else:
        # Merge: deduplicate by (layer_a, layer_b, relation_type)
        existing_keys = {
            (r["layer_a"], r["layer_b"], r["relation_type"]) for r in existing
        }
        for rel in incoming:
            key = (rel["layer_a"], rel["layer_b"], rel["relation_type"])
            if key not in existing_keys:
                existing.append(rel)
                existing_keys.add(key)
        merged = existing

    # Persist in project metadata
    meta = dict(project.metadata) if project.metadata else {}
    meta["relations"] = merged

    # Update project in repository
    updated = project.model_copy(update={"metadata": meta})
    try:
        repo.update(updated)
    except AttributeError:
        # Fallback for repositories that don't implement update (e.g. InMemory)
        repo._store[project_id] = updated  # type: ignore[attr-defined]

    # Broadcast WebSocket event
    hub.broadcast("relations.updated", {
        "project_id": str(project_id),
        "count": len(merged),
    })

    return {
        "project_id": str(project_id),
        "relations": merged,
        "stored": len(merged),
    }


# ------------------------------------------------------------------
# Schemas for stats & activity (Sprint R-3, issues #150 & #151)
# ------------------------------------------------------------------


class ProjectStats(BaseModel):
    project_id: str
    dataset_count: int
    layer_count: int
    rule_count: int
    trigger_count: int
    scenario_count: int
    total_feature_count: int
    last_activity: str | None


class ActivityEventItem(BaseModel):
    id: str
    event_type: str          # "dataset_import" | "rule_applied" | "trigger_fired" | "job_completed" | "project_created"
    title: str
    description: str | None
    status: str              # "success" | "error" | "running" | "info"
    timestamp: str
    metadata: dict           # extra context (dataset id, rule id, duration_ms, …)


class ActivityResponse(BaseModel):
    project_id: str
    items: list[ActivityEventItem]
    total: int


# ------------------------------------------------------------------
# Stats endpoint — GET /projects/{id}/stats  (issue #150)
# ------------------------------------------------------------------


@router.get("/{project_id}/stats", response_model=ProjectStats)
def get_project_stats(
    project_id: UUID,
    repo: Repository = Depends(get_project_repo),
    request: Request = None,  # type: ignore[assignment]
) -> ProjectStats:
    """Return aggregated statistics for a project.

    Aggregates dataset count, layer count, rule count, trigger count and
    scenario count.  Feature counts are summed from dataset metadata when
    available.
    """
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Pull additional counts from the other repos if available on app state
    rule_count = len(project.rules)
    trigger_count = len(project.triggers)
    scenario_count = 0
    total_feature_count = 0
    last_activity: str | None = project.created_at.isoformat() if hasattr(project.created_at, "isoformat") else str(project.created_at)

    if request is not None:
        # Optionally enrich from scenario repo
        try:
            scenario_repo = request.app.state.scenario_repo
            scenario_count = sum(
                1 for s in scenario_repo.list_all()
                if hasattr(s, "project_id") and str(s.project_id) == str(project_id)
            )
        except AttributeError:
            scenario_count = 0

        # Enrich feature count from dataset repo
        try:
            dataset_repo = request.app.state.dataset_repo
            layer_count = 0
            for ds_id in project.datasets:
                try:
                    ds = dataset_repo.get(ds_id) if not isinstance(ds_id, str) else dataset_repo.get(UUID(str(ds_id)))
                    if ds is not None and hasattr(ds, "metadata") and ds.metadata:
                        layers = ds.metadata.get("layers", [])
                        layer_count += len(layers)
                        for layer in layers:
                            total_feature_count += layer.get("feature_count", 0)
                except Exception:
                    pass
        except AttributeError:
            layer_count = len(project.datasets)  # fallback
    else:
        layer_count = len(project.datasets)

    return ProjectStats(
        project_id=str(project_id),
        dataset_count=len(project.datasets),
        layer_count=layer_count,
        rule_count=rule_count,
        trigger_count=trigger_count,
        scenario_count=scenario_count,
        total_feature_count=total_feature_count,
        last_activity=last_activity,
    )


# ------------------------------------------------------------------
# Activity timeline — GET /projects/{id}/activity  (issue #151)
# ------------------------------------------------------------------


@router.get("/{project_id}/activity", response_model=ActivityResponse)
def get_project_activity(
    project_id: UUID,
    limit: int = 20,
    offset: int = 0,
    repo: Repository = Depends(get_project_repo),
    request: Request = None,  # type: ignore[assignment]
) -> ActivityResponse:
    """Return recent activity events for a project.

    Events are assembled from job history, dataset imports, rule changes,
    and trigger firings stored in their respective repositories.
    The list is sorted newest-first and supports pagination via limit/offset.
    """
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    events: list[ActivityEventItem] = []
    now = datetime.datetime.now(tz=datetime.timezone.utc)

    # -- Project creation event ------------------------------------------
    created_ts = project.created_at.isoformat() if hasattr(project.created_at, "isoformat") else str(project.created_at)
    events.append(ActivityEventItem(
        id=f"project_created_{project_id}",
        event_type="project_created",
        title="Project created",
        description=project.name,
        status="info",
        timestamp=created_ts,
        metadata={"project_id": str(project_id)},
    ))

    # -- Dataset import events -------------------------------------------
    if request is not None:
        try:
            dataset_repo = request.app.state.dataset_repo
            for ds_id in project.datasets:
                try:
                    ds_uuid = ds_id if not isinstance(ds_id, str) else UUID(str(ds_id))
                    ds = dataset_repo.get(ds_uuid)
                    if ds is not None:
                        ds_ts = ds.created_at.isoformat() if hasattr(ds.created_at, "isoformat") else str(ds.created_at)
                        layer_count = len(ds.metadata.get("layers", [])) if ds.metadata else 0
                        events.append(ActivityEventItem(
                            id=f"dataset_import_{ds.id}",
                            event_type="dataset_import",
                            title="Dataset imported",
                            description=ds.name,
                            status="success",
                            timestamp=ds_ts,
                            metadata={
                                "dataset_id": str(ds.id),
                                "dataset_name": ds.name,
                                "format": ds.format or "unknown",
                                "layer_count": layer_count,
                            },
                        ))
                except Exception:
                    pass
        except AttributeError:
            pass

        # -- Job events --------------------------------------------------
        try:
            job_repo = request.app.state.job_repo
            for job in job_repo.list_all():
                try:
                    # Jobs linked to datasets in this project
                    job_ds_id = job.dataset_id if hasattr(job, "dataset_id") else None
                    if job_ds_id is None or str(job_ds_id) not in [str(d) for d in project.datasets]:
                        continue
                    job_ts = job.created_at.isoformat() if hasattr(job.created_at, "isoformat") else str(now)
                    status_map = {"completed": "success", "failed": "error", "running": "running"}
                    job_status = status_map.get(getattr(job, "status", ""), "info")
                    duration_ms = None
                    if hasattr(job, "duration_seconds") and job.duration_seconds is not None:
                        duration_ms = int(job.duration_seconds * 1000)
                    events.append(ActivityEventItem(
                        id=f"job_{job.id}",
                        event_type="job_completed",
                        title=job.name if hasattr(job, "name") else "Job",
                        description=getattr(job, "error_message", None),
                        status=job_status,
                        timestamp=job_ts,
                        metadata={
                            "job_id": str(job.id),
                            "duration_ms": duration_ms,
                        },
                    ))
                except Exception:
                    pass
        except AttributeError:
            pass

    # -- Sort by timestamp descending, paginate -------------------------
    events.sort(key=lambda e: e.timestamp, reverse=True)
    total = len(events)
    paginated = events[offset: offset + limit]

    return ActivityResponse(
        project_id=str(project_id),
        items=paginated,
        total=total,
    )
