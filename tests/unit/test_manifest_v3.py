"""Unit tests for the v3 manifest (ELT Lot 4A — issue #247).

Covers the schema, the loader, the ``models:`` → PipelineSpec compiler
and the v1/v2 → v3 migration helper. No engine is exercised — the tests
assert on the structure of the parsed manifest and the compiled
PipelineSpec.
"""

from __future__ import annotations

import json
import textwrap

import pytest
import yaml

from gispulse.core.manifest_v3 import (
    ManifestV3,
    ModelSpec,
    SourceSpec,
    compile_to_pipeline,
    load_manifest_v3,
    manifest_to_dict,
    migrate_to_v3,
)
from gispulse.core.pipeline_schema import SCHEMA_V3, validate_pipeline_json


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_schema_v3_accepts_canonical_adr0005_example():
    raw = {
        "version": 3,
        "sources": {
            "cadastre": {"uri": "./parcelles.gpkg", "crs": "EPSG:2154"},
            "plu": {"uri": "s3://bucket/plu.parquet"},
        },
        "staging": {"engine": "duckdb", "cdc": "incremental"},
        "models": {
            "zones_u": {
                "select": "plu",
                "transform": [{"filter": {"expression": "zone == 'U'"}}],
                "materialize": "view",
            },
            "parcelles_constructibles": {
                "select": "cadastre",
                "transform": [
                    {"spatial_join": {"with": "zones_u", "predicate": "intersects"}},
                    {"area_length": {"compute_area": True, "area_col": "surface_m2"}},
                ],
                "materialize": "incremental",
            },
        },
        "triggers": [
            {
                "name": "notify",
                "on": ["INSERT"],
                "table": "parcelles_constructibles",
                "actions": [{"type": "webhook", "url": "https://example.com/hook"}],
            }
        ],
    }
    assert validate_pipeline_json(raw) == []


def test_schema_v3_rejects_wrong_version():
    errors = validate_pipeline_json({"version": 2, "sources": {}, "models": {}})
    assert any("version" in e.lower() or "steps" in e.lower() for e in errors)


def test_schema_v3_rejects_transform_step_with_two_keys():
    raw = {
        "version": 3,
        "sources": {"x": {"uri": "./x.gpkg"}},
        "models": {
            "m": {
                "select": "x",
                # Two-keyed transform — schema requires exactly one.
                "transform": [{"filter": {}, "buffer": {}}],
            }
        },
    }
    errors = validate_pipeline_json(raw)
    assert errors, "schema should reject transforms with multiple capabilities"


def test_schema_v3_id_routes_via_version_field():
    """The schema picker routes v3 manifests by ``version: 3``."""
    assert SCHEMA_V3["properties"]["version"] == {"const": 3}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


_CANONICAL_YAML = textwrap.dedent(
    """
    version: 3
    name: demo
    sources:
      cadastre:
        uri: ./parcelles.gpkg
        crs: EPSG:2154
      plu:
        uri: s3://bucket/plu.parquet
    models:
      zones_u:
        select: plu
        transform:
          - filter: { expression: "zone == 'U'" }
      parcelles_constructibles:
        select: cadastre
        transform:
          - spatial_join: { with: zones_u, predicate: intersects }
          - area_length: { compute_area: true }
        materialize: incremental
    """
)


def test_loader_parses_yaml(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text(_CANONICAL_YAML, encoding="utf-8")
    m = load_manifest_v3(path)
    assert m.name == "demo"
    assert set(m.sources) == {"cadastre", "plu"}
    assert m.sources["cadastre"].crs == "EPSG:2154"
    assert set(m.models) == {"zones_u", "parcelles_constructibles"}
    pc = m.models["parcelles_constructibles"]
    assert pc.select == "cadastre"
    assert pc.materialize == "incremental"
    assert len(pc.transform) == 2


def test_loader_parses_json(tmp_path):
    raw = yaml.safe_load(_CANONICAL_YAML)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    m = load_manifest_v3(path)
    assert set(m.models) == {"zones_u", "parcelles_constructibles"}


def test_loader_rejects_wrong_version(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("version: 2\nname: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        load_manifest_v3(path)


def test_loader_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_manifest_v3(tmp_path / "nope.yaml")


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def _canonical_manifest() -> ManifestV3:
    return ManifestV3(
        sources={
            "cadastre": SourceSpec(name="cadastre", uri="./p.gpkg"),
            "plu": SourceSpec(name="plu", uri="s3://b/plu.parquet"),
        },
        models={
            "zones_u": ModelSpec(
                name="zones_u",
                select="plu",
                transform=[{"filter": {"expression": "zone == 'U'"}}],
            ),
            "parcelles_constructibles": ModelSpec(
                name="parcelles_constructibles",
                select="cadastre",
                transform=[
                    {"spatial_join": {"with": "zones_u", "predicate": "intersects"}},
                    {"area_length": {"compute_area": True}},
                ],
            ),
        },
    )


def test_compile_produces_three_steps_with_correct_chain():
    ps = compile_to_pipeline(_canonical_manifest())
    assert [s.id for s in ps.steps] == [
        "zones_u",
        "parcelles_constructibles__t0",
        "parcelles_constructibles",
    ]
    assert ps.ref_layers == {
        "cadastre": "./p.gpkg",
        "plu": "s3://b/plu.parquet",
    }


def test_compile_select_source_keeps_step_input_none():
    """A model rooted at a *source* compiles to a first step with input=None."""
    ps = compile_to_pipeline(_canonical_manifest())
    by_id = {s.id: s for s in ps.steps}
    # zones_u selects plu (a source) → input=None.
    assert by_id["zones_u"].input is None
    # parcelles_constructibles__t0 selects cadastre (a source); with=zones_u
    # rides on ref_layer rather than swapping the primary input.
    sj = by_id["parcelles_constructibles__t0"]
    assert sj.input is None
    assert sj.capability == "spatial_join"
    assert sj.params["ref_layer"] == "zones_u"
    assert sj.params["predicate"] == "intersects"


def test_compile_chains_transforms_within_a_model():
    ps = compile_to_pipeline(_canonical_manifest())
    by_id = {s.id: s for s in ps.steps}
    assert by_id["parcelles_constructibles"].input == "parcelles_constructibles__t0"
    assert by_id["parcelles_constructibles"].capability == "area_length"


def test_compile_select_model_routes_input_to_terminal_step():
    """A model selecting another *model* uses that model's terminal id."""
    m = ManifestV3(
        sources={"x": SourceSpec(name="x", uri="./x.gpkg")},
        models={
            "a": ModelSpec(
                name="a", select="x",
                transform=[{"filter": {"expression": "1==1"}}],
            ),
            "b": ModelSpec(
                name="b", select="a",
                transform=[{"buffer": {"distance": 5}}],
            ),
        },
    )
    ps = compile_to_pipeline(m)
    by_id = {s.id: s for s in ps.steps}
    assert by_id["b"].input == "a"


def test_compile_rejects_unknown_select():
    m = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="./x.gpkg")},
        models={"m": ModelSpec(name="m", select="nope", transform=[])},
    )
    with pytest.raises(ValueError, match="Unknown select target"):
        compile_to_pipeline(m)


def test_compile_empty_transform_yields_identity_filter():
    m = ManifestV3(
        sources={"src": SourceSpec(name="src", uri="./x.gpkg")},
        models={"m": ModelSpec(name="m", select="src", transform=[])},
    )
    ps = compile_to_pipeline(m)
    assert len(ps.steps) == 1
    assert ps.steps[0].capability == "filter"
    assert ps.steps[0].params == {}


# ---------------------------------------------------------------------------
# Migration v1 / v2 → v3
# ---------------------------------------------------------------------------


def test_migrate_v2_pipelinespec_to_v3():
    v2 = {
        "version": 2,
        "name": "demo",
        "ref_layers": {"zones": "./zones.gpkg"},
        "steps": [
            {"id": "s1", "capability": "filter", "params": {"expression": "pop > 100"}},
            {"id": "s2", "capability": "buffer", "params": {"distance": 50}, "input": "s1"},
        ],
    }
    v3 = migrate_to_v3(v2)
    assert v3["version"] == 3
    assert "zones" in v3["sources"]
    assert "input" in v3["sources"]  # primary-input placeholder
    assert set(v3["models"]) == {"s1", "s2"}
    assert v3["models"]["s2"]["select"] == "s1"
    assert validate_pipeline_json(v3) == []


def test_migrate_v1_flat_rules_to_v3():
    v1 = [
        {"name": "a", "capability": "filter", "config": {"expression": "x > 1"}},
        {"name": "b", "capability": "buffer", "config": {"distance": 10}},
    ]
    v3 = migrate_to_v3(v1)
    assert v3["version"] == 3
    # v1 is a linear chain → b selects a.
    assert v3["models"]["b"]["select"] == "a"
    assert validate_pipeline_json(v3) == []


def test_migrate_already_v3_is_passthrough():
    v3 = {"version": 3, "sources": {"x": {"uri": "./x.gpkg"}}, "models": {}}
    assert migrate_to_v3(v3) is v3


def test_manifest_to_dict_round_trips_canonical():
    m = _canonical_manifest()
    d = manifest_to_dict(m)
    assert d["version"] == 3
    assert d["sources"]["cadastre"]["uri"] == "./p.gpkg"
    assert d["models"]["parcelles_constructibles"]["select"] == "cadastre"
    # The output validates against the schema.
    assert validate_pipeline_json(d) == []
