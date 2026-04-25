"""Tests for persistence/style_converter.py — QML ↔ LayerStyleDef conversion."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

# Direct import to avoid heavy persistence.__init__ deps
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "style_converter", "persistence/style_converter.py"
)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

qml_to_style_def = sc.qml_to_style_def
style_def_to_qml = sc.style_def_to_qml


# ── Fixtures ───────────────────────────────────────────────────────────

SINGLE_FILL_QML = """<?xml version="1.0" encoding="UTF-8"?>
<qgis version="3.34">
  <renderer-v2 type="singleSymbol">
    <symbols>
      <symbol name="0" type="fill">
        <layer class="SimpleFill">
          <prop k="color" v="31,120,180,180" />
          <prop k="outline_color" v="0,0,0,255" />
          <prop k="outline_width" v="0.5" />
          <prop k="style" v="solid" />
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
</qgis>"""

SINGLE_POINT_QML = """<qgis version="3.34">
  <renderer-v2 type="singleSymbol">
    <symbols>
      <symbol name="0" type="marker">
        <layer class="SimpleMarker">
          <prop k="color" v="255,0,0,200" />
          <prop k="outline_color" v="0,0,0,255" />
          <prop k="outline_width" v="0.4" />
          <prop k="size" v="4" />
          <prop k="name" v="square" />
          <prop k="angle" v="45" />
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
</qgis>"""

SINGLE_LINE_QML = """<qgis version="3.34">
  <renderer-v2 type="singleSymbol">
    <symbols>
      <symbol name="0" type="line">
        <layer class="SimpleLine">
          <prop k="line_color" v="0,128,255,255" />
          <prop k="line_width" v="2" />
          <prop k="capstyle" v="round" />
          <prop k="joinstyle" v="miter" />
          <prop k="customdash" v="5;2;1;2" />
          <prop k="use_custom_dash" v="1" />
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
</qgis>"""

CATEGORIZED_QML = """<qgis version="3.34">
  <renderer-v2 type="categorizedSymbol" attr="landuse">
    <symbols>
      <symbol name="0" type="fill">
        <layer class="SimpleFill">
          <prop k="color" v="255,0,0,180" />
          <prop k="outline_color" v="0,0,0,255" />
          <prop k="outline_width" v="0.3" />
        </layer>
      </symbol>
      <symbol name="1" type="fill">
        <layer class="SimpleFill">
          <prop k="color" v="0,255,0,180" />
          <prop k="outline_color" v="0,0,0,255" />
          <prop k="outline_width" v="0.3" />
        </layer>
      </symbol>
      <symbol name="2" type="fill">
        <layer class="SimpleFill">
          <prop k="color" v="0,0,255,180" />
          <prop k="outline_color" v="0,0,0,255" />
          <prop k="outline_width" v="0.3" />
        </layer>
      </symbol>
    </symbols>
    <categories>
      <category value="residential" label="Residential" symbol="0" />
      <category value="commercial" label="Commercial" symbol="1" />
      <category value="" label="Other" symbol="2" />
    </categories>
  </renderer-v2>
</qgis>"""

GRADUATED_QML = """<qgis version="3.34">
  <renderer-v2 type="graduatedSymbol" attr="population" graduatedMethod="EqualInterval">
    <symbols>
      <symbol name="0" type="fill">
        <layer class="SimpleFill"><prop k="color" v="255,255,178,200" /><prop k="outline_color" v="0,0,0,255" /><prop k="outline_width" v="0.2" /></layer>
      </symbol>
      <symbol name="1" type="fill">
        <layer class="SimpleFill"><prop k="color" v="253,141,60,200" /><prop k="outline_color" v="0,0,0,255" /><prop k="outline_width" v="0.2" /></layer>
      </symbol>
      <symbol name="2" type="fill">
        <layer class="SimpleFill"><prop k="color" v="189,0,38,200" /><prop k="outline_color" v="0,0,0,255" /><prop k="outline_width" v="0.2" /></layer>
      </symbol>
    </symbols>
    <ranges>
      <range lower="0" upper="1000" label="0 - 1000" symbol="0" />
      <range lower="1000" upper="5000" label="1000 - 5000" symbol="1" />
      <range lower="5000" upper="50000" label="5000 - 50000" symbol="2" />
    </ranges>
  </renderer-v2>
</qgis>"""

RULE_BASED_QML = """<qgis version="3.34">
  <renderer-v2 type="RuleRenderer">
    <symbols>
      <symbol name="0" type="fill">
        <layer class="SimpleFill"><prop k="color" v="0,255,0,200" /><prop k="outline_color" v="0,0,0,255" /><prop k="outline_width" v="0.3" /></layer>
      </symbol>
      <symbol name="1" type="fill">
        <layer class="SimpleFill"><prop k="color" v="255,0,0,200" /><prop k="outline_color" v="0,0,0,255" /><prop k="outline_width" v="0.3" /></layer>
      </symbol>
    </symbols>
    <rules>
      <rule label="Active" filter="status = 'active'" symbol="0" checkstate="1" />
      <rule label="Inactive" filter="status = 'inactive'" symbol="1" checkstate="0" />
    </rules>
  </renderer-v2>
</qgis>"""

LABELED_QML = """<qgis version="3.34">
  <renderer-v2 type="singleSymbol">
    <symbols>
      <symbol name="0" type="fill">
        <layer class="SimpleFill"><prop k="color" v="100,150,200,180" /><prop k="outline_color" v="0,0,0,255" /><prop k="outline_width" v="0.3" /></layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings>
      <fieldName>name</fieldName>
      <text-style textColor="#333333" fontSize="12" fontWeight="75" />
      <text-buffer bufferDraw="1" bufferColor="#ffffff" bufferSize="1.5" />
    </settings>
  </labeling>
</qgis>"""


# ── Tests: QML → StyleDef ─────────────────────────────────────────────

class TestQmlToStyleDef:

    def test_single_fill(self):
        result = qml_to_style_def(SINGLE_FILL_QML, "polygon")
        assert result["renderer"] == "single"
        sym = result["symbol"]
        assert sym["kind"] == "fill"
        assert sym["color"] == "#1f78b4"
        assert 0.5 < sym["opacity"] < 0.8  # 180/255 ≈ 0.71
        assert sym["strokeColor"] == "#000000"

    def test_single_point(self):
        result = qml_to_style_def(SINGLE_POINT_QML, "point")
        assert result["renderer"] == "single"
        sym = result["symbol"]
        assert sym["kind"] == "point"
        assert sym["shape"] == "square"
        assert sym["color"] == "#ff0000"
        assert sym.get("rotation") == 45

    def test_single_line(self):
        result = qml_to_style_def(SINGLE_LINE_QML, "linestring")
        assert result["renderer"] == "single"
        sym = result["symbol"]
        assert sym["kind"] == "line"
        assert sym["color"] == "#0080ff"
        assert sym["cap"] == "round"
        assert sym["join"] == "miter"
        assert "dashPattern" in sym
        assert len(sym["dashPattern"]) == 4

    def test_categorized(self):
        result = qml_to_style_def(CATEGORIZED_QML, "polygon")
        assert result["renderer"] == "categorized"
        assert result["classField"] == "landuse"
        cats = result["categories"]
        assert len(cats) == 3
        assert cats[0]["value"] == "residential"
        assert cats[0]["label"] == "Residential"
        assert cats[0]["symbol"]["color"] == "#ff0000"
        assert cats[1]["value"] == "commercial"
        assert cats[2]["value"] is None  # empty string → null (fallback)

    def test_graduated(self):
        result = qml_to_style_def(GRADUATED_QML, "polygon")
        assert result["renderer"] == "graduated"
        assert result["graduatedField"] == "population"
        assert result["classifyMethod"] == "equal_interval"
        classes = result["classes"]
        assert len(classes) == 3
        assert classes[0]["lower"] == 0
        assert classes[0]["upper"] == 1000
        assert classes[2]["upper"] == 50000

    def test_rule_based(self):
        result = qml_to_style_def(RULE_BASED_QML, "polygon")
        assert result["renderer"] == "rule-based"
        rules = result["rules"]
        assert len(rules) == 2
        assert rules[0]["name"] == "Active"
        assert rules[0]["filter"] == "status = 'active'"
        assert rules[0]["enabled"] is True
        assert rules[1]["enabled"] is False

    def test_labeling(self):
        result = qml_to_style_def(LABELED_QML, "polygon")
        assert result["renderer"] == "single"
        label = result.get("labeling")
        assert label is not None
        assert label["enabled"] is True
        assert label["field"] == "name"
        assert label["color"] == "#333333"
        assert label["fontSize"] == 12
        assert label["fontWeight"] == "bold"
        assert label["haloColor"] == "#ffffff"
        assert label["haloWidth"] == 1.5

    def test_invalid_xml(self):
        result = qml_to_style_def("<not valid xml!!!>", "polygon")
        assert result["renderer"] == "single"

    def test_no_renderer(self):
        result = qml_to_style_def("<qgis><nothing/></qgis>", "polygon")
        assert result["renderer"] == "single"


# ── Tests: StyleDef → QML ─────────────────────────────────────────────

class TestStyleDefToQml:

    def test_single_fill_roundtrip(self):
        original = qml_to_style_def(SINGLE_FILL_QML, "polygon")
        qml = style_def_to_qml(original, "polygon")
        assert "singleSymbol" in qml
        assert "SimpleFill" in qml
        # Parse back
        roundtrip = qml_to_style_def(qml, "polygon")
        assert roundtrip["renderer"] == "single"
        assert roundtrip["symbol"]["kind"] == "fill"
        # Color should survive roundtrip
        assert roundtrip["symbol"]["color"] == original["symbol"]["color"]

    def test_categorized_roundtrip(self):
        original = qml_to_style_def(CATEGORIZED_QML, "polygon")
        qml = style_def_to_qml(original, "polygon")
        assert "categorizedSymbol" in qml
        roundtrip = qml_to_style_def(qml, "polygon")
        assert roundtrip["renderer"] == "categorized"
        assert roundtrip["classField"] == "landuse"
        assert len(roundtrip["categories"]) == len(original["categories"])

    def test_graduated_roundtrip(self):
        original = qml_to_style_def(GRADUATED_QML, "polygon")
        qml = style_def_to_qml(original, "polygon")
        assert "graduatedSymbol" in qml
        roundtrip = qml_to_style_def(qml, "polygon")
        assert roundtrip["renderer"] == "graduated"
        assert len(roundtrip["classes"]) == 3

    def test_rule_based_roundtrip(self):
        original = qml_to_style_def(RULE_BASED_QML, "polygon")
        qml = style_def_to_qml(original, "polygon")
        assert "RuleRenderer" in qml
        roundtrip = qml_to_style_def(qml, "polygon")
        assert roundtrip["renderer"] == "rule-based"
        assert len(roundtrip["rules"]) == 2

    def test_point_symbol_roundtrip(self):
        original = qml_to_style_def(SINGLE_POINT_QML, "point")
        qml = style_def_to_qml(original, "point")
        assert "SimpleMarker" in qml
        roundtrip = qml_to_style_def(qml, "point")
        assert roundtrip["symbol"]["kind"] == "point"
        assert roundtrip["symbol"]["shape"] == "square"

    def test_generates_valid_xml(self):
        style_def = {
            "renderer": "single",
            "symbol": {
                "kind": "fill",
                "color": "#3b82f6",
                "opacity": 0.5,
                "strokeColor": "#000000",
                "strokeWidth": 1.5,
            },
        }
        qml = style_def_to_qml(style_def, "polygon")
        # Should parse as valid XML
        root = ET.fromstring(qml)
        assert root.tag == "qgis"
        renderer = root.find("renderer-v2")
        assert renderer is not None
        assert renderer.get("type") == "singleSymbol"

    def test_labeling_roundtrip(self):
        original = qml_to_style_def(LABELED_QML, "polygon")
        qml = style_def_to_qml(original, "polygon")
        assert "<labeling" in qml
        assert "<fieldName>name</fieldName>" in qml
        roundtrip = qml_to_style_def(qml, "polygon")
        assert roundtrip["labeling"]["field"] == "name"


# ── Tests: Color helpers ───────────────────────────────────────────────

class TestColorHelpers:

    def test_parse_color_prop(self):
        assert sc._parse_color_prop("255,0,0,255") == "#ff0000"
        assert sc._parse_color_prop("0,128,255,200") == "#0080ff"
        assert sc._parse_color_prop("invalid") == "#888888"

    def test_extract_alpha(self):
        assert sc._extract_alpha("255,0,0,255") == 1.0
        assert sc._extract_alpha("255,0,0,128") == pytest.approx(0.5, abs=0.01)
        assert sc._extract_alpha("255,0,0,0") == 0.0

    def test_to_qgis_color(self):
        assert sc._to_qgis_color("#ff0000", 1.0) == "255,0,0,255"
        # 0.5 * 255 = 127.5 → rounds to 127 with int()
        result = sc._to_qgis_color("#0080ff", 0.5)
        parts = [int(x) for x in result.split(",")]
        assert parts[:3] == [0, 128, 255]
        assert abs(parts[3] - 128) <= 1

    def test_roundtrip_color(self):
        original = "31,120,180,180"
        hex_color = sc._parse_color_prop(original)
        alpha = sc._extract_alpha(original)
        back = sc._to_qgis_color(hex_color, alpha)
        # Allow ±1 rounding
        parts_orig = [int(x) for x in original.split(",")]
        parts_back = [int(x) for x in back.split(",")]
        for a, b in zip(parts_orig, parts_back):
            assert abs(a - b) <= 1
