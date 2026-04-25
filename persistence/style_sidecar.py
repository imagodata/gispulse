"""
style_sidecar.py — Persist style/legend metadata alongside vector outputs.

Whenever a pipeline step produces ``gdf.attrs["gispulse_style"]`` (classify,
choropleth, categorical, bivariate, head_tail_breaks, continuous_ramp) or
``gdf.attrs["gispulse_legend"]``, this module writes sidecar files next to
the main vector file:

    <stem>.style.qml   — QGIS QML (if the renderer is QML-compatible)
    <stem>.style.sld   — OGC SLD 1.1 (all renderers)
    <stem>.legend.json — legend data (breaks, counts, colors)

The sidecars are best-effort: any failure is logged and swallowed so the
primary vector write remains the source of truth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd

from core.logging import get_logger
from persistence.sld_converter import style_def_to_sld

log = get_logger(__name__)


# Renderers the QML converter understands — keep in sync with
# persistence.style_converter._build_*_renderer
_QML_COMPATIBLE_RENDERERS = {"single", "graduated", "categorized", "rule-based"}


def _infer_geom_type(gdf: gpd.GeoDataFrame) -> str:
    """Best-effort geometry type for sidecar XML generation."""
    if gdf.empty or gdf.geometry.empty:
        return "polygon"
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        t = geom.geom_type.lower()
        if "point" in t:
            return "point"
        if "line" in t:
            return "line"
        if "polygon" in t:
            return "polygon"
    return "polygon"


def write_style_sidecars(
    gdf: gpd.GeoDataFrame,
    vector_path: str | Path,
    layer_name: str | None = None,
) -> dict[str, str]:
    """Write .qml / .sld / .legend.json sidecars next to ``vector_path``.

    Returns a dict ``{kind: path}`` for each sidecar successfully written.
    Returns an empty dict if the GeoDataFrame carries no style metadata.
    """
    style: dict[str, Any] | None = gdf.attrs.get("gispulse_style")
    legend: dict[str, Any] | None = gdf.attrs.get("gispulse_legend")
    if not style and not legend:
        return {}

    vector_path = Path(vector_path)
    stem = vector_path.with_suffix("")
    name = layer_name or stem.name
    geom = _infer_geom_type(gdf)
    written: dict[str, str] = {}

    # Legend JSON — always safe to write whenever it exists.
    if legend:
        legend_path = stem.with_suffix(".legend.json")
        try:
            legend_path.write_text(json.dumps(legend, indent=2, ensure_ascii=False, default=str))
            written["legend"] = str(legend_path)
        except Exception as exc:
            log.warning("style_sidecar_legend_failed", path=str(legend_path), error=str(exc))

    # SLD — supports every renderer we emit (single / graduated / categorized /
    # rule-based). Best-effort for the bespoke "graduated_size", "continuous",
    # and "bivariate" renderers: SLD has no direct equivalent, skip.
    if style and style.get("renderer") in _QML_COMPATIBLE_RENDERERS:
        sld_path = stem.with_suffix(".style.sld")
        try:
            sld_xml = style_def_to_sld(style, geom_type=geom, layer_name=name)
            sld_path.write_text(sld_xml)
            written["sld"] = str(sld_path)
        except Exception as exc:
            log.warning("style_sidecar_sld_failed", path=str(sld_path), error=str(exc))

        # QML — needs the existing bidirectional converter. Imported lazily to
        # keep sidecar writing cheap when style metadata is absent.
        qml_path = stem.with_suffix(".style.qml")
        try:
            from persistence.style_converter import style_def_to_qml

            qml_xml = style_def_to_qml(style, geom_type=geom)
            qml_path.write_text(qml_xml)
            written["qml"] = str(qml_path)
        except Exception as exc:
            log.warning("style_sidecar_qml_failed", path=str(qml_path), error=str(exc))

    return written
