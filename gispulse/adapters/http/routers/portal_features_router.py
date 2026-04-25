"""
Portal features router — feature CRUD (create, update, delete).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import geopandas as gpd
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gispulse.adapters.http.layer_utils import load_layers, sanitize_datetime_columns
from gispulse.adapters.http.rate_limit import limiter
from core.logging import get_logger

log = get_logger(__name__)

router = APIRouter()


class FeatureUpdateBody(BaseModel):
    properties: dict[str, Any] | None = None
    geometry: dict[str, Any] | None = None


@router.put("/datasets/{dataset_id}/layers/{layer_name}/features/{fid}")
@limiter.limit("60/minute")
async def update_feature(
    request: Request,
    dataset_id: str,
    layer_name: str,
    fid: int,
    body: FeatureUpdateBody,
) -> JSONResponse:
    """Update properties and/or geometry of a single feature by its index."""
    import json

    from shapely.geometry import shape as shapely_shape

    layer_cache: dict = request.app.state.layer_cache
    dataset_repo = request.app.state.dataset_repo

    ds = dataset_repo.get(uuid.UUID(dataset_id))
    if ds is None:
        raise HTTPException(404, f"Dataset {dataset_id} not found")
    if not ds.source_path or not Path(ds.source_path).exists():
        raise HTTPException(404, "Dataset file not found on disk")

    gdfs: dict[str, gpd.GeoDataFrame] = layer_cache.get(dataset_id) or {}
    if layer_name not in gdfs:
        _, gdfs = load_layers(ds.source_path, ds.name)
        layer_cache[dataset_id] = gdfs

    if layer_name not in gdfs:
        raise HTTPException(404, f"Layer {layer_name} not found in dataset {dataset_id}")

    gdf = gdfs[layer_name].copy()
    if fid not in gdf.index:
        raise HTTPException(404, f"Feature {fid} not found in layer {layer_name}")

    if body.properties:
        for col, val in body.properties.items():
            if col == gdf.geometry.name:
                continue
            if col not in gdf.columns:
                gdf[col] = None
            gdf.at[fid, col] = val

    if body.geometry:
        try:
            new_geom = shapely_shape(body.geometry)
        except Exception as exc:
            raise HTTPException(400, f"Invalid GeoJSON geometry: {exc}") from exc
        gdf.at[fid, gdf.geometry.name] = new_geom

    source_path = Path(ds.source_path)
    try:
        ext = source_path.suffix.lower()
        if ext == ".gpkg":
            gdf.to_file(str(source_path), layer=layer_name, driver="GPKG")
        elif ext == ".geojson":
            gdf.to_file(str(source_path), driver="GeoJSON")
        elif ext == ".fgb":
            gdf.to_file(str(source_path), driver="FlatGeobuf")
    except Exception as exc:
        log.warning("Could not persist feature update to disk: %s", exc)

    gdfs[layer_name] = gdf
    layer_cache[dataset_id] = gdfs

    row_gdf = gdf.loc[[fid]]
    if row_gdf.crs and not row_gdf.crs.equals("EPSG:4326"):
        row_gdf = row_gdf.to_crs(epsg=4326)
    row_gdf = sanitize_datetime_columns(row_gdf)
    feature_json = json.loads(row_gdf.to_json())
    return JSONResponse(content={"updated": True, "feature": feature_json["features"][0]})


class FeatureCreateBody(BaseModel):
    type: str = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any] = {}


@router.post("/features/{layer_name}", status_code=201)
@limiter.limit("60/minute")
async def create_feature(
    request: Request,
    layer_name: str,
    body: FeatureCreateBody,
) -> JSONResponse:
    """Create a new feature on a cached layer."""
    from shapely.geometry import shape as shapely_shape

    layer_cache: dict = request.app.state.layer_cache

    dataset_id = None
    gdfs: dict[str, gpd.GeoDataFrame] | None = None
    for ds_id, cached in layer_cache.items():
        if isinstance(cached, dict) and layer_name in cached:
            dataset_id = ds_id
            gdfs = cached
            break

    if dataset_id is None or gdfs is None:
        raise HTTPException(404, f"Layer {layer_name} not found in any cached dataset")

    gdf = gdfs[layer_name].copy()
    try:
        geom = shapely_shape(body.geometry)
    except Exception as exc:
        raise HTTPException(400, f"Invalid GeoJSON geometry: {exc}") from exc

    new_fid = int(gdf.index.max() + 1) if len(gdf) > 0 else 0
    new_row = {**body.properties, gdf.geometry.name: geom}
    gdf.loc[new_fid] = new_row

    dataset_repo = request.app.state.dataset_repo
    ds = dataset_repo.get(uuid.UUID(dataset_id)) if dataset_id else None
    if ds and ds.source_path and Path(ds.source_path).exists():
        source_path = Path(ds.source_path)
        ext = source_path.suffix.lower()
        try:
            if ext == ".gpkg":
                gdf.to_file(str(source_path), layer=layer_name, driver="GPKG")
            elif ext == ".geojson":
                gdf.to_file(str(source_path), driver="GeoJSON")
            elif ext == ".fgb":
                gdf.to_file(str(source_path), driver="FlatGeobuf")
        except Exception as exc:
            log.warning("Could not persist new feature to disk: %s", exc)

    gdfs[layer_name] = gdf
    layer_cache[dataset_id] = gdfs
    return JSONResponse(content={"fid": new_fid, "status": "created"}, status_code=201)


@router.delete("/features/{layer_name}/{fid}")
@limiter.limit("60/minute")
async def delete_feature(
    request: Request,
    layer_name: str,
    fid: int,
) -> JSONResponse:
    """Delete a feature by layer name and feature index."""
    layer_cache: dict = request.app.state.layer_cache

    dataset_id = None
    gdfs: dict[str, gpd.GeoDataFrame] | None = None
    for ds_id, cached in layer_cache.items():
        if isinstance(cached, dict) and layer_name in cached:
            dataset_id = ds_id
            gdfs = cached
            break

    if dataset_id is None or gdfs is None:
        raise HTTPException(404, f"Layer {layer_name} not found in any cached dataset")

    gdf = gdfs[layer_name].copy()
    if fid not in gdf.index:
        raise HTTPException(404, f"Feature {fid} not found in layer {layer_name}")

    gdf = gdf.drop(index=fid).reset_index(drop=True)

    dataset_repo = request.app.state.dataset_repo
    ds = dataset_repo.get(uuid.UUID(dataset_id)) if dataset_id else None
    if ds and ds.source_path and Path(ds.source_path).exists():
        source_path = Path(ds.source_path)
        ext = source_path.suffix.lower()
        try:
            if ext == ".gpkg":
                gdf.to_file(str(source_path), layer=layer_name, driver="GPKG")
            elif ext == ".geojson":
                gdf.to_file(str(source_path), driver="GeoJSON")
            elif ext == ".fgb":
                gdf.to_file(str(source_path), driver="FlatGeobuf")
        except Exception as exc:
            log.warning("Could not persist feature deletion to disk: %s", exc)

    gdfs[layer_name] = gdf
    layer_cache[dataset_id] = gdfs
    return JSONResponse(content={"fid": fid, "status": "deleted"})
