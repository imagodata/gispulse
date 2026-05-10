"""Public, auth-bypassed viewer for Cocarte maps.

Exposes the read-only surface a non-authenticated visitor needs to
view a published map at `/c/{slug}` or, for `visibility=unlisted`
maps, at `/c/by-token/{token}`.

Endpoints:

* ``GET /c/{slug}``                   — public map view by slug
* ``GET /c/by-token/{token}``         — unlisted map view by share token
                                        (constant-time lookup)
* ``GET /c/health``                   — liveness for the public surface

Visibility semantics:

* ``private``  — always 404 from the public router (no leak of existence)
* ``unlisted`` — 404 from `/c/{slug}`; visible only via `/c/by-token/{token}`
* ``public``   — 200 from `/c/{slug}`

Issue imagodata/gispulse-portal#59 (Sprint 1.2 public viewer route).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.models import CocarteMap, MapVisibility
from gispulse.adapters.http.dependencies import get_map_repo
from persistence.map_io import MapRepository

router = APIRouter(prefix="/c", tags=["cocarte-public"])


# ------------------------------------------------------------------
# Schema — sanitised projection
# ------------------------------------------------------------------


class MapPublic(BaseModel):
    """Public-facing projection of a CocarteMap.

    Drops `owner_id` and `share_token` so a published map never leaks
    those fields. The `published_at` field is exposed so consumers can
    show "published on" dates; `created_at` and `updated_at` remain so
    the viewer SPA can compute "last updated" labels.
    """

    id: str
    slug: str
    title: str
    description: str
    visibility: MapVisibility
    view_state: dict[str, Any]
    layers: list[dict[str, Any]]
    style_overrides: dict[str, Any]
    snapshot_uri: str | None
    published_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_model(cls, m: CocarteMap) -> "MapPublic":
        return cls(
            id=str(m.id),
            slug=m.slug,
            title=m.title,
            description=m.description,
            visibility=m.visibility,
            view_state=dict(m.view_state),
            layers=list(m.layers),
            style_overrides=dict(m.style_overrides),
            snapshot_uri=m.snapshot_uri,
            published_at=m.published_at.isoformat() if m.published_at else None,
            created_at=m.created_at.isoformat(),
            updated_at=m.updated_at.isoformat(),
        )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/health")
def public_health() -> dict[str, str]:
    """Liveness probe for the public viewer surface."""
    return {"status": "ok"}


@router.get("/by-token/{token}", response_model=MapPublic)
def get_by_share_token(
    token: str,
    repo: MapRepository = Depends(get_map_repo),
) -> MapPublic:
    """Resolve an unlisted map via its opaque share token (constant-time)."""
    if not token or len(token) > 200:
        raise HTTPException(status_code=404, detail="Map not found")
    m = repo.get_by_share_token(token)
    if m is None or m.visibility != MapVisibility.UNLISTED:
        # Return 404 (not 403) so the response shape is identical to
        # an unknown token. Prevents enumerating valid tokens.
        raise HTTPException(status_code=404, detail="Map not found")
    return MapPublic.from_model(m)


@router.get("/{slug}", response_model=MapPublic)
def get_by_slug(
    slug: str,
    repo: MapRepository = Depends(get_map_repo),
) -> MapPublic:
    """Resolve a public map by its URL-safe slug.

    `private` → 404 (no leak). `unlisted` → 404 from this route; use
    `/c/by-token/{token}` instead. `public` → 200.
    """
    m = repo.get_by_slug(slug)
    if m is None:
        raise HTTPException(status_code=404, detail="Map not found")
    if m.visibility != MapVisibility.PUBLIC:
        # Hide existence of private and unlisted maps from this endpoint.
        raise HTTPException(status_code=404, detail="Map not found")
    return MapPublic.from_model(m)
