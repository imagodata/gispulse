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

import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gispulse.adapters.http.dependencies import get_saved_map_repo
from gispulse.adapters.http.rate_limit import limiter
from gispulse.core.models import SavedMap
from gispulse.persistence.repository import Repository
from gispulse.persistence.tier import get_current_tier

router = APIRouter(prefix="/maps", tags=["maps"])


# Number of *published* maps allowed per tier. ``None`` = unlimited.
# Mirrors the count-gate convention of projects_router._PROJECT_LIMITS;
# keep in sync with pricing_catalog.yml if a dedicated limit is added there.
_PUBLISHED_MAP_LIMITS: dict[str, int | None] = {
    "community": 3,
    "pro": None,
    "team": None,
    "enterprise": None,
}


def _enforce_publish_limit(repo: Repository, current: SavedMap) -> None:
    """Block a *new* publication when the tier's published-map quota is hit.

    Re-publishing an already-published map is always allowed (it does not
    grow the count).
    """
    tier = get_current_tier()
    limit = _PUBLISHED_MAP_LIMITS.get(tier, _PUBLISHED_MAP_LIMITS["community"])
    if limit is None:
        return
    if current.publication:  # already published → re-publish, no new slot
        return
    published = sum(1 for m in repo.list_all() if m.publication)
    if published >= limit:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Published-map limit reached for tier '{tier}' "
                f"({published}/{limit}). Upgrade for unlimited published maps."
            ),
        )


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
    is_published: bool
    public_token: str | None
    created_at: str
    updated_at: str


class PublicationResponse(BaseModel):
    """Returned by POST /maps/{id}/publish."""

    map_id: str
    token: str
    url: str
    published_at: str
    layer_count: int


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
        is_published=bool(m.publication),
        public_token=m.publication.get("token") if m.publication else None,
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


# ------------------------------------------------------------------
# Publication (#406) — public permalink by token
# ------------------------------------------------------------------


def find_map_by_token(repo: Repository, token: str) -> SavedMap | None:
    """Return the published map whose public token matches, or None.

    Used by the unauthenticated public router. The repository is small
    (published maps are quota-limited), so a linear scan is acceptable.
    """
    if not token:
        return None
    for m in repo.list_all():
        pub = m.publication
        if pub and pub.get("token") == token:
            return m
    return None


@router.post("/{map_id}/publish", response_model=PublicationResponse)
@limiter.limit("20/minute")
def publish_map(
    request: Request,
    map_id: UUID,
    repo: Repository = Depends(get_saved_map_repo),
) -> PublicationResponse:
    """Publish a saved map under a non-guessable public token.

    Freezes a snapshot of the current layers/styles/view/basemap — the
    public endpoints serve only this snapshot (an explicit allowlist), so
    later edits to the live map don't leak until it is re-published.
    Re-publishing keeps the same token but refreshes the snapshot.
    """
    saved = repo.get(map_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved map not found")

    _enforce_publish_limit(repo, saved)

    token = saved.publication.get("token") if saved.publication else None
    if not token:
        token = f"pub_{secrets.token_urlsafe(24)}"
    published_at = datetime.now(timezone.utc).isoformat()
    saved.publication = {
        "token": token,
        "published_at": published_at,
        "layers": saved.layers,
        "styles": saved.styles,
        "view": saved.view,
        "basemap": saved.basemap,
    }
    saved.updated_at = datetime.now(timezone.utc)
    repo.save(saved)

    return PublicationResponse(
        map_id=str(saved.id),
        token=token,
        url=f"/public/maps/{token}",
        published_at=published_at,
        layer_count=len(saved.layers),
    )


@router.delete("/{map_id}/publish", status_code=204)
def unpublish_map(
    map_id: UUID,
    repo: Repository = Depends(get_saved_map_repo),
) -> None:
    """Unpublish a map — the public token is invalidated immediately."""
    saved = repo.get(map_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved map not found")
    if saved.publication:
        saved.publication = {}
        saved.updated_at = datetime.now(timezone.utc)
        repo.save(saved)
