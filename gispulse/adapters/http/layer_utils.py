"""
Shared layer metadata helpers for portal and serve-mode apps.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from persistence.io import list_layers, read_vector, MULTI_LAYER_FORMATS
from persistence.gpkg import extract_layer_styles, extract_full_style_defs


def dtype_to_str(dtype: Any) -> str:
    """Convert a pandas/numpy dtype to a human-readable string."""
    s = str(dtype)
    if "int" in s:
        return "int"
    if "float" in s:
        return "float"
    if "datetime" in s:
        return "datetime"
    if "bool" in s:
        return "bool"
    return "str"


def build_layer_meta(gdf: gpd.GeoDataFrame | pd.DataFrame, name: str) -> dict:
    """Extract metadata from a GeoDataFrame (or plain DataFrame for non-spatial tables)."""
    is_spatial = isinstance(gdf, gpd.GeoDataFrame) and "geometry" in gdf.columns

    bbox = [0.0, 0.0, 0.0, 0.0]
    geom_type = None
    crs_str = None
    geom_col_name = None

    if is_spatial:
        geom_col_name = gdf.geometry.name
        if not gdf.empty:
            try:
                gdf_4326 = gdf.to_crs(epsg=4326) if gdf.crs and not gdf.crs.equals("EPSG:4326") else gdf
                bounds = gdf_4326.total_bounds
                bbox = [float(b) for b in bounds]
                if not all(math.isfinite(v) for v in bbox):
                    bbox = [0.0, 0.0, 0.0, 0.0]
            except Exception:
                bbox = [0.0, 0.0, 0.0, 0.0]
            geom_types = gdf.geometry.geom_type.unique().tolist()
            geom_type = geom_types[0] if geom_types else None

        crs_str = "EPSG:4326"
        if gdf.crs:
            epsg = gdf.crs.to_epsg() if hasattr(gdf.crs, "to_epsg") else None
            crs_str = f"EPSG:{epsg}" if epsg else str(gdf.crs)

    fields = []
    for col in gdf.columns:
        if geom_col_name and col == geom_col_name:
            continue
        fields.append({"name": col, "type": dtype_to_str(gdf[col].dtype)})

    return {
        "name": name,
        "geometry_type": geom_type,
        "feature_count": len(gdf),
        "bbox": bbox,
        "crs": crs_str,
        "fields": fields,
    }


def sanitize_datetime_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Convert datetime columns to ISO strings so gdf.to_json() won't crash."""
    has_datetime = any(
        pd.api.types.is_datetime64_any_dtype(gdf[c])
        for c in gdf.columns if c != "geometry"
    )
    if not has_datetime:
        return gdf
    gdf = gdf.copy()
    for col in gdf.columns:
        if col == "geometry":
            continue
        if pd.api.types.is_datetime64_any_dtype(gdf[col]):
            gdf[col] = gdf[col].astype(str).replace("NaT", None)
    return gdf


def load_layers(file_path: str, name: str) -> tuple[list[dict], dict[str, gpd.GeoDataFrame]]:
    """Load all layers from a spatial file. Returns (layer_metas, layer_gdfs)."""
    path_obj = Path(file_path)
    ext = path_obj.suffix.lower()

    if ext in MULTI_LAYER_FORMATS:
        layer_names = list_layers(file_path)
    else:
        layer_names = [name]

    layers = []
    layer_gdfs: dict[str, gpd.GeoDataFrame] = {}
    for lname in layer_names:
        read_layer = lname if ext in MULTI_LAYER_FORMATS else None
        gdf = read_vector(file_path, layer=read_layer)
        meta = build_layer_meta(gdf, lname)
        layer_gdfs[lname] = gdf
        layers.append(meta)

    return layers, layer_gdfs


def get_layer_styles(file_path: str) -> list[dict]:
    """Extract parsed styles from a GPKG file (or empty list for other formats)."""
    if not file_path or Path(file_path).suffix.lower() != ".gpkg":
        return []
    try:
        return extract_layer_styles(file_path)
    except Exception:
        return []


def get_full_style_defs(file_path: str) -> dict[str, dict]:
    """Extract full LayerStyleDef dicts from a GPKG, keyed by layer name."""
    if not file_path or Path(file_path).suffix.lower() != ".gpkg":
        return {}
    try:
        return extract_full_style_defs(file_path)
    except Exception:
        return {}
