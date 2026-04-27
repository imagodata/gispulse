"""
GISPulse serve-mode FastAPI application factory (Phase 1.5).

Creates a minimal, read-only FastAPI app for the embedded viewer.
Loads a spatial file at startup, caches layer metadata, and serves
the viewer SPA + API endpoints.

Usage::

    from gispulse.adapters.http.serve_app import create_serve_app

    app = create_serve_app("/data/parcels.gpkg")
"""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from gispulse.adapters.http.routers.viewer_router import router as viewer_router
from gispulse.adapters.http.layer_utils import dtype_to_str as _dtype_to_str
from gispulse.adapters.http.schemas import LayerFieldInfo
from core.logging import get_logger
from persistence.io import list_layers, read_vector, MULTI_LAYER_FORMATS

log = get_logger(__name__)

from gispulse import __version__ as _VERSION

# Path to the built viewer SPA (relative to project root)
_VIEWER_DIST = Path(__file__).resolve().parent.parent.parent / "viewer" / "dist"




def _build_layer_cache(file_path: str) -> dict[str, dict]:
    """Load all layers from a spatial file and build the metadata cache.

    Returns a dict mapping layer name -> metadata dict containing:
    - gdf: the loaded GeoDataFrame
    - geometry_type, feature_count, bbox, crs, fields
    """
    path_obj = Path(file_path)
    ext = path_obj.suffix.lower()

    # Determine layer names
    if ext in MULTI_LAYER_FORMATS:
        layer_names = list_layers(file_path)
    else:
        layer_names = [path_obj.stem]

    cache: dict[str, dict] = {}

    for lname in layer_names:
        # For multi-layer formats, pass the layer name
        read_layer = lname if ext in MULTI_LAYER_FORMATS else None
        gdf = read_vector(file_path, layer=read_layer)

        # Compute metadata — bbox always in WGS84 for MapLibre compatibility
        bbox = [0.0, 0.0, 0.0, 0.0]
        if not gdf.empty and gdf.geometry is not None:
            gdf_4326 = gdf.to_crs(epsg=4326) if gdf.crs and not gdf.crs.equals("EPSG:4326") else gdf
            bounds = gdf_4326.total_bounds  # [minx, miny, maxx, maxy]
            bbox = [float(b) for b in bounds]
            if any(not math.isfinite(v) for v in bbox):
                bbox = [0.0, 0.0, 0.0, 0.0]

        geom_types = gdf.geometry.geom_type.unique().tolist() if not gdf.empty else []
        geom_type = geom_types[0] if geom_types else None

        crs_str = str(gdf.crs) if gdf.crs else "EPSG:4326"
        # Try to get EPSG code
        if gdf.crs and hasattr(gdf.crs, "to_epsg") and gdf.crs.to_epsg():
            crs_str = f"EPSG:{gdf.crs.to_epsg()}"

        # Build field info (exclude geometry column)
        fields = []
        for col in gdf.columns:
            if col == gdf.geometry.name:
                continue
            fields.append(
                LayerFieldInfo(name=col, type=_dtype_to_str(gdf[col].dtype))
            )

        cache[lname] = {
            "gdf": gdf,
            "geometry_type": geom_type,
            "feature_count": len(gdf),
            "bbox": bbox,
            "crs": crs_str,
            "fields": fields,
        }
        log.info(
            "layer_cached",
            layer=lname,
            features=len(gdf),
            geom_type=geom_type,
        )

    return cache


def create_serve_app(
    file_path: str,
    static_dir: Path | None = None,
) -> FastAPI:
    """Create the viewer-mode FastAPI application.

    Args:
        file_path: Path to the spatial file to serve.
        static_dir: Optional path to the built SPA directory.
                    Defaults to ``viewer/dist/`` relative to project root.

    Returns:
        Configured FastAPI app with viewer endpoints and optional static serving.
    """
    file_path_resolved = str(Path(file_path).resolve())
    dist = static_dir or _VIEWER_DIST

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: load and cache layers
        log.info("serve_startup", file=file_path_resolved)
        layer_cache = _build_layer_cache(file_path_resolved)

        # Attach GPKG styles to layer cache entries
        if file_path_resolved.endswith(".gpkg"):
            from persistence.gpkg import extract_layer_styles

            styles = extract_layer_styles(file_path_resolved)
            style_map: dict[str, dict] = {}
            for s in styles:
                lname = s.get("layer_name", "")
                if lname not in style_map:
                    style_map[lname] = s
            for lname, meta in layer_cache.items():
                meta["style"] = style_map.get(lname)

        app.state.viewer_state = {
            "file_path": file_path_resolved,
            "layer_cache": layer_cache,
        }
        layer_count = len(layer_cache)
        log.info("serve_ready", layers=layer_count)
        yield
        # Shutdown
        log.info("serve_shutdown")

    app = FastAPI(
        title="GISPulse Viewer",
        version=_VERSION,
        description="Embedded spatial data viewer (read-only).",
        lifespan=lifespan,
    )

    # CORS for dev mode (Vite on different port)
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # Health endpoint
    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "version": _VERSION, "mode": "viewer"}

    # Viewer API
    app.include_router(viewer_router)

    # Static SPA (only if built)
    if dist.exists() and dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="viewer")
        log.info("static_mounted", directory=str(dist))

    return app
