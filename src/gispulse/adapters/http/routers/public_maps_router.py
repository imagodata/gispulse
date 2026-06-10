"""Public, unauthenticated read-only access to published maps (#406).

This router is the **only** unauthenticated surface for saved maps. It is
mounted with NO auth dependency, but it is a strict allowlist, not an
absence of guard (cf. #399):

* every endpoint is **GET** — mutations are impossible by construction
  (FastAPI answers 405 for any other method);
* content is reachable **only** by a non-guessable publication token;
* a published map exposes **only the snapshot of layers frozen at publish
  time** — there is no lateral access to the project's other datasets.

Endpoints::

    GET /public/maps/{token}                              — map snapshot
    GET /public/maps/{token}/layers/{layer}/features      — layer features
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from gispulse.adapters.http.layer_utils import gdf_to_feature_collection, load_layers
from gispulse.adapters.http.routers.saved_maps_router import find_map_by_token
from gispulse.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/public/maps", tags=["public-maps"])


def _published(request: Request, token: str):
    """Resolve a published map by token or raise 404.

    Returns the ``(SavedMap, publication_dict)`` pair. 404 (not 401/403) is
    deliberate: an unpublished or wrong token is indistinguishable from a
    non-existent one, so the token space can't be probed.
    """
    repo = request.app.state.saved_map_repo
    saved = find_map_by_token(repo, token)
    if saved is None or not saved.publication:
        raise HTTPException(status_code=404, detail="Published map not found")
    return saved, saved.publication


@router.get("/{token}")
def get_public_map(token: str, request: Request) -> JSONResponse:
    """Return the frozen snapshot of a published map (read-only)."""
    saved, pub = _published(request, token)
    return JSONResponse(
        content={
            "token": token,
            "name": saved.name,
            "description": saved.description,
            "layers": pub.get("layers", []),
            "styles": pub.get("styles", {}),
            "view": pub.get("view", {}),
            "basemap": pub.get("basemap", ""),
            "published_at": pub.get("published_at"),
        }
    )


@router.get("/{token}/layers/{layer}/features")
def get_public_map_features(
    token: str,
    layer: str,
    request: Request,
    bbox: str | None = Query(None, description="minx,miny,maxx,maxy (EPSG:4326)"),
    limit: int = Query(10000, ge=1, le=100000),
    offset: int = Query(0, ge=0),
    simplify: float | None = Query(None, ge=0),
) -> JSONResponse:
    """Serve features of a single published layer as GeoJSON (read-only).

    The ``layer`` must belong to the published snapshot's layer allowlist;
    any other dataset of the underlying project is unreachable.
    """
    saved, pub = _published(request, token)

    # Allowlist: find the published layer descriptor matching `layer`.
    descriptor = None
    for entry in pub.get("layers", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("layer") == layer or str(entry.get("id")) == layer:
            descriptor = entry
            break
    if descriptor is None:
        raise HTTPException(status_code=404, detail="Layer not in published map")

    dataset_id = descriptor.get("dataset_id")
    layer_name = descriptor.get("layer") or layer
    if not dataset_id:
        raise HTTPException(status_code=404, detail="Published layer has no dataset")

    # Resolve dataset_id → GeoDataFrame via the shared layer cache, mirroring
    # the portal feature endpoint (no auth, read-only).
    layer_cache: dict = getattr(request.app.state, "layer_cache", {})
    dataset_repo = request.app.state.dataset_repo
    gdfs = layer_cache.get(str(dataset_id))
    if gdfs is None:
        try:
            ds = dataset_repo.get(uuid.UUID(str(dataset_id)))
        except (ValueError, TypeError):
            raise HTTPException(status_code=404, detail="Published layer dataset not found")
        if ds is None or not ds.source_path:
            raise HTTPException(status_code=404, detail="Published layer dataset not found")
        from pathlib import Path

        if not Path(ds.source_path).exists():
            raise HTTPException(status_code=404, detail="Published layer data unavailable")
        _, gdfs = load_layers(ds.source_path, ds.name)
        layer_cache[str(dataset_id)] = gdfs

    if layer_name not in gdfs:
        raise HTTPException(status_code=404, detail="Layer not found in dataset")

    try:
        geojson = gdf_to_feature_collection(
            gdfs[layer_name],
            bbox=bbox,
            limit=limit,
            offset=offset,
            simplify=simplify,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid bbox. Expected: minx,miny,maxx,maxy")
    except Exception as exc:
        log.warning("public_map_features_failed", token=token, layer=layer, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to serialize layer")

    # Frozen-at-publish data → safe to cache aggressively at the edge.
    return JSONResponse(content=geojson, headers={"Cache-Control": "public, max-age=300"})
