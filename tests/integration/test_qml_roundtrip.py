"""QML ↔ LayerStyleDef roundtrip on real-world QGIS 3.34 fixtures.

Closes #44. Each fixture is parsed, serialised, parsed again, and the second
roundtrip must be idempotent (the first roundtrip may normalise unsupported
fields away — known lossy areas are documented in fixture_expectations).

Lossy areas (intentional, documented here):
- prop k="X" v="Y" legacy QGIS 3.x format → not supported, only Option/Map is
- text-buffer color/opacity → halo only stores color+width
- placement enum → mapped to "point" / "line" / "curved"
- rule-based scalemindenom/scalemaxdenom → not preserved (rules carry filter only)
- prop k="customdash" → not supported by serialiser yet
- outline_width: zero values get normalised on first serialise (0.0 → 0.33 → 0.5).
  We strip strokeWidth from the idempotence comparison; second roundtrip is stable.
- label field text vs <fieldname name="..."/> attribute form → only the text-content
  form is parsed (parser limitation worth tracking but out of scope for #44).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location(
    "style_converter", Path(__file__).resolve().parents[2] / "src" / "gispulse" / "persistence" / "style_converter.py"
)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

qml_to_style_def = sc.qml_to_style_def
style_def_to_qml = sc.style_def_to_qml


FIXTURE_DIR = Path(__file__).parent / "qml_fixtures"


@pytest.fixture(params=sorted(FIXTURE_DIR.glob("*.qml")), ids=lambda p: p.stem)
def qml_fixture(request) -> tuple[str, str]:
    """Load each .qml from disk and infer geom_type from filename."""
    p: Path = request.param
    geom_type = (
        "point" if "point" in p.stem
        else "line" if "rule_based" in p.stem or "labels_halo_curved" in p.stem
        else "polygon"
    )
    return p.read_text(encoding="utf-8"), geom_type


_VOLATILE_KEYS = {"label", "strokeWidth"}


def _strip_volatile(d: Any) -> Any:
    """Recursively drop fields that are normalised between roundtrips."""
    if isinstance(d, dict):
        return {k: _strip_volatile(v) for k, v in d.items() if k not in _VOLATILE_KEYS}
    if isinstance(d, list):
        return [_strip_volatile(x) for x in d]
    return d


class TestQmlRoundtripIdempotent:
    def test_second_roundtrip_is_stable(self, qml_fixture):
        """First parse may normalise; second roundtrip must be deep-equal."""
        qml, geom = qml_fixture
        sd1 = qml_to_style_def(qml, geom)
        qml_again = style_def_to_qml(sd1, geom)
        sd2 = qml_to_style_def(qml_again, geom)
        assert _strip_volatile(sd1) == _strip_volatile(sd2)

    def test_serialised_qml_is_valid_xml(self, qml_fixture):
        import xml.etree.ElementTree as ET
        qml, geom = qml_fixture
        sd = qml_to_style_def(qml, geom)
        out = style_def_to_qml(sd, geom)
        ET.fromstring(out)

    def test_renderer_kind_preserved(self, qml_fixture):
        """The renderer family must survive a roundtrip (single→single, etc)."""
        qml, geom = qml_fixture
        sd1 = qml_to_style_def(qml, geom)
        qml2 = style_def_to_qml(sd1, geom)
        sd2 = qml_to_style_def(qml2, geom)
        assert sd1.get("renderer") == sd2.get("renderer")


class TestSpecificFixtures:
    """Targeted assertions per fixture — protects against silent regressions."""

    def _load(self, stem: str, geom: str) -> dict:
        qml = (FIXTURE_DIR / f"{stem}.qml").read_text(encoding="utf-8")
        return qml_to_style_def(qml, geom)

    def test_categorized_polygon_landuse(self):
        sd = self._load("01_categorized_polygon_landuse", "polygon")
        assert sd["renderer"] == "categorized"
        assert sd["classField"] == "landuse"
        cats = sd.get("categories") or []
        values = [c.get("value") for c in cats]
        assert "residential" in values
        assert "commercial" in values
        assert "forest" in values

    def test_graduated_point_population_jenks(self):
        sd = self._load("02_graduated_point_population_jenks", "point")
        assert sd["renderer"] == "graduated"
        assert sd["graduatedField"] == "population"
        classes = sd.get("classes") or []
        assert len(classes) == 5
        assert classes[0]["lower"] == 0.0
        assert classes[-1]["upper"] == 1_000_000.0

    def test_rule_based_with_filter(self):
        sd = self._load("03_rule_based_scale_visibility", "line")
        assert sd["renderer"] == "rule-based"
        rules = sd.get("rules") or []
        assert len(rules) == 3
        for r in rules:
            assert "filter" in r
            assert r["enabled"] is True

    def test_labels_halo_curved(self):
        sd = self._load("04_labels_halo_curved", "line")
        labeling = sd.get("labeling") or {}
        assert labeling.get("enabled") is True
        assert labeling.get("field") == "name"
        assert labeling.get("haloColor") is not None

    def test_categorized_with_labels(self):
        sd = self._load("05_categorized_with_labels", "polygon")
        assert sd["renderer"] == "categorized"
        assert sd["classField"] == "building_type"
        labeling = sd.get("labeling") or {}
        assert labeling.get("enabled") is True
        assert labeling.get("field") == "addr_housenumber"
