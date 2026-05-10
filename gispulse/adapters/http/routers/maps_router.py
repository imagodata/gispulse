"""REST router for Cocarte maps (v1.7 ``Publish``).

Endpoints:
    POST   /maps                       — create a map (tier-gated, owner-scoped)
    GET    /maps                       — list current user's maps (active by default)
    GET    /maps/{id}                  — get a single map (owner-scoped)
    PATCH  /maps/{id}                  — partial update (owner-scoped)
    DELETE /maps/{id}                  — soft-delete (move to trash)
    POST   /maps/{id}/restore          — undelete a trashed map
    POST   /maps/{id}/rotate-token     — rotate share token (visibility=unlisted only)

Public viewer route ``GET /c/{slug}`` (auth-bypassed) is delivered in
Sprint 1.2 — out of scope for issue #56.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.models import CocarteMap, MapVisibility
from gispulse.adapters.http.auth import get_current_user
from gispulse.adapters.http.dependencies import get_map_repo
from gispulse.adapters.http.rate_limit import limiter
from persistence.map_io import MapRepository

router = APIRouter(prefix="/maps", tags=["cocarte"])


# Mirrors `core/pricing_catalog.yml` (`tiers.*.limits.maps`). `None` = unlimited.
# Keep in sync if the catalog changes.
_MAP_LIMITS: dict[str, int | None] = {
    "community": 5,
    "pro": 100,
    "team": None,
    "enterprise": None,
}


def _enforce_map_limit(repo: MapRepository, owner_id: UUID | None) -> None:
    from persistence.tier import get_current_tier

    tier = get_current_tier()
    limit = _MAP_LIMITS.get(tier, _MAP_LIMITS["community"])
    if limit is None:
        return
    count = repo.count_for_owner(owner_id)
    if count >= limit:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Map limit reached for tier '{tier}' ({count}/{limit}). "
                "Upgrade to Pro for 100 maps, or Team for unlimited."
            ),
        )


def _check_owner(m: CocarteMap, user) -> None:
    """Reject access if *user* is not the map owner (admins bypass).

    ``owner_id is None`` means single-user/legacy instance — anyone may
    edit. New maps created via this router always carry an ``owner_id``.
    """
    if user is None or m.owner_id is None:
        return
    if getattr(user, "role", None) == "admin":
        return
    if m.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not the map owner")


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


class MapCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    project_id: UUID | None = None


class MapUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    visibility: MapVisibility | None = None
    view_state: dict | None = None
    layers: list[dict] | None = None
    style_overrides: dict | None = None
    metadata: dict | None = None


class MapResponse(BaseModel):
    """Full server-side view (owner only)."""

    id: str
    slug: str
    project_id: str | None
    owner_id: str | None
    title: str
    description: str
    visibility: MapVisibility
    share_token: str | None
    view_state: dict
    layers: list[dict]
    style_overrides: dict
    snapshot_uri: str | None
    published_at: str | None
    template_origin_id: str | None
    deleted_at: str | None
    metadata: dict
    created_at: str
    updated_at: str


class MapListResponse(BaseModel):
    items: list[MapResponse]
    total: int
    limit: int
    offset: int


def _to_response(m: CocarteMap) -> MapResponse:
    return MapResponse(
        id=str(m.id),
        slug=m.slug,
        project_id=str(m.project_id) if m.project_id else None,
        owner_id=str(m.owner_id) if m.owner_id else None,
        title=m.title,
        description=m.description,
        visibility=m.visibility,
        share_token=m.share_token,
        view_state=m.view_state,
        layers=m.layers,
        style_overrides=m.style_overrides,
        snapshot_uri=m.snapshot_uri,
        published_at=m.published_at.isoformat() if m.published_at else None,
        template_origin_id=str(m.template_origin_id) if m.template_origin_id else None,
        deleted_at=m.deleted_at.isoformat() if m.deleted_at else None,
        metadata=m.metadata,
        created_at=m.created_at.isoformat(),
        updated_at=m.updated_at.isoformat(),
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("", response_model=MapResponse, status_code=201)
@limiter.limit("20/minute")
def create_map(
    request: Request,
    body: MapCreate,
    repo: MapRepository = Depends(get_map_repo),
    user=Depends(get_current_user),
) -> MapResponse:
    owner_id: UUID | None = user.id if user is not None else None
    _enforce_map_limit(repo, owner_id)

    m = CocarteMap(
        title=body.title,
        description=body.description,
        project_id=body.project_id,
        owner_id=owner_id,
    )
    m.slug = repo.allocate_slug(body.title)
    repo.save(m)
    return _to_response(m)


@router.get("", response_model=MapListResponse)
def list_maps(
    limit: int = 50,
    offset: int = 0,
    include_trashed: bool = False,
    repo: MapRepository = Depends(get_map_repo),
    user=Depends(get_current_user),
) -> MapListResponse:
    owner_id: UUID | None = user.id if user is not None else None
    if user is not None and getattr(user, "role", None) == "admin":
        all_maps = repo.list_all(include_trashed=include_trashed)
    else:
        all_maps = repo.list_for_owner(owner_id, include_trashed=include_trashed)
    items = [_to_response(m) for m in all_maps[offset : offset + limit]]
    return MapListResponse(items=items, total=len(all_maps), limit=limit, offset=offset)


@router.get("/{map_id}", response_model=MapResponse)
def get_map(
    map_id: UUID,
    include_trashed: bool = False,
    repo: MapRepository = Depends(get_map_repo),
    user=Depends(get_current_user),
) -> MapResponse:
    m = repo.get(map_id, include_trashed=include_trashed)
    if m is None:
        raise HTTPException(status_code=404, detail="Map not found")
    _check_owner(m, user)
    return _to_response(m)


@router.patch("/{map_id}", response_model=MapResponse)
def update_map(
    map_id: UUID,
    body: MapUpdate,
    repo: MapRepository = Depends(get_map_repo),
    user=Depends(get_current_user),
) -> MapResponse:
    m = repo.get(map_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Map not found")
    _check_owner(m, user)

    # Title rename freezes once the slug is published.
    if body.title is not None and m.published_at is None:
        m.title = body.title
    elif body.title is not None and body.title != m.title:
        # Allow title rename after publish, but slug remains frozen.
        m.title = body.title

    if body.description is not None:
        m.description = body.description
    if body.view_state is not None:
        m.view_state = body.view_state
    if body.layers is not None:
        m.layers = body.layers
    if body.style_overrides is not None:
        m.style_overrides = body.style_overrides
    if body.metadata is not None:
        m.metadata = body.metadata

    if body.visibility is not None and body.visibility != m.visibility:
        m.visibility = body.visibility
        if body.visibility == MapVisibility.UNLISTED and not m.share_token:
            m.share_token = secrets.token_urlsafe(32)
        elif body.visibility != MapVisibility.UNLISTED:
            m.share_token = None

    repo.save(m)
    return _to_response(m)


@router.delete("/{map_id}", status_code=204)
def delete_map(
    map_id: UUID,
    repo: MapRepository = Depends(get_map_repo),
    user=Depends(get_current_user),
) -> None:
    m = repo.get(map_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Map not found")
    _check_owner(m, user)
    if not repo.soft_delete(map_id):
        raise HTTPException(status_code=404, detail="Map not found")


@router.post("/{map_id}/restore", response_model=MapResponse)
def restore_map(
    map_id: UUID,
    repo: MapRepository = Depends(get_map_repo),
    user=Depends(get_current_user),
) -> MapResponse:
    m = repo.get(map_id, include_trashed=True)
    if m is None:
        raise HTTPException(status_code=404, detail="Map not found")
    _check_owner(m, user)
    if not repo.restore(map_id):
        raise HTTPException(status_code=409, detail="Map is not trashed")
    refreshed = repo.get(map_id)
    assert refreshed is not None
    return _to_response(refreshed)


@router.post("/{map_id}/rotate-token", response_model=MapResponse)
def rotate_share_token(
    map_id: UUID,
    repo: MapRepository = Depends(get_map_repo),
    user=Depends(get_current_user),
) -> MapResponse:
    m = repo.get(map_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Map not found")
    _check_owner(m, user)
    if m.visibility != MapVisibility.UNLISTED:
        raise HTTPException(
            status_code=409,
            detail="Share-token rotation is only valid for visibility='unlisted'",
        )
    m.share_token = secrets.token_urlsafe(32)
    m.updated_at = datetime.now(timezone.utc)
    repo.save(m)
    return _to_response(m)
