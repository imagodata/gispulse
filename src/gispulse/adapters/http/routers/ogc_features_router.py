"""
OGC API Features (Part 1) router for GISPulse.

Implements the six core endpoints of the OGC API — Features — Part 1
standard (OGC 17-069r4), exposing registered datasets as feature
collections with GeoJSON output in WGS 84.

Endpoints:
    GET /ogc/                              — Landing page
    GET /ogc/conformance                   — Conformance declaration
    GET /ogc/collections                   — List all collections
    GET /ogc/collections/{id}              — Single collection metadata
    GET /ogc/collections/{id}/items        — Paginated features (GeoJSON)
    GET /ogc/collections/{id}/items/{fid}  — Single feature
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from gispulse.adapters.http.dependencies import get_dataset_repo
from gispulse.persistence.io import read_vector as load_ogc_dataset
from gispulse.core.models import Dataset
from gispulse.persistence.io import read_vector
from gispulse.persistence.repository import Repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ogc", tags=["OGC API Features"])

_MEDIA_GEOJSON = "application/geo+json"

_CONFORMANCE_CLASSES = [
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_base_url(request: Request) -> str:
    """Reconstruct the OGC root URL from the incoming request."""
    return str(request.base_url).rstrip("/") + "/ogc"


def _get_dataset_or_404(
    collection_id: UUID, repo: Repository
) -> Dataset:
    ds = repo.get(collection_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Collection '{collection_id}' not found.")
    return ds  # type: ignore[return-value]


def _load_features(ds: Dataset, bbox: tuple[float, float, float, float] | None = None):
    """Load features from a dataset as a GeoDataFrame, reprojected to WGS 84."""

    if ds.ogc_source is not None:
        gdf = load_ogc_dataset(ds, bbox=bbox)
    elif ds.source_path:
        gdf = read_vector(ds.source_path, bbox=bbox)
    else:
        raise HTTPException(
            status_code=422,
            detail=f"Dataset '{ds.id}' has no source path or OGC source.",
        )

    # Reproject to WGS 84 if needed
    if gdf.crs is not None and not gdf.crs.equals("EPSG:4326"):
        gdf = gdf.to_crs("EPSG:4326")
    elif gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")

    return gdf


def _gdf_to_geojson_fc(gdf, total: int, limit: int, offset: int) -> dict:
    """Convert a GeoDataFrame slice to a GeoJSON FeatureCollection dict."""
    from shapely.geometry import mapping

    features = []
    for idx, row in gdf.iterrows():
        geom = row.geometry
        props = {
            k: _json_safe(v)
            for k, v in row.drop("geometry").items()
        }
        features.append({
            "type": "Feature",
            "id": str(idx),
            "geometry": mapping(geom) if geom is not None and not geom.is_empty else None,
            "properties": props,
        })

    return {
        "type": "FeatureCollection",
        "numberMatched": total,
        "numberReturned": len(features),
        "features": features,
    }


def _json_safe(v):
    """Make a value JSON-serialisable."""
    import numpy as np
    from datetime import datetime, date

    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, np.ndarray):
        return v.tolist()
    return v


def _feature_to_geojson(gdf, fid: int) -> dict:
    """Extract a single feature as a GeoJSON Feature dict."""
    from shapely.geometry import mapping

    if fid < 0 or fid >= len(gdf):
        raise HTTPException(status_code=404, detail=f"Feature '{fid}' not found.")

    row = gdf.iloc[fid]
    geom = row.geometry
    props = {
        k: _json_safe(v)
        for k, v in row.drop("geometry").items()
    }
    return {
        "type": "Feature",
        "id": str(fid),
        "geometry": mapping(geom) if geom is not None and not geom.is_empty else None,
        "properties": props,
    }


def _collection_meta(ds: Dataset, base_url: str) -> dict:
    """Build the OGC collection metadata dict for a dataset."""
    extent_spatial = None
    layers = ds.metadata.get("layers", [])
    # Try to get extent from metadata if available
    if layers:
        first_layer = layers[0]
        if "bbox" in first_layer:
            extent_spatial = {"bbox": [first_layer["bbox"]], "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}

    result: dict = {
        "id": str(ds.id),
        "title": ds.name,
        "description": ds.metadata.get("description", ""),
        "links": [
            {
                "href": f"{base_url}/collections/{ds.id}",
                "rel": "self",
                "type": "application/json",
            },
            {
                "href": f"{base_url}/collections/{ds.id}/items",
                "rel": "items",
                "type": _MEDIA_GEOJSON,
            },
        ],
        "crs": [ds.crs],
    }
    if extent_spatial:
        result["extent"] = {"spatial": extent_spatial}
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/")
def landing_page(request: Request) -> JSONResponse:
    """OGC API Features landing page.

    Returns service metadata and navigation links conformant with
    OGC API — Features — Part 1 (clause 7.2).
    """
    base = _build_base_url(request)
    body = {
        "title": "GISPulse OGC API Features",
        "description": "Access GISPulse datasets as OGC API Feature collections.",
        "links": [
            {"href": base + "/", "rel": "self", "type": "application/json"},
            {"href": base + "/conformance", "rel": "conformance", "type": "application/json"},
            {"href": base + "/collections", "rel": "data", "type": "application/json"},
        ],
    }
    return JSONResponse(content=body, media_type="application/json")


@router.get("/conformance")
def conformance() -> JSONResponse:
    """OGC API Features conformance declaration (clause 7.4)."""
    return JSONResponse(
        content={"conformsTo": _CONFORMANCE_CLASSES},
        media_type="application/json",
    )


@router.get("/collections")
def list_collections(
    request: Request,
    repo: Repository = Depends(get_dataset_repo),
) -> JSONResponse:
    """List all registered datasets as OGC feature collections (clause 7.13)."""
    base = _build_base_url(request)
    datasets = repo.list_all()
    collections = [_collection_meta(ds, base) for ds in datasets]
    body = {
        "collections": collections,
        "links": [
            {"href": base + "/collections", "rel": "self", "type": "application/json"},
        ],
    }
    return JSONResponse(content=body, media_type="application/json")


@router.get("/collections/{collection_id}")
def get_collection(
    collection_id: UUID,
    request: Request,
    repo: Repository = Depends(get_dataset_repo),
) -> JSONResponse:
    """Return metadata for a single collection (clause 7.14)."""
    ds = _get_dataset_or_404(collection_id, repo)
    base = _build_base_url(request)
    return JSONResponse(
        content=_collection_meta(ds, base),
        media_type="application/json",
    )


@router.get("/collections/{collection_id}/items")
def get_collection_items(
    collection_id: UUID,
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    bbox: str | None = Query(None, description="Bounding box: minx,miny,maxx,maxy"),
    repo: Repository = Depends(get_dataset_repo),
) -> JSONResponse:
    """Return paginated features for a collection as GeoJSON (clause 7.15).

    Supports ``limit``, ``offset``, and ``bbox`` query parameters.
    All geometries are returned in WGS 84 (CRS84).
    """
    ds = _get_dataset_or_404(collection_id, repo)

    bbox_tuple: tuple[float, float, float, float] | None = None
    if bbox:
        try:
            parts = [float(x.strip()) for x in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            bbox_tuple = (parts[0], parts[1], parts[2], parts[3])
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail="Invalid bbox format. Expected: minx,miny,maxx,maxy",
            )

    try:
        gdf = _load_features(ds, bbox=bbox_tuple)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Source file for dataset '{ds.id}' not found.")
    except Exception as exc:
        logger.exception("Failed to load features for collection %s", collection_id)
        raise HTTPException(status_code=500, detail=f"Error loading features: {exc}")

    total = len(gdf)
    page = gdf.iloc[offset : offset + limit]

    fc = _gdf_to_geojson_fc(page, total=total, limit=limit, offset=offset)

    # Navigation links
    base = _build_base_url(request)
    items_url = f"{base}/collections/{collection_id}/items"
    fc["links"] = [
        {"href": f"{items_url}?limit={limit}&offset={offset}", "rel": "self", "type": _MEDIA_GEOJSON},
    ]
    if offset + limit < total:
        fc["links"].append({
            "href": f"{items_url}?limit={limit}&offset={offset + limit}",
            "rel": "next",
            "type": _MEDIA_GEOJSON,
        })

    return JSONResponse(content=fc, media_type=_MEDIA_GEOJSON)


@router.get("/collections/{collection_id}/items/{fid}")
def get_collection_item(
    collection_id: UUID,
    fid: int,
    repo: Repository = Depends(get_dataset_repo),
) -> JSONResponse:
    """Return a single feature by index (clause 7.16).

    The feature ID is a zero-based integer index into the dataset.
    Geometries are returned in WGS 84 (CRS84).
    """
    ds = _get_dataset_or_404(collection_id, repo)

    try:
        gdf = _load_features(ds)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Source file for dataset '{ds.id}' not found.")
    except Exception as exc:
        logger.exception("Failed to load features for collection %s", collection_id)
        raise HTTPException(status_code=500, detail=f"Error loading features: {exc}")

    try:
        feature = _feature_to_geojson(gdf, fid)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading feature: {exc}")

    return JSONResponse(content=feature, media_type=_MEDIA_GEOJSON)
