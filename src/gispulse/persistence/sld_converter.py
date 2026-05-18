"""
sld_converter.py — GISPulse LayerStyleDef → OGC Styled Layer Descriptor (SLD 1.1).

Complements :mod:`persistence.style_converter` (QGIS QML) with the OGC standard
consumed by GeoServer, MapServer, QGIS Server, and most WMS clients.

Primary direction is **export** (LayerStyleDef → SLD XML) so a choropleth
emitted by :class:`capabilities.classification.ClassifyCapability` (or the
upcoming ChoroplethCapability) can be published as a standard OGC style.

Import (SLD → LayerStyleDef) covers single/graduated/categorized at minimum so
styles published via GeoServer can be re-ingested.

Supported renderers:
  - single
  - graduated   (N rules with ogc:PropertyIsBetween)
  - categorized (N rules with ogc:PropertyIsEqualTo)
  - rule-based  (passthrough where Filter is an OGC expression)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any


# ── OGC namespaces ───────────────────────────────────────────────────────
SLD_NS = "http://www.opengis.net/sld"
SE_NS = "http://www.opengis.net/se"
OGC_NS = "http://www.opengis.net/ogc"
XLINK_NS = "http://www.w3.org/1999/xlink"

# Register prefixes so ElementTree emits se:/ogc:/xlink: rather than ns0:/ns1:
ET.register_namespace("sld", SLD_NS)
ET.register_namespace("se", SE_NS)
ET.register_namespace("ogc", OGC_NS)
ET.register_namespace("xlink", XLINK_NS)


def _qname(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


# ── Export: style_def → SLD ──────────────────────────────────────────────


def style_def_to_sld(
    style: dict[str, Any],
    geom_type: str = "polygon",
    layer_name: str = "layer",
    style_name: str | None = None,
) -> str:
    """Convert a GISPulse LayerStyleDef dict into an SLD 1.1 XML string.

    Args:
        style: LayerStyleDef (as produced by :mod:`persistence.style_converter`
            or emitted on ``gdf.attrs["gispulse_style"]`` by classify/choropleth).
        geom_type: ``"point" | "line" | "polygon"``.
        layer_name: Name of the ``sld:NamedLayer`` (should match the layer name
            as served by WMS/WFS).
        style_name: Human name of the ``sld:UserStyle`` (defaults to layer_name).

    Returns:
        UTF-8 encoded XML string (with ``<?xml …?>`` prolog).
    """
    geom = _normalize_geom(geom_type)
    name = style_name or layer_name

    root = ET.Element(_qname(SLD_NS, "StyledLayerDescriptor"), {
        "version": "1.1.0",
    })

    named_layer = ET.SubElement(root, _qname(SLD_NS, "NamedLayer"))
    _text(named_layer, _qname(SE_NS, "Name"), layer_name)

    user_style = ET.SubElement(named_layer, _qname(SLD_NS, "UserStyle"))
    _text(user_style, _qname(SE_NS, "Name"), name)

    fts = ET.SubElement(user_style, _qname(SE_NS, "FeatureTypeStyle"))

    renderer = style.get("renderer", "single")
    if renderer == "graduated":
        _emit_graduated_rules(fts, style, geom)
    elif renderer == "categorized":
        _emit_categorized_rules(fts, style, geom)
    elif renderer == "rule-based":
        _emit_rule_based_rules(fts, style, geom)
    else:
        _emit_single_rule(fts, style, geom)

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return xml_bytes.decode("utf-8")


def _emit_single_rule(fts: ET.Element, style: dict[str, Any], geom: str) -> None:
    rule = ET.SubElement(fts, _qname(SE_NS, "Rule"))
    _text(rule, _qname(SE_NS, "Name"), "Single")
    symbol = style.get("symbol") or _default_symbol(geom)
    _emit_symbolizer(rule, symbol, geom)


def _emit_graduated_rules(fts: ET.Element, style: dict[str, Any], geom: str) -> None:
    """Emit one Rule per class with an ogc:PropertyIsBetween filter."""
    field = style.get("graduatedField") or style.get("classField") or style.get("field")
    classes = style.get("classes") or _classes_from_ranges(style, geom)
    if not field or not classes:
        _emit_single_rule(fts, style, geom)
        return
    for cls in classes:
        rule = ET.SubElement(fts, _qname(SE_NS, "Rule"))
        lower = cls.get("lower", cls.get("min"))
        upper = cls.get("upper", cls.get("max"))
        label = cls.get("label", f"{lower} – {upper}")
        _text(rule, _qname(SE_NS, "Name"), str(label))
        _nested_text(rule, [_qname(SE_NS, "Description"), _qname(SE_NS, "Title")], str(label))
        # Build <ogc:Filter><ogc:PropertyIsBetween>
        flt = ET.SubElement(rule, _qname(OGC_NS, "Filter"))
        between = ET.SubElement(flt, _qname(OGC_NS, "PropertyIsBetween"))
        _text(between, _qname(OGC_NS, "PropertyName"), field)
        _nested_text(between, [_qname(OGC_NS, "LowerBoundary"), _qname(OGC_NS, "Literal")], _fmt(lower))
        _nested_text(between, [_qname(OGC_NS, "UpperBoundary"), _qname(OGC_NS, "Literal")], _fmt(upper))

        symbol = cls.get("symbol") or {"kind": _kind_for_geom(geom), "color": cls.get("color", "#3b82f6")}
        # If only a color was supplied (classify output), synthesize a default symbol
        if "kind" not in symbol:
            symbol = {**_default_symbol(geom), "color": symbol.get("color", "#3b82f6")}
        _emit_symbolizer(rule, symbol, geom)


def _emit_categorized_rules(fts: ET.Element, style: dict[str, Any], geom: str) -> None:
    field = style.get("classField") or style.get("field")
    categories = style.get("categories") or []
    if not field or not categories:
        _emit_single_rule(fts, style, geom)
        return
    for cat in categories:
        rule = ET.SubElement(fts, _qname(SE_NS, "Rule"))
        value = cat.get("value")
        label = cat.get("label", str(value) if value is not None else "Other")
        _text(rule, _qname(SE_NS, "Name"), str(label))
        _nested_text(rule, [_qname(SE_NS, "Description"), _qname(SE_NS, "Title")], str(label))
        if value is not None:
            flt = ET.SubElement(rule, _qname(OGC_NS, "Filter"))
            eq = ET.SubElement(flt, _qname(OGC_NS, "PropertyIsEqualTo"))
            _text(eq, _qname(OGC_NS, "PropertyName"), field)
            _text(eq, _qname(OGC_NS, "Literal"), _fmt(value))
        else:
            # "all other values" bucket: use ElseFilter
            ET.SubElement(rule, _qname(SE_NS, "ElseFilter"))
        symbol = cat.get("symbol") or _default_symbol(geom)
        if "kind" not in symbol:
            symbol = {**_default_symbol(geom), "color": symbol.get("color", "#3b82f6")}
        _emit_symbolizer(rule, symbol, geom)


def _emit_rule_based_rules(fts: ET.Element, style: dict[str, Any], geom: str) -> None:
    rules = style.get("rules") or []
    if not rules:
        _emit_single_rule(fts, style, geom)
        return
    for r in rules:
        rule = ET.SubElement(fts, _qname(SE_NS, "Rule"))
        name = r.get("name", "Rule")
        _text(rule, _qname(SE_NS, "Name"), str(name))
        # Passthrough of a filter expression if provided (best-effort OGC)
        filter_expr = r.get("filter", "")
        if filter_expr:
            flt = ET.SubElement(rule, _qname(OGC_NS, "Filter"))
            # Can't safely compile arbitrary QGIS expressions — wrap as
            # <ogc:PropertyIsEqualTo> on a sentinel so SLD stays valid.
            # Downstream consumers should edit this rule in a WYSIWYG tool.
            pl = ET.SubElement(flt, _qname(OGC_NS, "PropertyIsEqualTo"))
            _text(pl, _qname(OGC_NS, "Literal"), "1")
            _text(pl, _qname(OGC_NS, "Literal"), "1")
            flt.set("comment", filter_expr)
        symbol = r.get("symbol") or _default_symbol(geom)
        if "kind" not in symbol:
            symbol = {**_default_symbol(geom), "color": symbol.get("color", "#3b82f6")}
        _emit_symbolizer(rule, symbol, geom)


# ── Symbolizers ──────────────────────────────────────────────────────────


def _emit_symbolizer(rule: ET.Element, symbol: dict[str, Any], geom: str) -> None:
    kind = symbol.get("kind") or _kind_for_geom(geom)
    if kind == "point" or geom == "point":
        _emit_point_symbolizer(rule, symbol)
    elif kind == "line" or geom == "line":
        _emit_line_symbolizer(rule, symbol)
    else:
        _emit_polygon_symbolizer(rule, symbol)


def _emit_polygon_symbolizer(rule: ET.Element, s: dict[str, Any]) -> None:
    ps = ET.SubElement(rule, _qname(SE_NS, "PolygonSymbolizer"))
    fill = ET.SubElement(ps, _qname(SE_NS, "Fill"))
    _svg_param(fill, "fill", s.get("color", "#3b82f6"))
    if "opacity" in s:
        _svg_param(fill, "fill-opacity", _fmt(s["opacity"]))
    stroke = ET.SubElement(ps, _qname(SE_NS, "Stroke"))
    _svg_param(stroke, "stroke", s.get("strokeColor", s.get("color", "#3b82f6")))
    _svg_param(stroke, "stroke-width", _fmt(s.get("strokeWidth", 1)))
    if "strokeOpacity" in s:
        _svg_param(stroke, "stroke-opacity", _fmt(s["strokeOpacity"]))


def _emit_line_symbolizer(rule: ET.Element, s: dict[str, Any]) -> None:
    ls = ET.SubElement(rule, _qname(SE_NS, "LineSymbolizer"))
    stroke = ET.SubElement(ls, _qname(SE_NS, "Stroke"))
    _svg_param(stroke, "stroke", s.get("color", "#3b82f6"))
    _svg_param(stroke, "stroke-width", _fmt(s.get("width", 1)))
    if "opacity" in s:
        _svg_param(stroke, "stroke-opacity", _fmt(s["opacity"]))
    if s.get("cap"):
        _svg_param(stroke, "stroke-linecap", s["cap"])
    if s.get("join"):
        _svg_param(stroke, "stroke-linejoin", s["join"])
    if s.get("dashPattern"):
        _svg_param(stroke, "stroke-dasharray", " ".join(_fmt(d) for d in s["dashPattern"]))


def _emit_point_symbolizer(rule: ET.Element, s: dict[str, Any]) -> None:
    pts = ET.SubElement(rule, _qname(SE_NS, "PointSymbolizer"))
    graphic = ET.SubElement(pts, _qname(SE_NS, "Graphic"))
    mark = ET.SubElement(graphic, _qname(SE_NS, "Mark"))
    shape_map = {
        "circle": "circle",
        "square": "square",
        "triangle": "triangle",
        "star": "star",
        "cross": "cross",
        "diamond": "x",  # SE 1.1 well-known "x" is the closest standard
    }
    _text(mark, _qname(SE_NS, "WellKnownName"), shape_map.get(s.get("shape", "circle"), "circle"))
    fill = ET.SubElement(mark, _qname(SE_NS, "Fill"))
    _svg_param(fill, "fill", s.get("color", "#3b82f6"))
    if "opacity" in s:
        _svg_param(fill, "fill-opacity", _fmt(s["opacity"]))
    stroke = ET.SubElement(mark, _qname(SE_NS, "Stroke"))
    _svg_param(stroke, "stroke", s.get("strokeColor", "#ffffff"))
    _svg_param(stroke, "stroke-width", _fmt(s.get("strokeWidth", 1)))
    _text(graphic, _qname(SE_NS, "Size"), _fmt(s.get("size", 5)))
    if s.get("rotation"):
        _text(graphic, _qname(SE_NS, "Rotation"), _fmt(s["rotation"]))


# ── Import: SLD → style_def (single / graduated / categorized) ───────────


def sld_to_style_def(sld_xml: str, geom_type: str = "polygon") -> dict[str, Any]:
    """Parse an SLD 1.x XML document back into a GISPulse LayerStyleDef dict.

    Best-effort: covers single / graduated (PropertyIsBetween) / categorized
    (PropertyIsEqualTo). Unknown shapes fall back to ``{"renderer": "single"}``.
    """
    try:
        root = ET.fromstring(sld_xml)
    except ET.ParseError:
        return {"renderer": "single"}

    geom = _normalize_geom(geom_type)
    rules = _find_all(root, "Rule")
    if not rules:
        return {"renderer": "single"}

    # Classify rules by filter shape
    graduated: list[dict[str, Any]] = []
    categorized: list[dict[str, Any]] = []
    plain: list[dict[str, Any]] = []
    field_graduated: str | None = None
    field_categorized: str | None = None

    for rule in rules:
        label = _text_of(_find_one(rule, "Name")) or _text_of(_find_one(rule, "Title")) or ""
        flt = _find_one(rule, "Filter")
        symbol = _parse_symbolizer(rule, geom)

        if flt is not None:
            between = _find_one(flt, "PropertyIsBetween")
            eq = _find_one(flt, "PropertyIsEqualTo")
            if between is not None:
                fld = _text_of(_find_one(between, "PropertyName"))
                lower = _text_of(_find_first_literal(_find_one(between, "LowerBoundary")))
                upper = _text_of(_find_first_literal(_find_one(between, "UpperBoundary")))
                try:
                    lo_v = float(lower) if lower is not None else 0.0
                    up_v = float(upper) if upper is not None else 0.0
                except (TypeError, ValueError):
                    lo_v, up_v = 0.0, 0.0
                graduated.append({
                    "lower": lo_v,
                    "upper": up_v,
                    "label": label or f"{lo_v} – {up_v}",
                    "symbol": symbol,
                })
                if fld:
                    field_graduated = fld
                continue
            if eq is not None:
                fld = _text_of(_find_one(eq, "PropertyName"))
                literals = _find_all(eq, "Literal")
                val_txt = _text_of(literals[0]) if literals else None
                val: Any = val_txt
                if val_txt is not None:
                    try:
                        val = float(val_txt) if "." in val_txt else int(val_txt)
                    except (TypeError, ValueError):
                        val = val_txt
                categorized.append({
                    "value": val,
                    "label": label or str(val),
                    "symbol": symbol,
                })
                if fld:
                    field_categorized = fld
                continue
        # ElseFilter → "all other values" bucket in categorized
        else_filter = _find_one(rule, "ElseFilter")
        if else_filter is not None:
            categorized.append({
                "value": None,
                "label": label or "Other",
                "symbol": symbol,
            })
            continue

        plain.append({"label": label, "symbol": symbol})

    if graduated and field_graduated:
        return {
            "renderer": "graduated",
            "graduatedField": field_graduated,
            "classes": graduated,
        }
    if categorized and field_categorized:
        return {
            "renderer": "categorized",
            "classField": field_categorized,
            "categories": categorized,
        }
    if plain:
        return {"renderer": "single", "symbol": plain[0]["symbol"]}
    return {"renderer": "single"}


def _parse_symbolizer(rule: ET.Element, geom: str) -> dict[str, Any]:
    poly = _find_one(rule, "PolygonSymbolizer")
    line = _find_one(rule, "LineSymbolizer")
    point = _find_one(rule, "PointSymbolizer")
    if poly is not None:
        return _parse_polygon_sym(poly)
    if line is not None:
        return _parse_line_sym(line)
    if point is not None:
        return _parse_point_sym(point)
    return _default_symbol(geom)


def _parse_polygon_sym(el: ET.Element) -> dict[str, Any]:
    fill = _find_one(el, "Fill")
    stroke = _find_one(el, "Stroke")
    return {
        "kind": "fill",
        "color": _svg_get(fill, "fill") or "#3b82f6",
        "opacity": _to_float(_svg_get(fill, "fill-opacity"), 1.0),
        "strokeColor": _svg_get(stroke, "stroke") or "#3b82f6",
        "strokeWidth": _to_float(_svg_get(stroke, "stroke-width"), 1.0),
    }


def _parse_line_sym(el: ET.Element) -> dict[str, Any]:
    stroke = _find_one(el, "Stroke")
    return {
        "kind": "line",
        "color": _svg_get(stroke, "stroke") or "#3b82f6",
        "width": _to_float(_svg_get(stroke, "stroke-width"), 1.0),
        "opacity": _to_float(_svg_get(stroke, "stroke-opacity"), 1.0),
    }


def _parse_point_sym(el: ET.Element) -> dict[str, Any]:
    graphic = _find_one(el, "Graphic")
    if graphic is None:
        return _default_symbol("point")
    mark = _find_one(graphic, "Mark")
    fill = _find_one(mark, "Fill") if mark is not None else None
    stroke = _find_one(mark, "Stroke") if mark is not None else None
    wkn = _text_of(_find_one(mark, "WellKnownName")) if mark is not None else "circle"
    size = _to_float(_text_of(_find_one(graphic, "Size")), 5.0)
    return {
        "kind": "point",
        "shape": wkn or "circle",
        "size": size,
        "color": _svg_get(fill, "fill") or "#3b82f6",
        "opacity": _to_float(_svg_get(fill, "fill-opacity"), 1.0),
        "strokeColor": _svg_get(stroke, "stroke") or "#ffffff",
        "strokeWidth": _to_float(_svg_get(stroke, "stroke-width"), 1.0),
    }


# ── Helpers ──────────────────────────────────────────────────────────────


def _normalize_geom(geom_type: str) -> str:
    t = (geom_type or "").lower()
    if "point" in t:
        return "point"
    if "line" in t:
        return "line"
    return "polygon"


def _kind_for_geom(geom: str) -> str:
    return {"point": "point", "line": "line"}.get(geom, "fill")


def _default_symbol(geom: str) -> dict[str, Any]:
    if geom == "point":
        return {"kind": "point", "shape": "circle", "size": 5, "color": "#3b82f6", "opacity": 0.85, "strokeColor": "#ffffff", "strokeWidth": 1}
    if geom == "line":
        return {"kind": "line", "color": "#3b82f6", "width": 2, "opacity": 1.0}
    return {"kind": "fill", "color": "#3b82f6", "opacity": 0.4, "strokeColor": "#3b82f6", "strokeWidth": 1.5}


def _text(parent: ET.Element, tag: str, value: str) -> ET.Element:
    """Create a child element with text content. Single-tag only."""
    el = ET.SubElement(parent, tag)
    el.text = value
    return el


def _nested_text(parent: ET.Element, tags: list[str], value: str) -> ET.Element:
    """Create a chain of nested elements, attaching ``value`` to the leaf."""
    current = parent
    for t in tags:
        current = ET.SubElement(current, t)
    current.text = value
    return current


def _svg_param(parent: ET.Element, name: str, value: str) -> ET.Element:
    el = ET.SubElement(parent, _qname(SE_NS, "SvgParameter"), {"name": name})
    el.text = value
    return el


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        # Avoid trailing zeros while keeping enough precision for breaks
        if v == int(v):
            return str(int(v))
        return f"{v:.6g}"
    return str(v)


def _classes_from_ranges(style: dict[str, Any], geom: str) -> list[dict]:
    """Accept alternative key name ``ranges`` (as produced by the UI)."""
    ranges = style.get("ranges") or []
    out: list[dict] = []
    for r in ranges:
        sym = r.get("symbol")
        if sym is None:
            sym = {**_default_symbol(geom), "color": r.get("color", "#3b82f6")}
        out.append({
            "lower": r.get("min", r.get("lower", 0)),
            "upper": r.get("max", r.get("upper", 0)),
            "label": r.get("label", ""),
            "symbol": sym,
        })
    return out


# ── XML traversal (namespace-agnostic) ───────────────────────────────────


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_all(parent: ET.Element | None, local: str) -> list[ET.Element]:
    if parent is None:
        return []
    return [el for el in parent.iter() if _localname(el.tag) == local]


def _find_one(parent: ET.Element | None, local: str) -> ET.Element | None:
    results = _find_all(parent, local)
    return results[0] if results else None


def _find_first_literal(parent: ET.Element | None) -> ET.Element | None:
    if parent is None:
        return None
    return _find_one(parent, "Literal")


def _text_of(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    return (el.text or "").strip() or None


def _svg_get(container: ET.Element | None, name: str) -> str | None:
    if container is None:
        return None
    for child in container:
        if _localname(child.tag) in ("SvgParameter", "CssParameter") and child.get("name") == name:
            return (child.text or "").strip() or None
    return None


def _to_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
