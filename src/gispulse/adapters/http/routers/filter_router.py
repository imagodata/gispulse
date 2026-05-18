"""
Filter router for GISPulse — interactive spatial filtering.

Provides endpoints for applying attribute and spatial filters interactively
from the FilterPanel UI, powered by the FilterService orchestration layer.

Endpoints:
    POST /api/filter/preview    — Count + bbox without returning features
    POST /api/filter/apply      — Apply filter and return GeoJSON features
    POST /api/filter/chain      — Apply a multi-step FilterChain
    POST /api/filter/validate   — Validate an expression without executing
    GET  /api/filter/predicates — List available spatial predicates
    GET  /api/filter/cache/stats — Cache statistics
    DELETE /api/filter/cache     — Invalidate cache
"""

from __future__ import annotations

import json
from typing import Any

import geopandas as gpd
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from gispulse.adapters.http.rate_limit import limiter
from gispulse.core.filter.cache import FilterCache
from gispulse.core.filter.chain import FilterChain
from gispulse.core.filter.expression import Dialect, FilterExpression, SpatialPredicate
from gispulse.core.filter.expression_converter import ExpressionConverter
from gispulse.core.filter.service import FilterService
from gispulse.core.filter.types import CombinationStrategy, Filter, FilterType
from gispulse.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/filter", tags=["filter"])

# ---------------------------------------------------------------------------
# Spatial predicates metadata
# ---------------------------------------------------------------------------

SPATIAL_PREDICATES = [
    {"id": "intersects", "label": "Intersects", "description": "Features that spatially intersect the source"},
    {"id": "contains", "label": "Contains", "description": "Features that contain the source geometry"},
    {"id": "within", "label": "Within", "description": "Features that are within the source geometry"},
    {"id": "crosses", "label": "Crosses", "description": "Features whose boundary crosses the source"},
    {"id": "touches", "label": "Touches", "description": "Features that touch the source boundary"},
    {"id": "overlaps", "label": "Overlaps", "description": "Features that overlap the source geometry"},
    {"id": "disjoint", "label": "Disjoint", "description": "Features with no spatial relationship"},
    {"id": "equals", "label": "Equals", "description": "Features with identical geometry"},
    {"id": "dwithin", "label": "DWithin", "description": "Features within a given distance"},
]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class FilterRequest(BaseModel):
    """Interactive filter request from the FilterPanel."""
    dataset_id: str = Field(..., description="Source dataset ID")
    layer_name: str = Field(..., description="Target layer to filter")
    expression: str | None = Field(None, description="Pandas query expression for attribute filter")
    spatial_predicate: str | None = Field(None, description="Spatial predicate (intersects, within, etc.)")
    ref_dataset_id: str | None = Field(None, description="Reference dataset ID for spatial filter")
    ref_layer_name: str | None = Field(None, description="Reference layer name for spatial filter")
    ref_wkt: str | None = Field(None, description="WKT geometry for spatial filter")
    ref_geojson: dict | None = Field(None, description="GeoJSON geometry for spatial filter")
    buffer_distance: float | None = Field(None, description="Buffer distance in meters (applied to ref geometry)")
    limit: int = Field(10000, description="Max features to return", ge=1, le=100000)


class FilterChainRequest(BaseModel):
    """Multi-step filter chain request."""
    dataset_id: str
    layer_name: str
    combination_strategy: str = "priority_and"
    filters: list[dict[str, Any]]
    limit: int = Field(10000, ge=1, le=100000)


class FilterValidateRequest(BaseModel):
    """Expression validation request."""
    expression: str


class FilterPreviewResponse(BaseModel):
    count: int
    total: int
    bbox: list[float] | None = None
    execution_time_ms: float = 0.0
    is_cached: bool = False
    backend: str = ""


class FilterApplyResponse(BaseModel):
    type: str = "FeatureCollection"
    features: list[dict[str, Any]]
    total_count: int
    filtered_count: int
    bbox: list[float] | None = None
    execution_time_ms: float = 0.0
    is_cached: bool = False
    backend: str = ""


class FilterValidateResponse(BaseModel):
    is_valid: bool
    errors: list[str] = []


class CacheStatsResponse(BaseModel):
    hits: int
    misses: int
    size: int
    max_size: int
    hit_rate: float
    utilization: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_filter_service(request: Request) -> FilterService:
    """Get or create the FilterService from app state."""
    if hasattr(request.app.state, "filter_service"):
        return request.app.state.filter_service

    engine = getattr(request.app.state, "spatial_engine", None)
    if engine is None:
        raise HTTPException(500, "No spatial engine configured")

    cache = getattr(request.app.state, "filter_cache", None)
    if cache is None:
        cache = FilterCache(max_size=256, default_ttl_seconds=300)
        request.app.state.filter_cache = cache

    service = FilterService(engine=engine, cache=cache)
    request.app.state.filter_service = service
    return service


def _get_layer_gdf(request: Request, dataset_id: str, layer_name: str) -> gpd.GeoDataFrame:
    """Resolve a layer from the cache or raise 404."""
    cache: dict = request.app.state.layer_cache
    cache_key = f"{dataset_id}::{layer_name}"

    if cache_key in cache:
        return cache[cache_key]

    repo = request.app.state.dataset_repo
    ds = repo.get(dataset_id)
    if ds is None:
        raise HTTPException(404, f"Dataset {dataset_id} not found")

    from pathlib import Path

    from gispulse.persistence.io import read_vector

    data_dir: Path = request.app.state.data_dir
    source_path = data_dir / ds.source_path if not Path(ds.source_path).is_absolute() else Path(ds.source_path)

    if not source_path.exists():
        raise HTTPException(404, f"Source file not found: {ds.source_path}")

    gdf = read_vector(str(source_path), layer=layer_name)
    cache[cache_key] = gdf
    return gdf


def _resolve_ref_gdf(
    request: Request,
    req: FilterRequest,
) -> gpd.GeoDataFrame | None:
    """Build a reference GeoDataFrame from the filter request."""
    from shapely import wkt as shapely_wkt
    from shapely.geometry import shape

    if req.ref_dataset_id and req.ref_layer_name:
        return _get_layer_gdf(request, req.ref_dataset_id, req.ref_layer_name)

    geom = None
    if req.ref_geojson:
        geom = shape(req.ref_geojson)
    elif req.ref_wkt:
        geom = shapely_wkt.loads(req.ref_wkt)

    if geom is not None:
        return gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
    return None


def _build_expression(req: FilterRequest, ref_wkt: str | None = None) -> FilterExpression:
    """Build a FilterExpression from the API request."""
    if req.spatial_predicate:
        try:
            pred = SpatialPredicate(req.spatial_predicate)
        except ValueError:
            raise HTTPException(400, f"Unknown spatial predicate: {req.spatial_predicate}")

        if req.expression:
            # Mixed attribute + spatial
            return FilterExpression(
                raw=req.expression,
                dialect=Dialect.PANDAS,
                is_spatial=True,
                spatial_predicates=(pred,),
                buffer_value=req.buffer_distance,
                target_layer=f"{req.dataset_id}::{req.layer_name}",
                ref_wkt=ref_wkt,
            )
        return FilterExpression.create_spatial(
            [pred],
            buffer_value=req.buffer_distance or 0,
            target_layer=f"{req.dataset_id}::{req.layer_name}",
            ref_wkt=ref_wkt,
        )

    if req.expression:
        return FilterExpression.create(
            req.expression,
            target_layer=f"{req.dataset_id}::{req.layer_name}",
        )

    raise HTTPException(400, "No filter expression or spatial predicate provided")


def _gdf_to_geojson_features(gdf: gpd.GeoDataFrame, limit: int) -> list[dict]:
    if gdf.empty:
        return []
    from gispulse.adapters.http.layer_utils import sanitize_datetime_columns
    subset = sanitize_datetime_columns(gdf.head(limit))
    fc = json.loads(subset.to_json())
    return fc.get("features", [])


def _bbox_list(bbox: tuple[float, ...] | None) -> list[float] | None:
    if bbox is None:
        return None
    return [float(b) for b in bbox]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/predicates")
def list_predicates() -> list[dict]:
    """List available spatial predicates."""
    return SPATIAL_PREDICATES


@router.post("/preview", response_model=FilterPreviewResponse)
@limiter.limit("30/minute")
def preview_filter(req: FilterRequest, request: Request) -> FilterPreviewResponse:
    """Preview filter results: count + bbox without returning features."""
    service = _get_filter_service(request)
    gdf = _get_layer_gdf(request, req.dataset_id, req.layer_name)
    total = len(gdf)

    ref_gdf = _resolve_ref_gdf(request, req)
    ref_wkt = None
    if ref_gdf is not None:
        ref_wkt = ref_gdf.union_all().wkt

    try:
        expr = _build_expression(req, ref_wkt)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Invalid filter: {exc}")

    result = service.preview(expr, gdf, ref_gdf)

    if result.has_error:
        raise HTTPException(400, result.error_message or "Filter error")

    return FilterPreviewResponse(
        count=result.feature_count,
        total=total,
        bbox=_bbox_list(result.bbox),
        execution_time_ms=result.execution_time_ms,
        is_cached=result.is_cached,
        backend=result.backend_name,
    )


@router.post("/apply", response_model=FilterApplyResponse)
@limiter.limit("20/minute")
def apply_filter(req: FilterRequest, request: Request) -> FilterApplyResponse:
    """Apply filter and return GeoJSON features."""
    service = _get_filter_service(request)
    gdf = _get_layer_gdf(request, req.dataset_id, req.layer_name)
    total = len(gdf)

    ref_gdf = _resolve_ref_gdf(request, req)
    ref_wkt = None
    if ref_gdf is not None:
        ref_wkt = ref_gdf.union_all().wkt

    try:
        expr = _build_expression(req, ref_wkt)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Invalid filter: {exc}")

    result = service.apply(expr, gdf, ref_gdf)

    if result.has_error:
        raise HTTPException(400, result.error_message or "Filter error")

    features = _gdf_to_geojson_features(result.gdf, req.limit) if result.gdf is not None else []

    return FilterApplyResponse(
        features=features,
        total_count=total,
        filtered_count=result.feature_count,
        bbox=_bbox_list(result.bbox),
        execution_time_ms=result.execution_time_ms,
        is_cached=result.is_cached,
        backend=result.backend_name,
    )


@router.post("/chain", response_model=FilterApplyResponse)
def apply_chain(req: FilterChainRequest, request: Request) -> FilterApplyResponse:
    """Apply a multi-step FilterChain."""
    service = _get_filter_service(request)
    gdf = _get_layer_gdf(request, req.dataset_id, req.layer_name)
    total = len(gdf)

    try:
        strategy = CombinationStrategy(req.combination_strategy)
    except ValueError:
        raise HTTPException(400, f"Unknown combination strategy: {req.combination_strategy}")

    layer_key = f"{req.dataset_id}::{req.layer_name}"
    chain = FilterChain(target_layer=layer_key, combination_strategy=strategy)

    for fd in req.filters:
        try:
            ftype = FilterType(fd.get("type", "field_condition"))
        except ValueError:
            raise HTTPException(400, f"Unknown filter type: {fd.get('type')}")

        f = Filter(
            filter_type=ftype,
            expression=fd.get("expression", ""),
            layer_name=fd.get("layer_name", req.layer_name),
            priority=fd.get("priority"),
            combine_operator=fd.get("operator", "AND"),
            metadata=fd.get("metadata", {}),
        )
        if not chain.add_filter(f):
            raise HTTPException(400, f"Invalid filter: {f!r}")

    result = service.apply_chain(chain, gdf)

    if result.has_error:
        raise HTTPException(400, result.error_message or "Chain filter error")

    features = _gdf_to_geojson_features(result.gdf, req.limit) if result.gdf is not None else []

    return FilterApplyResponse(
        features=features,
        total_count=total,
        filtered_count=result.feature_count,
        bbox=_bbox_list(result.bbox),
        execution_time_ms=result.execution_time_ms,
        is_cached=result.is_cached,
        backend=result.backend_name,
    )


@router.post("/validate", response_model=FilterValidateResponse)
def validate_expression(req: FilterValidateRequest) -> FilterValidateResponse:
    """Validate a filter expression without executing it."""
    converter = ExpressionConverter()
    is_valid, errors = converter.validate(req.expression)
    return FilterValidateResponse(is_valid=is_valid, errors=errors)


@router.get("/cache/stats", response_model=CacheStatsResponse)
def cache_stats(request: Request) -> CacheStatsResponse:
    """Get filter cache statistics."""
    service = _get_filter_service(request)
    stats = service.get_cache_stats()
    return CacheStatsResponse(
        hits=stats.hits,
        misses=stats.misses,
        size=stats.size,
        max_size=stats.max_size,
        hit_rate=stats.hit_rate,
        utilization=stats.utilization,
    )


@router.delete("/cache")
def clear_cache(request: Request, layer_key: str | None = None) -> dict:
    """Invalidate filter cache (optionally for a specific layer)."""
    service = _get_filter_service(request)
    count = service.invalidate_cache(layer_key)
    return {"cleared": count, "layer_key": layer_key}
