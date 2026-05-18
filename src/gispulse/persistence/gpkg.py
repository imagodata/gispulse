"""
GeoPackage persistence helpers for GISPulse.

Provides read/write helpers over GPKG files via GeoPandas/pyogrio,
and a factory that creates Dataset + Layer domain objects from a GPKG path.
Includes multi-layer import/export and GPKG style table handling.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

import geopandas as gpd
import pyogrio

from gispulse.core.models import Dataset, Layer


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def list_layers(path: str) -> list[str]:
    """Return the list of layer names available in a GeoPackage file.

    Args:
        path: Absolute path to the .gpkg file.

    Returns:
        List of layer name strings.
    """
    info = pyogrio.list_layers(path)
    return [row[0] for row in info]


def read_gpkg(path: str, layer: Optional[str] = None) -> gpd.GeoDataFrame:
    """Read one layer (or the first layer) from a GeoPackage.

    Args:
        path:  Absolute path to the .gpkg file.
        layer: Layer name to read. If None, reads the first available layer.

    Returns:
        GeoDataFrame with the layer contents.
    """
    if layer is None:
        layers = list_layers(path)
        if not layers:
            raise ValueError(f"No layers found in GeoPackage: {path}")
        layer = layers[0]
    return gpd.read_file(path, layer=layer)


def write_gpkg(
    gdf: gpd.GeoDataFrame,
    path: str,
    layer: str,
    mode: str = "w",
) -> None:
    """Write a GeoDataFrame as a layer into a GeoPackage.

    Args:
        gdf:   GeoDataFrame to persist.
        path:  Absolute path to the .gpkg file (created if absent).
        layer: Target layer name inside the GPKG.
        mode:  'w' to overwrite/create, 'a' to append.
    """
    gdf.to_file(path, layer=layer, driver="GPKG", mode=mode)


# ---------------------------------------------------------------------------
# Multi-layer read / write
# ---------------------------------------------------------------------------


def read_all_layers(
    path: str,
    crs: str | None = None,
) -> dict[str, gpd.GeoDataFrame]:
    """Read every layer from a GeoPackage into a name->GeoDataFrame dict."""
    names = list_layers(path)
    result: dict[str, gpd.GeoDataFrame] = {}
    for name in names:
        gdf = gpd.read_file(path, layer=name)
        if crs and gdf.crs is None:
            gdf = gdf.set_crs(crs)
        result[name] = gdf
    return result


def write_all_layers(
    layers: dict[str, gpd.GeoDataFrame],
    path: str,
) -> None:
    """Write multiple layers into a single GeoPackage."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    first = True
    for layer_name, gdf in layers.items():
        mode = "w" if first else "a"
        gdf.to_file(path, layer=layer_name, driver="GPKG", mode=mode)
        first = False


# ---------------------------------------------------------------------------
# GPKG Style table (layer_styles) -- read / write / convert
# ---------------------------------------------------------------------------

_STYLE_COLUMNS = (
    "f_table_catalog",
    "f_table_schema",
    "f_table_name",
    "f_geometry_column",
    "styleName",
    "styleQML",
    "styleSLD",
    "useAsDefault",
    "description",
    "owner",
    "ui",
    "update_time",
)

_CREATE_LAYER_STYLES = """
CREATE TABLE IF NOT EXISTS layer_styles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    f_table_catalog TEXT DEFAULT '',
    f_table_schema TEXT DEFAULT '',
    f_table_name TEXT NOT NULL,
    f_geometry_column TEXT DEFAULT '',
    styleName TEXT DEFAULT '',
    styleQML TEXT,
    styleSLD TEXT,
    useAsDefault BOOLEAN DEFAULT 1,
    description TEXT DEFAULT '',
    owner TEXT DEFAULT '',
    ui TEXT,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def read_styles(path: str) -> list[dict[str, Any]]:
    """Read the layer_styles table from a GeoPackage (if it exists)."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='layer_styles'"
        )
        if cur.fetchone() is None:
            return []
        rows = conn.execute(
            f"SELECT {', '.join(_STYLE_COLUMNS)} FROM layer_styles"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def write_styles(
    path: str,
    styles: list[dict[str, Any]],
    layer_mapping: dict[str, str] | None = None,
) -> None:
    """Write style rows into a GeoPackage's layer_styles table."""
    if not styles:
        return

    conn = sqlite3.connect(path)
    try:
        conn.execute(_CREATE_LAYER_STYLES)
        placeholders = ", ".join("?" for _ in _STYLE_COLUMNS)
        insert_sql = (
            f"INSERT INTO layer_styles ({', '.join(_STYLE_COLUMNS)}) "
            f"VALUES ({placeholders})"
        )

        for style in styles:
            row = dict(style)
            if layer_mapping and row.get("f_table_name") in layer_mapping:
                row["f_table_name"] = layer_mapping[row["f_table_name"]]
            values = tuple(row.get(col) for col in _STYLE_COLUMNS)
            conn.execute(insert_sql, values)
        conn.commit()
    finally:
        conn.close()


def copy_styles(
    src_path: str,
    dst_path: str,
    layer_mapping: dict[str, str] | None = None,
) -> int:
    """Copy styles from source GPKG to destination GPKG."""
    styles = read_styles(src_path)
    if not styles:
        return 0
    dst_layers = set(list_layers(dst_path))
    mapping = layer_mapping or {}
    relevant = [
        s
        for s in styles
        if mapping.get(s["f_table_name"], s["f_table_name"]) in dst_layers
    ]
    write_styles(dst_path, relevant, layer_mapping=mapping)
    return len(relevant)


# ---------------------------------------------------------------------------
# QML / SLD style parsing -- extract color + opacity for UI
# ---------------------------------------------------------------------------

_QML_COLOR_RE = re.compile(
    r'name="color"[^>]*value="(\d+),(\d+),(\d+),(\d+)"'
)
_QML_ALPHA_RE = re.compile(r'alpha="([0-9.]+)"')
_QML_OUTLINE_RE = re.compile(
    r'name="outline_color"[^>]*value="(\d+),(\d+),(\d+),(\d+)"'
)
_QML_OUTLINE_W_RE = re.compile(
    r'name="outline_width"[^>]*value="([0-9.]+)"'
)
_SLD_FILL_RE = re.compile(
    r'<se:SvgParameter\s+name="fill">\s*([#0-9a-fA-F]+)\s*</se:SvgParameter>'
)
_SLD_OPACITY_RE = re.compile(
    r'<se:SvgParameter\s+name="fill-opacity">'
    r"\s*([0-9.]+)\s*</se:SvgParameter>"
)


def parse_style_colors(style: dict[str, Any]) -> dict[str, Any]:
    """Extract color and opacity from a GPKG style row (QML or SLD)."""
    result: dict[str, Any] = {
        "layer_name": style.get("f_table_name", ""),
        "style_name": style.get("styleName", ""),
        "color": None,
        "opacity": None,
        "stroke_color": None,
        "stroke_width": None,
    }

    qml = style.get("styleQML") or ""
    sld = style.get("styleSLD") or ""

    if qml:
        m = _QML_COLOR_RE.search(qml)
        if m:
            r, g, b, a = (
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4)),
            )
            result["color"] = f"#{r:02x}{g:02x}{b:02x}"
            result["opacity"] = round(a / 255, 2)

        ma = _QML_ALPHA_RE.search(qml)
        if ma:
            result["opacity"] = round(float(ma.group(1)), 2)

        mo = _QML_OUTLINE_RE.search(qml)
        if mo:
            ro, go, bo = (
                int(mo.group(1)),
                int(mo.group(2)),
                int(mo.group(3)),
            )
            result["stroke_color"] = f"#{ro:02x}{go:02x}{bo:02x}"

        mw = _QML_OUTLINE_W_RE.search(qml)
        if mw:
            result["stroke_width"] = round(float(mw.group(1)), 2)

    elif sld:
        mf = _SLD_FILL_RE.search(sld)
        if mf:
            result["color"] = mf.group(1)

        mop = _SLD_OPACITY_RE.search(sld)
        if mop:
            result["opacity"] = round(float(mop.group(1)), 2)

    return result


def extract_layer_styles(path: str) -> list[dict[str, Any]]:
    """Extract parsed color/opacity info for all styles in a GPKG."""
    raw_styles = read_styles(path)
    parsed = []
    for s in raw_styles:
        info = parse_style_colors(s)
        if info["color"] is not None:
            parsed.append(info)
    return parsed


def extract_full_style_defs(path: str) -> dict[str, dict[str, Any]]:
    """Extract full LayerStyleDef dicts from GPKG, keyed by layer name.

    Uses the style_converter to parse the complete QML, not just color/opacity.
    Falls back to simple color extraction if QML parsing fails.
    """
    from gispulse.persistence.style_converter import qml_to_style_def

    raw_styles = read_styles(path)
    result: dict[str, dict[str, Any]] = {}

    for s in raw_styles:
        layer_name = s.get("f_table_name", "")
        if not layer_name:
            continue

        qml = s.get("styleQML") or ""
        geom_col = s.get("f_geometry_column", "geom")

        if qml:
            try:
                style_def = qml_to_style_def(qml, geom_type="polygon")
                if style_def.get("renderer"):
                    result[layer_name] = style_def
                    continue
            except Exception:
                pass

        # Fallback to simple extraction
        info = parse_style_colors(s)
        if info["color"]:
            result[layer_name] = {
                "renderer": "single",
                "symbol": {
                    "kind": "fill",
                    "color": info["color"],
                    "opacity": info.get("opacity", 0.7) or 0.7,
                    "strokeColor": info.get("stroke_color") or info["color"],
                    "strokeWidth": info.get("stroke_width") or 1.5,
                },
            }

    return result


# ---------------------------------------------------------------------------
# Domain factory
# ---------------------------------------------------------------------------


def dataset_from_gpkg(path: str) -> Dataset:
    """Create a Dataset domain object (with its Layers) from a GPKG file.

    Inspects every layer in the GPKG to populate Dataset.layers metadata
    and constructs one Layer per GPKG layer.

    Args:
        path: Absolute path to the .gpkg file.

    Returns:
        Dataset with metadata populated. The list of Layer objects is stored
        in dataset.metadata["layers"] as a list of dicts (serialisable).
    """
    layer_names = list_layers(path)

    dataset = Dataset(
        name=path.split("/")[-1].replace(".gpkg", ""),
        source_path=path,
        data_category="vector",
        format="GPKG",
    )

    layers_meta: list[dict] = []
    for lname in layer_names:
        layer_info = pyogrio.read_info(path, layer=lname)
        crs_str = layer_info.get("crs", "EPSG:4326") or "EPSG:4326"
        geom_type = layer_info.get("geometry_type", None)
        feature_count = layer_info.get("features", 0)

        layer_obj = Layer(
            dataset_id=dataset.id,
            name=lname,
            geometry_type=geom_type,
            feature_count=feature_count,
        )
        layers_meta.append(
            {
                "id": str(layer_obj.id),
                "name": layer_obj.name,
                "geometry_type": layer_obj.geometry_type,
                "feature_count": layer_obj.feature_count,
                "crs": crs_str,
            }
        )

    dataset.metadata["layers"] = layers_meta
    dataset.metadata["layer_count"] = len(layer_names)
    return dataset
