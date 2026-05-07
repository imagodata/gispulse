"""
Portal datasets router — listing, metadata, delete, rename, export.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import geopandas as gpd
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from fastapi import File, UploadFile, Form

from gispulse.adapters.http.layer_utils import build_layer_meta, get_layer_styles, load_layers, sanitize_datetime_columns
from gispulse.adapters.http.rate_limit import limiter
from core.config import settings as _cfg
from core.logging import get_logger
from persistence.io import write_vector

log = get_logger(__name__)

router = APIRouter()


@router.get("/datasets")
async def list_datasets(request: Request) -> list[dict]:
    """List all datasets with layer metadata."""
    dataset_repo = request.app.state.dataset_repo
    layer_cache: dict = request.app.state.layer_cache

    result = []
    for ds in dataset_repo.list_all():
        ds_id = str(ds.id)
        layers: list[dict] = []
        if ds.source_path and Path(ds.source_path).exists():
            try:
                if ds_id not in layer_cache:
                    _, gdfs = load_layers(ds.source_path, ds.name)
                    layer_cache[ds_id] = gdfs
                gdfs = layer_cache[ds_id]
                for lname, gdf in gdfs.items():
                    layers.append(build_layer_meta(gdf, lname))
            except Exception as e:
                log.warning("list_datasets_layer_meta_failed", ds=ds_id, error=str(e))

        file_size = 0
        if ds.source_path and Path(ds.source_path).exists():
            file_size = Path(ds.source_path).stat().st_size

        styles = get_layer_styles(ds.source_path) if ds.source_path else []

        result.append({
            "id": ds_id,
            "name": ds.name,
            # Hide server filesystem paths in read-only / public-demo deployments.
            "source_path": None if _cfg.api.read_only else ds.source_path,
            "format": ds.format,
            "crs": ds.crs,
            "file_size": file_size,
            "layers": layers,
            "styles": styles,
            "created_at": ds.created_at.isoformat() if ds.created_at else None,
        })
    return result


@router.get("/datasets/{dataset_id}/styles")
async def get_dataset_styles(request: Request, dataset_id: str) -> JSONResponse:
    """Get parsed layer styles from a GPKG dataset."""
    dataset_repo = request.app.state.dataset_repo
    ds = dataset_repo.get(uuid.UUID(dataset_id))
    if ds is None:
        raise HTTPException(404, f"Dataset {dataset_id} not found")
    styles = get_layer_styles(ds.source_path) if ds.source_path else []
    return JSONResponse(content={"styles": styles})


@router.get("/datasets/{dataset_id}/layers/{layer_name}/features")
async def get_layer_features(
    request: Request,
    dataset_id: str,
    layer_name: str,
    bbox: str | None = Query(None, description="minx,miny,maxx,maxy"),
    limit: int = Query(10000, ge=1, le=100000),
    offset: int = Query(0, ge=0),
    simplify: float | None = Query(None, ge=0),
) -> JSONResponse:
    """Return layer features as GeoJSON FeatureCollection."""
    layer_cache: dict = request.app.state.layer_cache
    dataset_repo = request.app.state.dataset_repo

    gdfs = layer_cache.get(dataset_id)
    if gdfs is None:
        ds = dataset_repo.get(uuid.UUID(dataset_id))
        if ds is None:
            raise HTTPException(404, f"Dataset {dataset_id} not found")
        if not ds.source_path or not Path(ds.source_path).exists():
            raise HTTPException(404, "Dataset file not found on disk")
        _, gdfs = load_layers(ds.source_path, ds.name)
        layer_cache[dataset_id] = gdfs

    if layer_name not in gdfs:
        raise HTTPException(404, f"Layer {layer_name} not found in dataset {dataset_id}")

    gdf = gdfs[layer_name]
    is_spatial = (
        isinstance(gdf, gpd.GeoDataFrame)
        and "geometry" in gdf.columns
        and not gdf.geometry.isna().all()
    )

    if not is_spatial:
        total = len(gdf)
        df_slice = gdf.iloc[offset: offset + limit]
        records = json.loads(df_slice.to_json(orient="records"))
        return JSONResponse(content={"type": "Table", "records": records, "total_count": total})

    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            minx, miny, maxx, maxy = parts
            if gdf.crs and not gdf.crs.equals("EPSG:4326"):
                from pyproj import Transformer
                transformer = Transformer.from_crs("EPSG:4326", gdf.crs, always_xy=True)
                minx, miny = transformer.transform(minx, miny)
                maxx, maxy = transformer.transform(maxx, maxy)
            gdf = gdf.cx[minx:maxx, miny:maxy]
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid bbox. Expected: minx,miny,maxx,maxy")

    total = len(gdf)
    gdf = gdf.iloc[offset: offset + limit]

    if simplify and simplify > 0:
        gdf = gdf.copy()
        original_geom_types = gdf.geometry.geom_type
        gdf["geometry"] = gdf.geometry.simplify(simplify, preserve_topology=True)
        # Drop features whose geometry degenerated to a different type family
        # (e.g. small polygons collapsed to Points by aggressive simplification)
        new_geom_types = gdf.geometry.geom_type
        def _geom_family(t: str) -> str:
            t = t.lower()
            if "polygon" in t: return "polygon"
            if "line" in t: return "line"
            if "point" in t: return "point"
            return t
        mask = original_geom_types.map(_geom_family) == new_geom_types.map(_geom_family)
        gdf = gdf[mask]

    if gdf.crs and not gdf.crs.equals("EPSG:4326"):
        gdf = gdf.to_crs(epsg=4326)

    # Drop features with null/empty geometries that can crash to_json()
    gdf = gdf[~gdf.geometry.isna() & ~gdf.geometry.is_empty]
    # Repair invalid geometries (common in MultiPolygon layers)
    if hasattr(gdf.geometry, "make_valid"):
        gdf["geometry"] = gdf.geometry.make_valid()

    gdf = sanitize_datetime_columns(gdf)

    try:
        geojson = json.loads(gdf.to_json())
    except Exception as exc:
        log.warning("geojson_serialization_failed", layer=layer_name, error=str(exc))
        raise HTTPException(500, f"Failed to serialize layer '{layer_name}' to GeoJSON: {exc}")

    geojson["total_count"] = total
    return JSONResponse(content=geojson)


@router.delete("/datasets/{dataset_id}")
@limiter.limit("30/minute")
async def delete_dataset(request: Request, dataset_id: str) -> JSONResponse:
    """Delete a dataset and its files from disk.

    Shares its body with the public ``/datasets/{id}`` endpoint via
    :func:`gispulse.adapters.http.dataset_ops.delete_dataset` so the two
    URL spaces can never drift on cleanup ordering / FS error handling
    (the duplication that motivated #416).
    """
    from gispulse.adapters.http.dataset_ops import delete_dataset as _delete

    dataset_repo = request.app.state.dataset_repo
    layer_cache: dict = request.app.state.layer_cache

    try:
        ds = _delete(
            dataset_id=uuid.UUID(dataset_id),
            repo=dataset_repo,
            layer_cache=layer_cache,
        )
    except KeyError:
        raise HTTPException(404, f"Dataset {dataset_id} not found")
    return JSONResponse(content={"status": "deleted", "id": dataset_id, "name": ds.name})


class RenameDatasetBody(BaseModel):
    name: str


@router.patch("/datasets/{dataset_id}")
@limiter.limit("30/minute")
async def rename_dataset(
    request: Request, dataset_id: str, body: RenameDatasetBody
) -> JSONResponse:
    """Rename a dataset."""
    dataset_repo = request.app.state.dataset_repo
    ds = dataset_repo.get(uuid.UUID(dataset_id))
    if ds is None:
        raise HTTPException(404, f"Dataset {dataset_id} not found")

    ds.name = body.name
    dataset_repo.save(ds)
    log.info("dataset_renamed", id=dataset_id, new_name=body.name)
    return JSONResponse(content={"status": "renamed", "id": dataset_id, "name": body.name})


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def _write_gpkg_styles(gpkg_path: str, styles: list[tuple[str, str, float, dict | None, str | None]]) -> None:
    """Write a layer_styles table into a GeoPackage for QGIS compatibility.

    Each entry: (layer_name, color, opacity, style_def_or_none, geom_type_or_none)
    If style_def is provided, uses the full style_converter for rich QML.
    """
    from persistence.gpkg_connection import connect_gpkg

    conn = connect_gpkg(gpkg_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS layer_styles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                f_table_catalog TEXT DEFAULT '',
                f_table_schema TEXT DEFAULT '',
                f_table_name TEXT NOT NULL,
                f_geometry_column TEXT DEFAULT 'geom',
                styleName TEXT,
                styleQML TEXT,
                styleSLD TEXT,
                useAsDefault INTEGER DEFAULT 1,
                description TEXT,
                owner TEXT DEFAULT '',
                ui TEXT,
                update_time TEXT DEFAULT (datetime('now'))
            )
        """)
        for layer_name, color, opacity, style_def, geom_type in styles:
            if style_def:
                try:
                    from persistence.style_converter import style_def_to_qml
                    qml = style_def_to_qml(style_def, geom_type or "polygon")
                except Exception:
                    qml = _generate_qml_style(layer_name, color, opacity)
            else:
                qml = _generate_qml_style(layer_name, color, opacity)
            conn.execute(
                "INSERT INTO layer_styles "
                "(f_table_name, styleName, styleQML, useAsDefault, description) "
                "VALUES (?, ?, ?, 1, ?)",
                (layer_name, f"{layer_name}_style", qml, f"Auto-generated style for {layer_name}"),
            )
        conn.commit()
    finally:
        conn.close()


def _generate_qml_style(layer_name: str, color: str, opacity: float) -> str:
    """Generate minimal QGIS-compatible QML style string."""
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    a = int(opacity * 255)

    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34" styleCategories="Symbology">
  <renderer-v2 type="singleSymbol" symbollevels="0" enableorderby="0">
    <symbols>
      <symbol name="0" type="fill" alpha="{opacity}">
        <layer class="SimpleFill" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="{r},{g},{b},{a}"/>
            <Option name="outline_color" type="QString" value="{r},{g},{b},255"/>
            <Option name="outline_width" type="QString" value="0.26"/>
            <Option name="style" type="QString" value="solid"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
</qgis>"""


class ExportGpkgBody(BaseModel):
    layers: list[dict]
    filename: str | None = None


@router.post("/datasets/export-gpkg")
@limiter.limit("10/minute")
async def export_gpkg(request: Request, body: ExportGpkgBody) -> JSONResponse:
    """Export selected layers as a multi-layer GPKG with optional styles."""
    import tempfile

    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    layer_cache: dict = request.app.state.layer_cache
    dataset_repo = request.app.state.dataset_repo

    if not body.layers:
        raise HTTPException(400, "No layers specified")

    # Sanitize filename — only keep basename, strip path traversal
    raw_name = body.filename or "export.gpkg"
    filename = Path(raw_name).name  # strip directory components
    if not filename.endswith(".gpkg"):
        filename += ".gpkg"

    tmp = tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False)
    tmp_path = tmp.name
    tmp.close()

    styles_to_write: list[tuple[str, str, float, dict | None, str | None]] = []
    for i, layer_spec in enumerate(body.layers):
        ds_id = layer_spec.get("datasetId")
        layer_name = layer_spec.get("layerName")
        color = layer_spec.get("color", "#3b82f6")
        opacity = layer_spec.get("opacity", 0.7)
        style_def = layer_spec.get("styleDef")  # advanced style definition (optional)
        geom_type = layer_spec.get("geomType")   # geometry type hint (optional)
        if not ds_id or not layer_name:
            continue
        gdfs = layer_cache.get(ds_id)
        if gdfs is None:
            ds = dataset_repo.get(uuid.UUID(ds_id))
            if ds is None or not ds.source_path:
                continue
            _, gdfs = load_layers(ds.source_path, ds.name)
            layer_cache[ds_id] = gdfs
        if layer_name not in gdfs:
            continue
        gdf = gdfs[layer_name]
        mode = "w" if i == 0 else "a"
        gdf.to_file(tmp_path, layer=layer_name, driver="GPKG", mode=mode)
        styles_to_write.append((layer_name, color, opacity, style_def, geom_type))

    if styles_to_write:
        _write_gpkg_styles(tmp_path, styles_to_write)

    log.info("gpkg_exported", layers=len(styles_to_write), filename=filename)
    return FileResponse(
        path=tmp_path,
        filename=filename,
        media_type="application/geopackage+sqlite3",
        background=BackgroundTask(Path(tmp_path).unlink, missing_ok=True),
    )


class ExportLayersBody(BaseModel):
    layers: list[dict]
    format: str = "gpkg"
    filename: str | None = None


@router.post("/datasets/export")
@limiter.limit("10/minute")
async def export_layers(request: Request, body: ExportLayersBody) -> JSONResponse:
    """Export selected layers in multiple formats."""
    import tempfile

    from fastapi.responses import FileResponse

    layer_cache: dict = request.app.state.layer_cache
    dataset_repo = request.app.state.dataset_repo

    if not body.layers:
        raise HTTPException(400, "No layers specified")

    FORMAT_MAP = {
        "gpkg": (".gpkg", "application/geopackage+sqlite3"),
        "geojson": (".geojson", "application/geo+json"),
        "fgb": (".fgb", "application/octet-stream"),
        "parquet": (".parquet", "application/octet-stream"),
        "shp": (".shp", "application/x-shapefile"),
        "gml": (".gml", "application/gml+xml"),
        "csv": (".csv", "text/csv"),
    }

    fmt = body.format.lower()
    if fmt not in FORMAT_MAP:
        raise HTTPException(400, f"Unsupported format: {fmt}. Supported: {', '.join(FORMAT_MAP)}")

    ext, media_type = FORMAT_MAP[fmt]
    # Sanitize filename — only keep basename, strip path traversal
    raw_name = body.filename or f"export{ext}"
    filename = Path(raw_name).name
    if not filename.endswith(ext):
        filename += ext

    import geopandas as gpd_lib
    import pandas as pd

    gdfs: list[tuple[str, Any]] = []
    for layer_spec in body.layers:
        ds_id = layer_spec.get("datasetId")
        layer_name = layer_spec.get("layerName")
        if not ds_id or not layer_name:
            continue
        cached = layer_cache.get(ds_id)
        if cached is None:
            ds = dataset_repo.get(uuid.UUID(ds_id))
            if ds is None or not ds.source_path:
                continue
            _, cached = load_layers(ds.source_path, ds.name)
            layer_cache[ds_id] = cached
        if layer_name in cached:
            gdfs.append((layer_name, cached[layer_name]))

    if not gdfs:
        raise HTTPException(404, "No layers found to export")

    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp_path = tmp.name
    tmp.close()

    if fmt == "gpkg":
        styles_to_write: list[tuple[str, str, float]] = []
        for i, (lname, gdf) in enumerate(gdfs):
            mode = "w" if i == 0 else "a"
            gdf.to_file(tmp_path, layer=lname, driver="GPKG", mode=mode)
            color = "#3b82f6"
            opacity = 0.7
            for ls in body.layers:
                if ls.get("layerName") == lname:
                    color = ls.get("color", color)
                    opacity = ls.get("opacity", opacity)
                    break
            styles_to_write.append((lname, color, opacity))
        if styles_to_write:
            _write_gpkg_styles(tmp_path, styles_to_write)
    elif len(gdfs) == 1:
        write_vector(gdfs[0][1], tmp_path)
    else:
        combined = gpd_lib.GeoDataFrame(pd.concat([gdf for _, gdf in gdfs], ignore_index=True))
        if combined.crs is None and gdfs[0][1].crs is not None:
            combined.set_crs(gdfs[0][1].crs, inplace=True)
        write_vector(combined, tmp_path)

    log.info("layers_exported", layers=len(gdfs), format=fmt, filename=filename)
    from starlette.background import BackgroundTask
    return FileResponse(
        path=tmp_path, filename=filename, media_type=media_type,
        background=BackgroundTask(Path(tmp_path).unlink, missing_ok=True),
    )


@router.get("/capabilities")
async def get_capabilities() -> list[dict]:
    """List available capabilities with JSON schemas."""
    from capabilities.registry import list_all
    return list_all()


# ── Style classification endpoints ─────────────────────────────────────


@router.get("/datasets/{dataset_id}/layers/{layer_name}/distinct/{field}")
async def get_distinct_values(
    request: Request,
    dataset_id: str,
    layer_name: str,
    field: str,
    limit: int = Query(500, ge=1, le=5000),
) -> JSONResponse:
    """Return distinct values for a field — used by categorized style editor."""
    layer_cache: dict = request.app.state.layer_cache
    dataset_repo = request.app.state.dataset_repo

    gdfs = layer_cache.get(dataset_id)
    if gdfs is None:
        ds = dataset_repo.get(uuid.UUID(dataset_id))
        if ds is None:
            raise HTTPException(404, f"Dataset {dataset_id} not found")
        if not ds.source_path or not Path(ds.source_path).exists():
            raise HTTPException(404, "Dataset file not found on disk")
        _, gdfs = load_layers(ds.source_path, ds.name)
        layer_cache[dataset_id] = gdfs

    if layer_name not in gdfs:
        raise HTTPException(404, f"Layer {layer_name} not found")

    gdf = gdfs[layer_name]
    if field not in gdf.columns:
        raise HTTPException(404, f"Field {field} not found in layer {layer_name}")

    values = gdf[field].dropna().unique()
    # Sort and limit
    try:
        sorted_vals = sorted(values)
    except TypeError:
        sorted_vals = sorted(values, key=str)

    result = [v if not hasattr(v, "item") else v.item() for v in sorted_vals[:limit]]
    return JSONResponse(content={"field": field, "count": len(result), "values": result})


@router.get("/datasets/{dataset_id}/layers/{layer_name}/stats/{field}")
async def get_field_stats(
    request: Request,
    dataset_id: str,
    layer_name: str,
    field: str,
) -> JSONResponse:
    """Return numeric stats for a field — used by graduated style editor."""
    layer_cache: dict = request.app.state.layer_cache
    dataset_repo = request.app.state.dataset_repo

    gdfs = layer_cache.get(dataset_id)
    if gdfs is None:
        ds = dataset_repo.get(uuid.UUID(dataset_id))
        if ds is None:
            raise HTTPException(404, f"Dataset {dataset_id} not found")
        if not ds.source_path or not Path(ds.source_path).exists():
            raise HTTPException(404, "Dataset file not found on disk")
        _, gdfs = load_layers(ds.source_path, ds.name)
        layer_cache[dataset_id] = gdfs

    if layer_name not in gdfs:
        raise HTTPException(404, f"Layer {layer_name} not found")

    gdf = gdfs[layer_name]
    if field not in gdf.columns:
        raise HTTPException(404, f"Field {field} not found in layer {layer_name}")

    series = gdf[field].dropna()

    try:
        numeric = series.astype(float)
    except (ValueError, TypeError):
        raise HTTPException(400, f"Field {field} is not numeric")

    quantiles = [0.1, 0.25, 0.5, 0.75, 0.9]
    q_values = numeric.quantile(quantiles).tolist()

    return JSONResponse(content={
        "field": field,
        "count": int(len(numeric)),
        "min": float(numeric.min()),
        "max": float(numeric.max()),
        "mean": float(numeric.mean()),
        "std": float(numeric.std()),
        "quantiles": {str(q): round(v, 4) for q, v in zip(quantiles, q_values)},
    })


def _load_layer_gdf(request: Request, dataset_id: str, layer_name: str):
    """Resolve dataset+layer to a GeoDataFrame, populating layer_cache. Raises HTTPException."""
    layer_cache: dict = request.app.state.layer_cache
    dataset_repo = request.app.state.dataset_repo

    gdfs = layer_cache.get(dataset_id)
    if gdfs is None:
        ds = dataset_repo.get(uuid.UUID(dataset_id))
        if ds is None:
            raise HTTPException(404, f"Dataset {dataset_id} not found")
        if not ds.source_path or not Path(ds.source_path).exists():
            raise HTTPException(404, "Dataset file not found on disk")
        _, gdfs = load_layers(ds.source_path, ds.name)
        layer_cache[dataset_id] = gdfs
    if layer_name not in gdfs:
        raise HTTPException(404, f"Layer {layer_name} not found")
    return gdfs[layer_name]


_VALID_BREAKS_METHODS = {"quantile", "equal_interval", "jenks", "pretty", "std_dev"}


class BreaksBody(BaseModel):
    field: str
    method: str = "jenks"
    n_classes: int = 5


@router.post("/datasets/{dataset_id}/layers/{layer_name}/breaks")
async def compute_breaks(
    request: Request,
    dataset_id: str,
    layer_name: str,
    body: BreaksBody,
) -> JSONResponse:
    """Compute classification breaks for a numeric field via ClassifyCapability."""
    if body.method not in _VALID_BREAKS_METHODS:
        raise HTTPException(
            400,
            f"method must be one of {sorted(_VALID_BREAKS_METHODS)}, got '{body.method}'",
        )
    if body.n_classes < 2 or body.n_classes > 20:
        raise HTTPException(400, "n_classes must be between 2 and 20")

    gdf = _load_layer_gdf(request, dataset_id, layer_name)
    if body.field not in gdf.columns:
        raise HTTPException(404, f"Field {body.field} not found in layer {layer_name}")

    try:
        gdf[body.field].dropna().astype(float)
    except (ValueError, TypeError):
        raise HTTPException(400, f"Field {body.field} is not numeric")

    from capabilities.classification import ClassifyCapability

    try:
        result = ClassifyCapability().execute(
            gdf,
            field=body.field,
            method=body.method,
            bins=body.n_classes,
            color_col=None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    meta = result.attrs.get("gispulse_style", {})
    breaks = meta.get("breaks") or []
    labels = [
        f"{round(breaks[i], 4)} – {round(breaks[i + 1], 4)}"
        for i in range(len(breaks) - 1)
    ]
    return JSONResponse(content={
        "field": body.field,
        "method": body.method,
        "n_classes": len(breaks) - 1 if len(breaks) > 1 else 0,
        "breaks": [float(b) for b in breaks],
        "labels": labels,
    })


def _upsert_layer_style(gpkg_path: str, layer_name: str, qml_xml: str) -> None:
    """DELETE existing styleQML for layer, then INSERT new one. Idempotent."""
    from persistence.gpkg import _CREATE_LAYER_STYLES
    from persistence.gpkg_connection import connect_gpkg

    conn = connect_gpkg(gpkg_path)
    try:
        conn.execute(_CREATE_LAYER_STYLES)
        conn.execute(
            "DELETE FROM layer_styles WHERE f_table_name = ?",
            (layer_name,),
        )
        conn.execute(
            "INSERT INTO layer_styles (f_table_name, styleName, styleQML, useAsDefault, description) "
            "VALUES (?, ?, ?, 1, ?)",
            (layer_name, f"{layer_name}_style", qml_xml, f"Style for {layer_name}"),
        )
        conn.commit()
    finally:
        conn.close()


class StyleBody(BaseModel):
    layer_name: str
    style_def: dict[str, Any]
    geom_type: str = "polygon"


@router.put("/datasets/{dataset_id}/styles")
async def put_dataset_style(
    request: Request,
    dataset_id: str,
    body: StyleBody,
) -> JSONResponse:
    """Persist a LayerStyleDef into the GPKG layer_styles table (overwrites)."""
    dataset_repo = request.app.state.dataset_repo
    layer_cache: dict = request.app.state.layer_cache

    ds = dataset_repo.get(uuid.UUID(dataset_id))
    if ds is None:
        raise HTTPException(404, f"Dataset {dataset_id} not found")
    if not ds.source_path or not Path(ds.source_path).exists():
        raise HTTPException(404, "Dataset file not found on disk")

    from persistence.style_converter import style_def_to_qml

    try:
        qml = style_def_to_qml(body.style_def, body.geom_type)
    except Exception as exc:
        raise HTTPException(400, f"Invalid style_def: {exc}")

    _upsert_layer_style(ds.source_path, body.layer_name, qml)
    layer_cache.pop(dataset_id, None)

    return JSONResponse(content={
        "layer_name": body.layer_name,
        "qml_size_bytes": len(qml.encode("utf-8")),
    })


_QML_MAX_BYTES = 1_048_576


@router.post("/datasets/{dataset_id}/styles/import")
async def import_qml_style(
    request: Request,
    dataset_id: str,
    layer_name: str = Form(...),
    geom_type: str = Form("polygon"),
    file: UploadFile = File(...),
) -> JSONResponse:
    """Import a .qml file: parse → LayerStyleDef → persist via upsert."""
    dataset_repo = request.app.state.dataset_repo
    layer_cache: dict = request.app.state.layer_cache

    ds = dataset_repo.get(uuid.UUID(dataset_id))
    if ds is None:
        raise HTTPException(404, f"Dataset {dataset_id} not found")
    if not ds.source_path or not Path(ds.source_path).exists():
        raise HTTPException(404, "Dataset file not found on disk")

    raw = await file.read()
    if len(raw) > _QML_MAX_BYTES:
        raise HTTPException(413, "QML file exceeds 1 MB limit")

    try:
        qml_xml = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "QML file must be UTF-8 encoded")

    import xml.etree.ElementTree as _ET

    from persistence.style_converter import qml_to_style_def, style_def_to_qml

    try:
        _ET.fromstring(qml_xml)
    except _ET.ParseError as exc:
        raise HTTPException(400, f"Invalid QML XML: {exc}")

    try:
        style_def = qml_to_style_def(qml_xml, geom_type)
    except Exception as exc:
        raise HTTPException(400, f"Invalid QML: {exc}")

    canonical_qml = style_def_to_qml(style_def, geom_type)
    _upsert_layer_style(ds.source_path, layer_name, canonical_qml)
    layer_cache.pop(dataset_id, None)

    return JSONResponse(content={
        "layer_name": layer_name,
        "style_def": style_def,
        "qml_size_bytes": len(raw),
    })
