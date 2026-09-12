"""REST router for saved web maps (#405).

A *saved map* is a reusable composition of layers + per-layer styles +
viewport + filters that the web editor can store and reload. It is the
persistence socle of the web-mapping editor EPIC (#409); publication as a
public permalink (#406), MVT tiles (#407) and view stats (#408) build on
top of this entity.

Endpoints::

    POST   /maps              — create a saved map
    GET    /maps              — list saved maps (paginated)
    GET    /maps/{id}         — get one saved map
    PUT    /maps/{id}         — replace a saved map's composition
    DELETE /maps/{id}         — delete a saved map
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gispulse.adapters.http.dependencies import get_saved_map_repo
from gispulse.adapters.http.rate_limit import limiter
from gispulse.core.models import SavedMap
from gispulse.persistence.repository import Repository

router = APIRouter(prefix="/maps", tags=["maps"])


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


class SavedMapCreate(BaseModel):
    """Request body to create or replace a saved map.

    ``layers`` is an ordered list of layer descriptors; ``styles`` /
    ``filters`` are keyed by layer id; ``view`` holds the viewport. The
    inner shapes are intentionally free-form (passthrough JSON) so the
    editor can evolve them without a backend change.
    """

    name: str
    description: str = ""
    project_id: UUID | None = None
    layers: list[dict[str, Any]] = Field(default_factory=list)
    styles: dict[str, Any] = Field(default_factory=dict)
    view: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    basemap: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SavedMapResponse(BaseModel):
    id: str
    name: str
    description: str
    project_id: str | None
    layers: list[dict[str, Any]]
    styles: dict[str, Any]
    view: dict[str, Any]
    filters: dict[str, Any]
    basemap: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class PaginatedSavedMaps(BaseModel):
    items: list[SavedMapResponse]
    total: int
    limit: int
    offset: int


def _to_response(m: SavedMap) -> SavedMapResponse:
    return SavedMapResponse(
        id=str(m.id),
        name=m.name,
        description=m.description,
        project_id=str(m.project_id) if m.project_id is not None else None,
        layers=m.layers,
        styles=m.styles,
        view=m.view,
        filters=m.filters,
        basemap=m.basemap,
        metadata=m.metadata,
        created_at=m.created_at.isoformat(),
        updated_at=m.updated_at.isoformat(),
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("", response_model=SavedMapResponse, status_code=201)
@limiter.limit("30/minute")
def create_map(
    request: Request,
    body: SavedMapCreate,
    repo: Repository = Depends(get_saved_map_repo),
) -> SavedMapResponse:
    saved = SavedMap(
        name=body.name,
        description=body.description,
        project_id=body.project_id,
        layers=body.layers,
        styles=body.styles,
        view=body.view,
        filters=body.filters,
        basemap=body.basemap,
        metadata=body.metadata,
    )
    repo.save(saved)
    return _to_response(saved)


@router.get("", response_model=PaginatedSavedMaps)
def list_maps(
    limit: int = 50,
    offset: int = 0,
    project_id: UUID | None = None,
    repo: Repository = Depends(get_saved_map_repo),
) -> PaginatedSavedMaps:
    maps = repo.list_all()
    if project_id is not None:
        maps = [m for m in maps if str(m.project_id) == str(project_id)]
    items = [_to_response(m) for m in maps]
    return PaginatedSavedMaps(
        items=items[offset: offset + limit],
        total=len(items),
        limit=limit,
        offset=offset,
    )


@router.get("/{map_id}", response_model=SavedMapResponse)
def get_map(
    map_id: UUID,
    repo: Repository = Depends(get_saved_map_repo),
) -> SavedMapResponse:
    saved = repo.get(map_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved map not found")
    return _to_response(saved)


@router.put("/{map_id}", response_model=SavedMapResponse)
def update_map(
    map_id: UUID,
    body: SavedMapCreate,
    repo: Repository = Depends(get_saved_map_repo),
) -> SavedMapResponse:
    saved = repo.get(map_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved map not found")
    saved.name = body.name
    saved.description = body.description
    saved.project_id = body.project_id
    saved.layers = body.layers
    saved.styles = body.styles
    saved.view = body.view
    saved.filters = body.filters
    saved.basemap = body.basemap
    saved.metadata = body.metadata
    saved.updated_at = datetime.now(timezone.utc)
    repo.save(saved)
    return _to_response(saved)


@router.delete("/{map_id}", status_code=204)
def delete_map(
    map_id: UUID,
    repo: Repository = Depends(get_saved_map_repo),
) -> None:
    if not repo.delete(map_id):
        raise HTTPException(status_code=404, detail="Saved map not found")
