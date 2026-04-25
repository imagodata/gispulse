"""
style_converter.py — Bidirectional QGIS QML <-> GISPulse style conversion.

Converts between:
  - QGIS QML XML (stored in GPKG layer_styles table)
  - GISPulse LayerStyleDef JSON (used by the portal UI)

Supports:
  - SingleSymbolRenderer
  - CategorizedSymbolRenderer
  - GraduatedSymbolRenderer
  - RuleBasedRenderer
  - Simple labeling extraction
  - Full QML generation for export back to QGIS
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any


# ── QML → GISPulse ─────────────────────────────────────────────────────


def qml_to_style_def(qml_xml: str, geom_type: str = "polygon") -> dict[str, Any]:
    """Parse a complete QGIS QML XML into a LayerStyleDef-compatible dict."""
    try:
        root = ET.fromstring(qml_xml)
    except ET.ParseError:
        return {"renderer": "single"}

    # Find renderer element
    renderer_el = root.find(".//renderer-v2")
    if renderer_el is None:
        # Try direct <renderer-v2> at top level
        renderer_el = root.find("renderer-v2")
    if renderer_el is None:
        return {"renderer": "single"}

    renderer_type = renderer_el.get("type", "singleSymbol")
    geom = _normalize_geom(geom_type)

    result: dict[str, Any] = {}

    if renderer_type == "singleSymbol":
        result = _parse_single(renderer_el, geom)
    elif renderer_type == "categorizedSymbol":
        result = _parse_categorized(renderer_el, geom)
    elif renderer_type == "graduatedSymbol":
        result = _parse_graduated(renderer_el, geom)
    elif renderer_type == "RuleRenderer":
        result = _parse_rule_based(renderer_el, geom)
    else:
        result = {"renderer": "single"}

    # Extract labeling
    labeling = _parse_labeling(root)
    if labeling:
        result["labeling"] = labeling

    return result


def _normalize_geom(geom_type: str) -> str:
    t = geom_type.lower()
    if "point" in t:
        return "point"
    if "line" in t:
        return "line"
    if "polygon" in t:
        return "polygon"
    return "polygon"


# ── Single symbol ──────────────────────────────────────────────────────


def _parse_single(renderer_el: ET.Element, geom: str) -> dict[str, Any]:
    symbols = renderer_el.find("symbols")
    if symbols is None:
        return {"renderer": "single"}

    symbol_el = symbols.find("symbol")
    if symbol_el is None:
        return {"renderer": "single"}

    sym = _parse_symbol(symbol_el, geom)
    return {"renderer": "single", "symbol": sym}


# ── Categorized ────────────────────────────────────────────────────────


def _parse_categorized(renderer_el: ET.Element, geom: str) -> dict[str, Any]:
    field = renderer_el.get("attr", "")
    symbols_el = renderer_el.find("symbols")
    categories_el = renderer_el.find("categories")

    if symbols_el is None or categories_el is None:
        return {"renderer": "categorized", "classField": field, "categories": []}

    # Build symbol lookup by key
    sym_map: dict[str, dict] = {}
    for sym_el in symbols_el.findall("symbol"):
        name = sym_el.get("name", "")
        sym_map[name] = _parse_symbol(sym_el, geom)

    categories: list[dict] = []
    for cat_el in categories_el.findall("category"):
        value = cat_el.get("value", "")
        label = cat_el.get("label", value)
        sym_key = cat_el.get("symbol", "")
        symbol = sym_map.get(sym_key, _default_symbol(geom))

        # Empty value string = "all other values" (null category)
        parsed_value: str | float | None = None
        if value == "":
            parsed_value = None
        else:
            try:
                parsed_value = float(value) if "." in value else int(value)
            except (ValueError, TypeError):
                parsed_value = value

        categories.append({
            "value": parsed_value,
            "label": label,
            "symbol": symbol,
        })

    return {
        "renderer": "categorized",
        "classField": field,
        "categories": categories,
    }


# ── Graduated ──────────────────────────────────────────────────────────


def _parse_graduated(renderer_el: ET.Element, geom: str) -> dict[str, Any]:
    field = renderer_el.get("attr", "")
    symbols_el = renderer_el.find("symbols")
    ranges_el = renderer_el.find("ranges")

    if symbols_el is None or ranges_el is None:
        return {"renderer": "graduated", "graduatedField": field, "classes": []}

    sym_map: dict[str, dict] = {}
    for sym_el in symbols_el.findall("symbol"):
        name = sym_el.get("name", "")
        sym_map[name] = _parse_symbol(sym_el, geom)

    classes: list[dict] = []
    for range_el in ranges_el.findall("range"):
        lower = float(range_el.get("lower", "0"))
        upper = float(range_el.get("upper", "0"))
        label = range_el.get("label", f"{lower} - {upper}")
        sym_key = range_el.get("symbol", "")
        symbol = sym_map.get(sym_key, _default_symbol(geom))

        classes.append({
            "lower": lower,
            "upper": upper,
            "label": label,
            "symbol": symbol,
        })

    # Detect classify method from mode attribute
    method = renderer_el.get("graduatedMethod", "")
    method_map = {
        "EqualInterval": "equal_interval",
        "Quantile": "quantile",
        "NaturalBreaks": "natural_breaks",
        "Jenks": "natural_breaks",
        "StdDev": "std_dev",
    }

    return {
        "renderer": "graduated",
        "graduatedField": field,
        "classifyMethod": method_map.get(method, "equal_interval"),
        "classes": classes,
    }


# ── Rule-based ─────────────────────────────────────────────────────────


def _parse_rule_based(renderer_el: ET.Element, geom: str) -> dict[str, Any]:
    rules_el = renderer_el.find("rules")
    if rules_el is None:
        return {"renderer": "rule-based", "rules": []}

    symbols_el = renderer_el.find("symbols")
    sym_map: dict[str, dict] = {}
    if symbols_el is not None:
        for sym_el in symbols_el.findall("symbol"):
            name = sym_el.get("name", "")
            sym_map[name] = _parse_symbol(sym_el, geom)

    rules: list[dict] = []
    for rule_el in rules_el.findall("rule"):
        name = rule_el.get("label", rule_el.get("description", "Rule"))
        filter_expr = rule_el.get("filter", "")
        sym_key = rule_el.get("symbol", "")
        enabled = rule_el.get("checkstate", "1") != "0"
        symbol = sym_map.get(sym_key, _default_symbol(geom))

        rules.append({
            "name": name,
            "filter": filter_expr,
            "symbol": symbol,
            "enabled": enabled,
        })

    return {"renderer": "rule-based", "rules": rules}


# ── Symbol parsing ─────────────────────────────────────────────────────


def _parse_symbol(symbol_el: ET.Element, geom: str) -> dict[str, Any]:
    """Parse a <symbol> element into a symbol dict."""
    # Find first layer (most QGIS symbols have a single layer)
    layer_el = symbol_el.find("layer")
    if layer_el is None:
        return _default_symbol(geom)

    props = {}
    for prop_el in layer_el.findall("prop"):
        k = prop_el.get("k", "")
        v = prop_el.get("v", "")
        props[k] = v

    if geom == "point":
        return _parse_point_symbol(props)
    elif geom == "line":
        return _parse_line_symbol(props)
    else:
        return _parse_fill_symbol(props)


def _parse_point_symbol(props: dict[str, str]) -> dict[str, Any]:
    color = _parse_color_prop(props.get("color", "0,0,255,255"))
    outline_color = _parse_color_prop(props.get("outline_color", "0,0,0,255"))
    outline_width = _parse_float(props.get("outline_width", "0.5"))
    size = _parse_float(props.get("size", "3"))
    name = props.get("name", "circle")

    shape_map = {
        "circle": "circle",
        "square": "square",
        "rectangle": "square",
        "triangle": "triangle",
        "cross": "cross",
        "cross2": "cross",
        "star": "star",
        "diamond": "diamond",
        "regular_star": "star",
    }

    opacity = _extract_alpha(props.get("color", "0,0,255,255"))
    rotation = _parse_float(props.get("angle", "0"))

    return {
        "kind": "point",
        "shape": shape_map.get(name, "circle"),
        "size": max(1, size * 1.5),  # QML mm → px approximation
        "color": color,
        "opacity": opacity,
        "strokeColor": outline_color,
        "strokeWidth": outline_width,
        **({"rotation": rotation} if rotation != 0 else {}),
    }


def _parse_line_symbol(props: dict[str, str]) -> dict[str, Any]:
    color = _parse_color_prop(props.get("line_color", props.get("color", "0,0,0,255")))
    width = _parse_float(props.get("line_width", props.get("width", "1")))
    opacity = _extract_alpha(props.get("line_color", props.get("color", "0,0,0,255")))
    dash = props.get("customdash", "")
    cap = props.get("capstyle", "round")
    join = props.get("joinstyle", "round")

    cap_map = {"flat": "butt", "square": "square", "round": "round"}
    join_map = {"bevel": "bevel", "miter": "miter", "round": "round"}

    result: dict[str, Any] = {
        "kind": "line",
        "color": color,
        "width": max(0.5, width * 1.5),  # mm → px
        "opacity": opacity,
        "cap": cap_map.get(cap, "round"),
        "join": join_map.get(join, "round"),
    }

    if dash:
        parts = [_parse_float(d) * 1.5 for d in dash.split(";") if d.strip()]
        if parts:
            result["dashPattern"] = parts

    return result


def _parse_fill_symbol(props: dict[str, str]) -> dict[str, Any]:
    color = _parse_color_prop(props.get("color", "0,0,255,128"))
    outline_color = _parse_color_prop(props.get("outline_color", "0,0,0,255"))
    outline_width = _parse_float(props.get("outline_width", "0.5"))
    opacity = _extract_alpha(props.get("color", "0,0,255,128"))

    return {
        "kind": "fill",
        "color": color,
        "opacity": opacity,
        "strokeColor": outline_color,
        "strokeWidth": max(0.5, outline_width * 1.5),
    }


def _default_symbol(geom: str) -> dict[str, Any]:
    if geom == "point":
        return {"kind": "point", "shape": "circle", "size": 5, "color": "#3b82f6", "opacity": 0.85, "strokeColor": "#ffffff", "strokeWidth": 1}
    if geom == "line":
        return {"kind": "line", "color": "#3b82f6", "width": 2, "opacity": 1, "cap": "round", "join": "round"}
    return {"kind": "fill", "color": "#3b82f6", "opacity": 0.4, "strokeColor": "#3b82f6", "strokeWidth": 1.5}


# ── Labeling ───────────────────────────────────────────────────────────


def _parse_labeling(root: ET.Element) -> dict[str, Any] | None:
    """Extract simple labeling config from QML."""
    labeling = root.find(".//labeling")
    if labeling is None:
        return None

    # Check if labeling is enabled
    labeling_type = labeling.get("type", "")
    if labeling_type not in ("simple", ""):
        return None

    settings = labeling.find(".//settings")
    if settings is None:
        settings = labeling.find("settings")
    if settings is None:
        return None

    # Field name
    field_el = settings.find(".//fieldName")
    if field_el is None or not field_el.text:
        return None

    field = field_el.text

    # Text style
    text_style = settings.find(".//text-style")
    color = "#000000"
    font_size = 10
    font_weight = "normal"
    if text_style is not None:
        color = text_style.get("textColor", "#000000")
        font_size = _parse_float(text_style.get("fontSize", "10"))
        weight = int(text_style.get("fontWeight", "50"))
        font_weight = "bold" if weight >= 75 else "normal"

    # Halo / buffer
    text_buffer = settings.find(".//text-buffer")
    halo_color = None
    halo_width = None
    if text_buffer is not None:
        if text_buffer.get("bufferDraw", "0") == "1":
            halo_color = text_buffer.get("bufferColor", "#ffffff")
            halo_width = _parse_float(text_buffer.get("bufferSize", "1"))

    return {
        "enabled": True,
        "field": field,
        "color": color,
        "fontSize": font_size,
        "fontWeight": font_weight,
        **({"haloColor": halo_color} if halo_color else {}),
        **({"haloWidth": halo_width} if halo_width else {}),
    }


# ── GISPulse → QML ────────────────────────────────────────────────────


def style_def_to_qml(style_def: dict[str, Any], geom_type: str = "polygon") -> str:
    """Generate valid QGIS QML XML from a LayerStyleDef dict."""
    geom = _normalize_geom(geom_type)
    renderer_type = style_def.get("renderer", "single")

    root = ET.Element("qgis")
    root.set("version", "3.34.0")

    if renderer_type == "single":
        renderer_el = _build_single_renderer(style_def, geom)
    elif renderer_type == "categorized":
        renderer_el = _build_categorized_renderer(style_def, geom)
    elif renderer_type == "graduated":
        renderer_el = _build_graduated_renderer(style_def, geom)
    elif renderer_type == "rule-based":
        renderer_el = _build_rule_renderer(style_def, geom)
    else:
        renderer_el = _build_single_renderer(style_def, geom)

    root.append(renderer_el)

    # Labeling
    labeling = style_def.get("labeling")
    if labeling and labeling.get("enabled"):
        root.append(_build_labeling(labeling))

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _build_single_renderer(style_def: dict, geom: str) -> ET.Element:
    renderer = ET.Element("renderer-v2", type="singleSymbol")
    symbols = ET.SubElement(renderer, "symbols")
    sym = style_def.get("symbol", _default_symbol(geom))
    symbols.append(_build_symbol_element("0", sym, geom))
    return renderer


def _build_categorized_renderer(style_def: dict, geom: str) -> ET.Element:
    renderer = ET.Element("renderer-v2", type="categorizedSymbol")
    renderer.set("attr", style_def.get("classField", ""))

    symbols = ET.SubElement(renderer, "symbols")
    categories = ET.SubElement(renderer, "categories")

    for i, cat in enumerate(style_def.get("categories", [])):
        sym_key = str(i)
        symbols.append(_build_symbol_element(sym_key, cat.get("symbol", _default_symbol(geom)), geom))

        cat_el = ET.SubElement(categories, "category")
        cat_el.set("value", "" if cat.get("value") is None else str(cat["value"]))
        cat_el.set("label", cat.get("label", str(cat.get("value", ""))))
        cat_el.set("symbol", sym_key)

    return renderer


def _build_graduated_renderer(style_def: dict, geom: str) -> ET.Element:
    renderer = ET.Element("renderer-v2", type="graduatedSymbol")
    renderer.set("attr", style_def.get("graduatedField", ""))

    method = style_def.get("classifyMethod", "equal_interval")
    method_map = {
        "equal_interval": "EqualInterval",
        "quantile": "Quantile",
        "natural_breaks": "NaturalBreaks",
        "std_dev": "StdDev",
    }
    renderer.set("graduatedMethod", method_map.get(method, "EqualInterval"))

    symbols = ET.SubElement(renderer, "symbols")
    ranges = ET.SubElement(renderer, "ranges")

    for i, cls in enumerate(style_def.get("classes", [])):
        sym_key = str(i)
        symbols.append(_build_symbol_element(sym_key, cls.get("symbol", _default_symbol(geom)), geom))

        range_el = ET.SubElement(ranges, "range")
        range_el.set("lower", str(cls.get("lower", 0)))
        range_el.set("upper", str(cls.get("upper", 0)))
        range_el.set("label", cls.get("label", f"{cls.get('lower', 0)} - {cls.get('upper', 0)}"))
        range_el.set("symbol", sym_key)

    return renderer


def _build_rule_renderer(style_def: dict, geom: str) -> ET.Element:
    renderer = ET.Element("renderer-v2", type="RuleRenderer")
    symbols = ET.SubElement(renderer, "symbols")
    rules_el = ET.SubElement(renderer, "rules")

    for i, rule in enumerate(style_def.get("rules", [])):
        sym_key = str(i)
        symbols.append(_build_symbol_element(sym_key, rule.get("symbol", _default_symbol(geom)), geom))

        rule_el = ET.SubElement(rules_el, "rule")
        rule_el.set("label", rule.get("name", f"Rule {i + 1}"))
        rule_el.set("filter", rule.get("filter", ""))
        rule_el.set("symbol", sym_key)
        rule_el.set("checkstate", "1" if rule.get("enabled", True) else "0")

    return renderer


def _build_symbol_element(name: str, sym: dict, geom: str) -> ET.Element:
    """Build a <symbol> element from a symbol dict."""
    kind = sym.get("kind", geom)

    if kind == "point":
        sym_type = "marker"
    elif kind == "line":
        sym_type = "line"
    else:
        sym_type = "fill"

    symbol = ET.Element("symbol", name=name, type=sym_type)
    layer_el = ET.SubElement(symbol, "layer")

    if kind == "point":
        layer_el.set("class", "SimpleMarker")
        _add_prop(layer_el, "color", _to_qgis_color(sym.get("color", "#3b82f6"), sym.get("opacity", 0.85)))
        _add_prop(layer_el, "outline_color", _to_qgis_color(sym.get("strokeColor", "#ffffff"), 1))
        _add_prop(layer_el, "outline_width", str(round(sym.get("strokeWidth", 1) / 1.5, 2)))
        _add_prop(layer_el, "size", str(round(sym.get("size", 5) / 1.5, 2)))
        _add_prop(layer_el, "name", sym.get("shape", "circle"))
        if sym.get("rotation"):
            _add_prop(layer_el, "angle", str(sym["rotation"]))

    elif kind == "line":
        layer_el.set("class", "SimpleLine")
        _add_prop(layer_el, "line_color", _to_qgis_color(sym.get("color", "#3b82f6"), sym.get("opacity", 1)))
        _add_prop(layer_el, "line_width", str(round(sym.get("width", 2) / 1.5, 2)))
        cap_map = {"butt": "flat", "square": "square", "round": "round"}
        join_map = {"bevel": "bevel", "miter": "miter", "round": "round"}
        _add_prop(layer_el, "capstyle", cap_map.get(sym.get("cap", "round"), "round"))
        _add_prop(layer_el, "joinstyle", join_map.get(sym.get("join", "round"), "round"))
        if sym.get("dashPattern"):
            _add_prop(layer_el, "customdash", ";".join(str(round(d / 1.5, 2)) for d in sym["dashPattern"]))
            _add_prop(layer_el, "use_custom_dash", "1")

    else:  # fill
        layer_el.set("class", "SimpleFill")
        _add_prop(layer_el, "color", _to_qgis_color(sym.get("color", "#3b82f6"), sym.get("opacity", 0.4)))
        _add_prop(layer_el, "outline_color", _to_qgis_color(sym.get("strokeColor", "#3b82f6"), 1))
        _add_prop(layer_el, "outline_width", str(round(sym.get("strokeWidth", 1.5) / 1.5, 2)))
        _add_prop(layer_el, "style", "solid")

    return symbol


def _build_labeling(label_def: dict) -> ET.Element:
    """Build a <labeling> element."""
    labeling = ET.Element("labeling", type="simple")
    settings = ET.SubElement(labeling, "settings")

    field_name = ET.SubElement(settings, "fieldName")
    field_name.text = label_def.get("field", "")

    text_style = ET.SubElement(settings, "text-style")
    text_style.set("textColor", label_def.get("color", "#000000"))
    text_style.set("fontSize", str(label_def.get("fontSize", 10)))
    text_style.set("fontWeight", "75" if label_def.get("fontWeight") == "bold" else "50")

    if label_def.get("haloColor"):
        buf = ET.SubElement(settings, "text-buffer")
        buf.set("bufferDraw", "1")
        buf.set("bufferColor", label_def["haloColor"])
        buf.set("bufferSize", str(label_def.get("haloWidth", 1)))

    return labeling


# ── Utility helpers ────────────────────────────────────────────────────


def _add_prop(parent: ET.Element, key: str, value: str) -> None:
    prop = ET.SubElement(parent, "prop")
    prop.set("k", key)
    prop.set("v", value)


def _parse_color_prop(color_str: str) -> str:
    """Convert QGIS 'r,g,b,a' color to hex '#rrggbb'."""
    parts = color_str.split(",")
    if len(parts) >= 3:
        try:
            r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
            return f"#{r:02x}{g:02x}{b:02x}"
        except ValueError:
            pass
    return "#888888"


def _extract_alpha(color_str: str) -> float:
    """Extract alpha from 'r,g,b,a' as 0-1 float."""
    parts = color_str.split(",")
    if len(parts) >= 4:
        try:
            return round(int(parts[3]) / 255, 2)
        except ValueError:
            pass
    return 1.0


def _to_qgis_color(hex_color: str, opacity: float) -> str:
    """Convert hex color + opacity to QGIS 'r,g,b,a' format."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "0,0,255,255"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(min(max(opacity, 0), 1) * 255)
    return f"{r},{g},{b},{a}"


def _parse_float(s: str, default: float = 0.0) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return default
