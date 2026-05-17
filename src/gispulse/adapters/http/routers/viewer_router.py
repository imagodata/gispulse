"""
Viewer router for GISPulse Phase 1.5.

Read-only endpoints to serve spatial layers as GeoJSON for the embedded
deck.gl viewer. These endpoints form the API contract reused in Phase 2.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from gispulse.adapters.http.dependencies import get_viewer_state
from gispulse.adapters.http.schemas import (
    FeatureCreate,
    FeatureUpdate,
    LayerDetailResponse,
    LayerListResponse,
    LayerStyleInfo,
    ViewerLayerSummary,
)

router = APIRouter(prefix="/v1/viewer", tags=["viewer"])


def _get_layer_or_404(state: dict, name: str) -> dict:
    """Lookup a layer in the cache or raise 404."""
    cache = state["layer_cache"]
    if name not in cache:
        raise HTTPException(status_code=404, detail=f"Layer '{name}' not found.")
    return cache[name]


@router.get(
    "/layers",
    response_model=LayerListResponse,
    summary="List all layers",
)
def list_layers(state: dict = Depends(get_viewer_state)) -> LayerListResponse:
    """Return metadata for all layers in the loaded file."""
    summaries = []
    for name, meta in state["layer_cache"].items():
        style_data = meta.get("style")
        style = LayerStyleInfo(**style_data) if style_data else None
        summaries.append(
            ViewerLayerSummary(
                name=name,
                geometry_type=meta["geometry_type"],
                feature_count=meta["feature_count"],
                bbox=meta["bbox"],
                crs=meta["crs"],
                style=style,
            )
        )
    return LayerListResponse(file=state["file_path"], layers=summaries)


@router.get(
    "/layers/{name}",
    response_model=LayerDetailResponse,
    summary="Layer detail",
)
def get_layer(name: str, state: dict = Depends(get_viewer_state)) -> LayerDetailResponse:
    """Return detailed metadata for a single layer, including field schema."""
    meta = _get_layer_or_404(state, name)
    return LayerDetailResponse(
        name=name,
        geometry_type=meta["geometry_type"],
        feature_count=meta["feature_count"],
        bbox=meta["bbox"],
        crs=meta["crs"],
        fields=meta["fields"],
    )


@router.get(
    "/layers/{name}/features",
    summary="Layer features as GeoJSON",
)
def get_features(
    name: str,
    bbox: str | None = Query(None, description="Viewport filter: minx,miny,maxx,maxy"),
    limit: int = Query(10000, ge=1, le=100000, description="Max features to return."),
    offset: int = Query(0, ge=0, description="Skip first N features."),
    simplify: float | None = Query(None, ge=0, description="Simplification tolerance in CRS units."),
    state: dict = Depends(get_viewer_state),
) -> JSONResponse:
    """Return features as a GeoJSON FeatureCollection.

    Supports viewport-based filtering (bbox), pagination (limit/offset),
    and geometry simplification for large datasets.
    """

    meta = _get_layer_or_404(state, name)
    gdf: gpd.GeoDataFrame = meta["gdf"]

    # Bbox filter
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            minx, miny, maxx, maxy = parts
            gdf = gdf.cx[minx:maxx, miny:maxy]
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail="Invalid bbox format. Expected: minx,miny,maxx,maxy",
            )

    total = len(gdf)

    # Pagination
    gdf = gdf.iloc[offset : offset + limit]

    # Simplify geometries
    if simplify and simplify > 0:
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.simplify(simplify, preserve_topology=True)

    # Convert to GeoJSON dict
    geojson = _gdf_to_geojson(gdf)
    geojson["total_count"] = total

    return JSONResponse(content=geojson)


@router.get(
    "/layers/{name}/bbox",
    summary="Layer bounding box",
)
def get_bbox(
    name: str,
    state: dict = Depends(get_viewer_state),
) -> dict[str, list[float]]:
    """Return the bounding box for a layer."""
    meta = _get_layer_or_404(state, name)
    return {"bbox": meta["bbox"]}


@router.get(
    "/styles",
    summary="Layer styles from GPKG",
)
def get_styles(
    state: dict = Depends(get_viewer_state),
) -> JSONResponse:
    """Return parsed styles (color, opacity, stroke) for all layers.

    Reads the ``layer_styles`` table from GPKG files and returns
    extracted color/opacity info for UI rendering.  Returns an empty
    list for non-GPKG files.
    """
    file_path = state.get("file_path", "")
    if not file_path or not file_path.endswith(".gpkg"):
        return JSONResponse(content={"styles": []})

    from gispulse.persistence.gpkg import extract_layer_styles

    styles = extract_layer_styles(file_path)
    return JSONResponse(content={"styles": styles})


@router.get(
    "/layers/{name}/style",
    summary="Style for a specific layer",
)
def get_layer_style(
    name: str,
    state: dict = Depends(get_viewer_state),
) -> JSONResponse:
    """Return parsed style for a single layer (from GPKG layer_styles table)."""
    _get_layer_or_404(state, name)

    file_path = state.get("file_path", "")
    if not file_path or not file_path.endswith(".gpkg"):
        return JSONResponse(content={"style": None})

    from gispulse.persistence.gpkg import extract_layer_styles

    styles = extract_layer_styles(file_path)
    for s in styles:
        if s.get("layer_name") == name:
            return JSONResponse(content={"style": s})

    return JSONResponse(content={"style": None})


def _gdf_to_geojson(gdf: Any) -> dict:
    """Convert a GeoDataFrame to a GeoJSON dict efficiently."""
    import json
    from gispulse.adapters.http.layer_utils import sanitize_datetime_columns

    gdf = sanitize_datetime_columns(gdf)
    return json.loads(gdf.to_json())


def _flush_layer(state: dict, name: str) -> None:
    """Write the cached GeoDataFrame back to the source file."""
    from gispulse.persistence.io import write_vector

    meta = state["layer_cache"][name]
    gdf = meta["gdf"]
    file_path = state["file_path"]
    write_vector(gdf, file_path, layer=name)


def _update_layer_meta(meta: dict) -> None:
    """Refresh feature_count and bbox from the cached GeoDataFrame."""
    gdf = meta["gdf"]
    meta["feature_count"] = len(gdf)
    if not gdf.empty and gdf.geometry is not None:
        import math
        gdf_4326 = gdf.to_crs(epsg=4326) if gdf.crs and not gdf.crs.equals("EPSG:4326") else gdf
        bounds = gdf_4326.total_bounds
        bbox = [float(b) for b in bounds]
        meta["bbox"] = bbox if all(math.isfinite(v) for v in bbox) else [0.0, 0.0, 0.0, 0.0]
    else:
        meta["bbox"] = [0.0, 0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# Feature editing endpoints (Phase 2)
# ---------------------------------------------------------------------------


@router.post(
    "/layers/{name}/features",
    status_code=201,
    summary="Add a feature",
)
def create_feature(
    name: str,
    feature: FeatureCreate,
    state: dict = Depends(get_viewer_state),
) -> JSONResponse:
    """Add a new feature (GeoJSON) to a layer.

    The feature is appended to the in-memory GeoDataFrame and flushed
    to the source file.
    """
    import geopandas as gpd
    from shapely.geometry import shape

    meta = _get_layer_or_404(state, name)
    gdf: gpd.GeoDataFrame = meta["gdf"]

    geom = shape(feature.geometry)
    props = dict(feature.properties)

    # Build a single-row GeoDataFrame and append
    new_row = gpd.GeoDataFrame([props], geometry=[geom], crs=gdf.crs)
    # Ensure columns match
    for col in gdf.columns:
        if col not in new_row.columns and col != gdf.geometry.name:
            new_row[col] = None

    meta["gdf"] = gpd.GeoDataFrame(
        __import__("pandas").concat([gdf, new_row], ignore_index=True),
        geometry=gdf.geometry.name,
        crs=gdf.crs,
    )
    _update_layer_meta(meta)
    _flush_layer(state, name)

    fid = len(meta["gdf"]) - 1
    return JSONResponse(content={"fid": fid, "status": "created"}, status_code=201)


@router.put(
    "/layers/{name}/features/{fid}",
    summary="Update a feature",
)
def update_feature(
    name: str,
    fid: int,
    update: FeatureUpdate,
    state: dict = Depends(get_viewer_state),
) -> JSONResponse:
    """Update geometry and/or properties of an existing feature by index."""
    from shapely.geometry import shape

    meta = _get_layer_or_404(state, name)
    gdf: gpd.GeoDataFrame = meta["gdf"]

    if fid < 0 or fid >= len(gdf):
        raise HTTPException(status_code=404, detail=f"Feature {fid} not found.")

    if update.geometry is not None:
        gdf.loc[fid, gdf.geometry.name] = shape(update.geometry)

    if update.properties is not None:
        for key, value in update.properties.items():
            if key != gdf.geometry.name:
                gdf.loc[fid, key] = value

    _update_layer_meta(meta)
    _flush_layer(state, name)

    return JSONResponse(content={"fid": fid, "status": "updated"})


@router.delete(
    "/layers/{name}/features/{fid}",
    status_code=200,
    summary="Delete a feature",
)
def delete_feature(
    name: str,
    fid: int,
    state: dict = Depends(get_viewer_state),
) -> JSONResponse:
    """Delete a feature by index from a layer."""
    import geopandas as gpd

    meta = _get_layer_or_404(state, name)
    gdf: gpd.GeoDataFrame = meta["gdf"]

    if fid < 0 or fid >= len(gdf):
        raise HTTPException(status_code=404, detail=f"Feature {fid} not found.")

    meta["gdf"] = gpd.GeoDataFrame(
        gdf.drop(index=fid).reset_index(drop=True),
        geometry=gdf.geometry.name,
        crs=gdf.crs,
    )
    _update_layer_meta(meta)
    _flush_layer(state, name)

    return JSONResponse(content={"fid": fid, "status": "deleted"})
