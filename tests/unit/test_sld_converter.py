"""Unit tests for persistence.sld_converter (LayerStyleDef ↔ OGC SLD 1.1)."""

from __future__ import annotations

import xml.etree.ElementTree as ET


from gispulse.persistence.sld_converter import sld_to_style_def, style_def_to_sld


SE_NS = "http://www.opengis.net/se"
OGC_NS = "http://www.opengis.net/ogc"
SLD_NS = "http://www.opengis.net/sld"


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _rules(root: ET.Element) -> list[ET.Element]:
    return [el for el in root.iter() if _localname(el.tag) == "Rule"]


def _svg(el: ET.Element, name: str) -> str | None:
    for c in el:
        if _localname(c.tag) in ("SvgParameter", "CssParameter") and c.get("name") == name:
            return c.text
    return None


class TestExportSingle:
    def test_single_polygon(self):
        style = {
            "renderer": "single",
            "symbol": {"kind": "fill", "color": "#ff0000", "opacity": 0.7, "strokeColor": "#000000", "strokeWidth": 1.5},
        }
        sld = style_def_to_sld(style, geom_type="polygon", layer_name="parcelles")
        root = ET.fromstring(sld)
        assert root.tag.endswith("StyledLayerDescriptor")
        assert root.get("version") == "1.1.0"

        rules = _rules(root)
        assert len(rules) == 1
        # Exactly one PolygonSymbolizer with correct fill
        poly = next(el for el in rules[0].iter() if _localname(el.tag) == "PolygonSymbolizer")
        fill = next(el for el in poly if _localname(el.tag) == "Fill")
        assert _svg(fill, "fill") == "#ff0000"
        assert _svg(fill, "fill-opacity") == "0.7"

    def test_single_line(self):
        style = {"renderer": "single", "symbol": {"kind": "line", "color": "#00ff00", "width": 3, "opacity": 0.9}}
        sld = style_def_to_sld(style, geom_type="line")
        root = ET.fromstring(sld)
        line_sym = next((el for el in root.iter() if _localname(el.tag) == "LineSymbolizer"), None)
        assert line_sym is not None
        stroke = next(el for el in line_sym if _localname(el.tag) == "Stroke")
        assert _svg(stroke, "stroke") == "#00ff00"
        assert _svg(stroke, "stroke-width") == "3"

    def test_single_point(self):
        style = {
            "renderer": "single",
            "symbol": {"kind": "point", "shape": "square", "size": 8, "color": "#112233", "strokeColor": "#ffffff", "strokeWidth": 2},
        }
        sld = style_def_to_sld(style, geom_type="point")
        root = ET.fromstring(sld)
        wkn = next((el for el in root.iter() if _localname(el.tag) == "WellKnownName"), None)
        assert wkn is not None
        assert wkn.text == "square"


class TestExportGraduated:
    def _graduated(self) -> dict:
        return {
            "renderer": "graduated",
            "graduatedField": "price_per_m2",
            "classifyMethod": "jenks",
            "classes": [
                {"lower": 1500, "upper": 3200, "label": "1500 – 3200", "symbol": {"kind": "fill", "color": "#ffffb2"}},
                {"lower": 3200, "upper": 4800, "label": "3200 – 4800", "symbol": {"kind": "fill", "color": "#fecc5c"}},
                {"lower": 4800, "upper": 6500, "label": "4800 – 6500", "symbol": {"kind": "fill", "color": "#fd8d3c"}},
                {"lower": 6500, "upper": 10000, "label": "6500 – 10000", "symbol": {"kind": "fill", "color": "#f03b20"}},
                {"lower": 10000, "upper": 25000, "label": "10000 – 25000", "symbol": {"kind": "fill", "color": "#bd0026"}},
            ],
        }

    def test_graduated_five_rules(self):
        sld = style_def_to_sld(self._graduated(), geom_type="polygon", layer_name="dvf")
        root = ET.fromstring(sld)
        rules = _rules(root)
        assert len(rules) == 5

    def test_graduated_property_between(self):
        sld = style_def_to_sld(self._graduated())
        root = ET.fromstring(sld)
        betweens = [el for el in root.iter() if _localname(el.tag) == "PropertyIsBetween"]
        assert len(betweens) == 5
        # First rule: field & bounds
        between = betweens[0]
        prop = next(el for el in between if _localname(el.tag) == "PropertyName")
        assert prop.text == "price_per_m2"
        lower_bound = next(el for el in between if _localname(el.tag) == "LowerBoundary")
        literal = next(el for el in lower_bound.iter() if _localname(el.tag) == "Literal")
        assert literal.text == "1500"

    def test_graduated_colors_in_fills(self):
        sld = style_def_to_sld(self._graduated())
        root = ET.fromstring(sld)
        fills = [el for el in root.iter() if _localname(el.tag) == "Fill"]
        colors = [_svg(f, "fill") for f in fills]
        assert "#ffffb2" in colors
        assert "#bd0026" in colors

    def test_graduated_ranges_alt_keyname(self):
        """UI often ships ``ranges`` (min/max/color) instead of ``classes``."""
        style = {
            "renderer": "graduated",
            "graduatedField": "pop",
            "ranges": [
                {"min": 0, "max": 100, "color": "#ffffb2", "label": "0-100"},
                {"min": 100, "max": 500, "color": "#bd0026", "label": "100-500"},
            ],
        }
        sld = style_def_to_sld(style, geom_type="polygon")
        root = ET.fromstring(sld)
        assert len(_rules(root)) == 2


class TestExportCategorized:
    def test_categorized_with_else(self):
        style = {
            "renderer": "categorized",
            "classField": "status",
            "categories": [
                {"value": "deployed", "label": "Déployé", "symbol": {"kind": "fill", "color": "#2ca02c"}},
                {"value": "planned", "label": "Planifié", "symbol": {"kind": "fill", "color": "#ff7f0e"}},
                {"value": None, "label": "Autre", "symbol": {"kind": "fill", "color": "#bdbdbd"}},
            ],
        }
        sld = style_def_to_sld(style, geom_type="polygon", layer_name="ftth")
        root = ET.fromstring(sld)
        rules = _rules(root)
        assert len(rules) == 3
        # ElseFilter present on the last rule
        assert any(_localname(el.tag) == "ElseFilter" for el in rules[-1].iter())
        # PropertyIsEqualTo on the first two
        eqs = [el for el in root.iter() if _localname(el.tag) == "PropertyIsEqualTo"]
        assert len(eqs) == 2


class TestImportRoundtrip:
    def test_graduated_roundtrip(self):
        original = {
            "renderer": "graduated",
            "graduatedField": "price",
            "classes": [
                {"lower": 0, "upper": 100, "label": "0-100", "symbol": {"kind": "fill", "color": "#ffffb2", "opacity": 1.0, "strokeColor": "#3b82f6", "strokeWidth": 1.5}},
                {"lower": 100, "upper": 500, "label": "100-500", "symbol": {"kind": "fill", "color": "#bd0026", "opacity": 1.0, "strokeColor": "#3b82f6", "strokeWidth": 1.5}},
            ],
        }
        sld = style_def_to_sld(original, geom_type="polygon")
        parsed = sld_to_style_def(sld, geom_type="polygon")
        assert parsed["renderer"] == "graduated"
        assert parsed["graduatedField"] == "price"
        assert len(parsed["classes"]) == 2
        assert parsed["classes"][0]["lower"] == 0.0
        assert parsed["classes"][0]["upper"] == 100.0
        assert parsed["classes"][0]["symbol"]["color"] == "#ffffb2"
        assert parsed["classes"][1]["symbol"]["color"] == "#bd0026"

    def test_categorized_roundtrip(self):
        original = {
            "renderer": "categorized",
            "classField": "type",
            "categories": [
                {"value": "A", "label": "A", "symbol": {"kind": "fill", "color": "#ff0000", "opacity": 1.0, "strokeColor": "#3b82f6", "strokeWidth": 1.5}},
                {"value": "B", "label": "B", "symbol": {"kind": "fill", "color": "#00ff00", "opacity": 1.0, "strokeColor": "#3b82f6", "strokeWidth": 1.5}},
            ],
        }
        sld = style_def_to_sld(original, geom_type="polygon")
        parsed = sld_to_style_def(sld, geom_type="polygon")
        assert parsed["renderer"] == "categorized"
        assert parsed["classField"] == "type"
        values = {c["value"] for c in parsed["categories"]}
        assert values == {"A", "B"}

    def test_single_roundtrip(self):
        original = {
            "renderer": "single",
            "symbol": {"kind": "fill", "color": "#abcdef", "opacity": 0.5, "strokeColor": "#123456", "strokeWidth": 2.0},
        }
        sld = style_def_to_sld(original, geom_type="polygon")
        parsed = sld_to_style_def(sld, geom_type="polygon")
        assert parsed["renderer"] == "single"
        assert parsed["symbol"]["color"] == "#abcdef"


class TestValidXml:
    def test_export_is_well_formed(self):
        style = {"renderer": "single", "symbol": {"kind": "fill", "color": "#123456"}}
        sld = style_def_to_sld(style)
        assert sld.startswith("<?xml")
        # Must parse without error
        ET.fromstring(sld)

    def test_namespace_declarations(self):
        # Use a graduated style so ogc:Filter forces the ogc namespace declaration
        style = {
            "renderer": "graduated",
            "graduatedField": "v",
            "classes": [{"lower": 0, "upper": 1, "label": "0-1", "symbol": {"kind": "fill", "color": "#000"}}],
        }
        sld = style_def_to_sld(style)
        assert "xmlns" in sld
        assert "opengis.net/se" in sld
        assert "opengis.net/ogc" in sld
        assert "opengis.net/sld" in sld

    def test_empty_graduated_falls_back_to_single(self):
        style = {"renderer": "graduated", "graduatedField": "x", "classes": []}
        sld = style_def_to_sld(style, geom_type="polygon")
        root = ET.fromstring(sld)
        # Single fallback → exactly one rule
        assert len(_rules(root)) == 1


class TestErrorTolerance:
    def test_malformed_sld_returns_single(self):
        assert sld_to_style_def("not<xml>", geom_type="polygon") == {"renderer": "single"}

    def test_empty_sld_returns_single(self):
        empty = '<?xml version="1.0"?><StyledLayerDescriptor xmlns="http://www.opengis.net/sld" version="1.1.0"/>'
        result = sld_to_style_def(empty)
        assert result == {"renderer": "single"}
