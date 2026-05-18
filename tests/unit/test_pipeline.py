"""Tests for core.pipeline (v1/v2 grammar) and orchestration.pipeline_executor."""

from __future__ import annotations

import json
import pytest

import geopandas as gpd
from shapely.geometry import Point

from gispulse.core.pipeline import (
    PipelineSpec,
    StepSpec,
    load_pipeline,
    pipeline_to_dict,
    _parse_predicate,
)
from gispulse.core.predicates import AttrPredicate, CompoundPredicate, GeomPredicate
from gispulse.orchestration.pipeline_executor import PipelineExecutor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_gdf():
    """GeoDataFrame with 5 points and numeric values."""
    return gpd.GeoDataFrame(
        {
            "name": ["A", "B", "C", "D", "E"],
            "value": [10, 25, 50, 75, 100],
            "zone": ["urban", "rural", "urban", "rural", "urban"],
            "geometry": [
                Point(0, 0),
                Point(1, 1),
                Point(2, 2),
                Point(3, 3),
                Point(4, 4),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def v1_rules_file(tmp_path):
    """Create a v1 rules JSON file."""
    rules = [
        {
            "name": "filter_big",
            "capability": "filter",
            "config": {"expression": "value > 20", "order": 0},
            "enabled": True,
        },
        {
            "name": "buffer_100",
            "capability": "buffer",
            "config": {"distance": 100, "order": 1},
        },
    ]
    path = tmp_path / "v1_rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


@pytest.fixture
def v2_pipeline_file(tmp_path):
    """Create a v2 pipeline JSON file."""
    data = {
        "version": 2,
        "name": "test_pipeline",
        "description": "A test pipeline with DAG",
        "ref_layers": {"zones": "data/zones.gpkg"},
        "steps": [
            {
                "id": "filter_big",
                "type": "capability",
                "capability": "filter",
                "params": {"expression": "value > 20"},
            },
            {
                "id": "buffer",
                "type": "capability",
                "capability": "buffer",
                "params": {"distance": 100},
                "input": "filter_big",
            },
        ],
        "triggers": [
            {
                "on": "dml:parcelles:INSERT,UPDATE",
                "when": [{"type": "attr", "field": "value", "op": "gt", "value": 50}],
                "then": "run_pipeline",
            }
        ],
    }
    path = tmp_path / "v2_pipeline.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ===========================================================================
# core.pipeline — Loader tests
# ===========================================================================


class TestLoadPipelineV1:
    """Test loading v1 (flat rule list) format."""

    def test_loads_v1_format(self, v1_rules_file):
        spec = load_pipeline(v1_rules_file)
        assert spec.version == 1
        assert len(spec.steps) == 2
        assert spec.steps[0].id == "filter_big"
        assert spec.steps[0].capability == "filter"
        assert spec.steps[1].id == "buffer_100"

    def test_v1_extracts_order_from_config(self, v1_rules_file):
        spec = load_pipeline(v1_rules_file)
        assert spec.steps[0].order == 0
        assert spec.steps[1].order == 1
        # order should not leak into params
        assert "order" not in spec.steps[0].params

    def test_v1_is_not_dag(self, v1_rules_file):
        spec = load_pipeline(v1_rules_file)
        assert not spec.is_dag

    def test_v1_enabled_steps(self, v1_rules_file):
        spec = load_pipeline(v1_rules_file)
        assert len(spec.enabled_steps) == 2

    def test_v1_with_disabled_step(self, tmp_path):
        rules = [
            {"name": "a", "capability": "filter", "config": {"expression": "True"}},
            {"name": "b", "capability": "buffer", "config": {"distance": 10}, "enabled": False},
        ]
        path = tmp_path / "rules.json"
        path.write_text(json.dumps(rules), encoding="utf-8")
        spec = load_pipeline(path)
        assert len(spec.enabled_steps) == 1
        assert spec.enabled_steps[0].id == "a"

    def test_v1_auto_order_from_position(self, tmp_path):
        rules = [
            {"name": "first", "capability": "filter", "config": {"expression": "True"}},
            {"name": "second", "capability": "buffer", "config": {"distance": 10}},
            {"name": "third", "capability": "dissolve", "config": {}},
        ]
        path = tmp_path / "rules.json"
        path.write_text(json.dumps(rules), encoding="utf-8")
        spec = load_pipeline(path)
        assert [s.order for s in spec.steps] == [0, 1, 2]


class TestLoadPipelineV2:
    """Test loading v2 (dict with version key) format."""

    def test_loads_v2_format(self, v2_pipeline_file):
        spec = load_pipeline(v2_pipeline_file)
        assert spec.version == 2
        assert spec.name == "test_pipeline"
        assert spec.description == "A test pipeline with DAG"
        assert len(spec.steps) == 2
        assert len(spec.triggers) == 1
        assert spec.ref_layers == {"zones": "data/zones.gpkg"}

    def test_v2_is_dag(self, v2_pipeline_file):
        spec = load_pipeline(v2_pipeline_file)
        assert spec.is_dag

    def test_v2_step_input_ref(self, v2_pipeline_file):
        spec = load_pipeline(v2_pipeline_file)
        assert spec.steps[0].input is None
        assert spec.steps[1].input == "filter_big"

    def test_v2_trigger_parsing(self, v2_pipeline_file):
        spec = load_pipeline(v2_pipeline_file)
        t = spec.triggers[0]
        assert t.on == "dml:parcelles:INSERT,UPDATE"
        assert t.then == "run_pipeline"
        assert len(t.when) == 1
        assert isinstance(t.when[0], AttrPredicate)
        assert t.when[0].field == "value"
        assert t.when[0].op == "gt"

    def test_v2_with_when_predicate(self, tmp_path):
        data = {
            "version": 2,
            "name": "conditional",
            "steps": [
                {
                    "id": "s1",
                    "type": "capability",
                    "capability": "buffer",
                    "params": {"distance": 50},
                    "when": {"type": "attr", "field": "zone", "op": "eq", "value": "urban"},
                }
            ],
        }
        path = tmp_path / "cond.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        spec = load_pipeline(path)
        assert spec.steps[0].when is not None
        assert isinstance(spec.steps[0].when, AttrPredicate)
        assert spec.steps[0].when.value == "urban"


class TestLoadPipelineErrors:
    """Test error handling in pipeline loader."""

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_pipeline("/nonexistent/pipeline.json")

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_pipeline(path)

    def test_invalid_type(self, tmp_path):
        path = tmp_path / "str.json"
        path.write_text('"just a string"', encoding="utf-8")
        with pytest.raises(ValueError, match="JSON object.*or array"):
            load_pipeline(path)


# ===========================================================================
# Predicate parsing
# ===========================================================================


class TestPredicateParsing:
    def test_attr_predicate(self):
        pred = _parse_predicate({"type": "attr", "field": "area", "op": "gt", "value": 100})
        assert isinstance(pred, AttrPredicate)
        assert pred.field == "area"
        assert pred.op == "gt"

    def test_geom_predicate(self):
        pred = _parse_predicate({
            "type": "geom",
            "op": "intersects",
            "ref_table": "zones",
            "buffer_m": 50,
        })
        assert isinstance(pred, GeomPredicate)
        assert pred.op == "intersects"
        assert pred.buffer_m == 50

    def test_compound_predicate(self):
        pred = _parse_predicate({
            "type": "compound",
            "logic": "AND",
            "predicates": [
                {"type": "attr", "field": "x", "op": "gt", "value": 0},
                {"type": "attr", "field": "y", "op": "lt", "value": 100},
            ],
        })
        assert isinstance(pred, CompoundPredicate)
        assert pred.logic == "AND"
        assert len(pred.predicates) == 2

    def test_unknown_predicate_type(self):
        with pytest.raises(ValueError, match="Unknown predicate type"):
            _parse_predicate({"type": "unknown"})


# ===========================================================================
# Serialization roundtrip
# ===========================================================================


class TestPipelineSerialization:
    def test_roundtrip_v2(self, v2_pipeline_file):
        spec = load_pipeline(v2_pipeline_file)
        d = pipeline_to_dict(spec)
        assert d["version"] == 2
        assert d["name"] == "test_pipeline"
        assert len(d["steps"]) == 2
        assert d["steps"][1]["input"] == "filter_big"
        assert len(d["triggers"]) == 1
        assert d["ref_layers"] == {"zones": "data/zones.gpkg"}

    def test_roundtrip_preserves_capability(self):
        spec = PipelineSpec(
            name="test",
            steps=[
                StepSpec(id="s1", capability="buffer", params={"distance": 50}),
            ],
        )
        d = pipeline_to_dict(spec)
        assert d["steps"][0]["capability"] == "buffer"
        assert d["steps"][0]["params"] == {"distance": 50}


# ===========================================================================
# PipelineSpec properties
# ===========================================================================


class TestPipelineSpecProperties:
    def test_is_dag_false_for_linear(self):
        spec = PipelineSpec(steps=[
            StepSpec(id="a", capability="filter"),
            StepSpec(id="b", capability="buffer"),
        ])
        assert not spec.is_dag

    def test_is_dag_true_when_input_ref(self):
        spec = PipelineSpec(steps=[
            StepSpec(id="a", capability="filter"),
            StepSpec(id="b", capability="buffer", input="a"),
        ])
        assert spec.is_dag

    def test_enabled_steps_sorted_by_order(self):
        spec = PipelineSpec(steps=[
            StepSpec(id="b", capability="buffer", order=2),
            StepSpec(id="a", capability="filter", order=1),
            StepSpec(id="c", capability="dissolve", order=3, enabled=False),
        ])
        enabled = spec.enabled_steps
        assert len(enabled) == 2
        assert [s.id for s in enabled] == ["a", "b"]


# ===========================================================================
# PipelineExecutor — linear mode
# ===========================================================================


class TestPipelineExecutorLinear:
    def test_linear_filter_then_buffer(self, sample_gdf):
        spec = PipelineSpec(steps=[
            StepSpec(id="filter", capability="filter", params={"expression": "value > 50"}, order=0),
            StepSpec(id="buffer", capability="buffer", params={"distance": 100}, order=1),
        ])
        executor = PipelineExecutor()
        results = executor.execute(spec, {"input": sample_gdf})
        assert "filter" in results
        assert "buffer" in results
        assert len(results["filter"]) == 2  # D=75, E=100
        assert len(results["buffer"]) == 2
        # Buffer should produce polygons
        assert results["buffer"].geometry.iloc[0].geom_type == "Polygon"

    def test_linear_disabled_step_skipped(self, sample_gdf):
        spec = PipelineSpec(steps=[
            StepSpec(id="filter", capability="filter", params={"expression": "value > 50"}, order=0),
            StepSpec(id="skip", capability="buffer", params={"distance": 100}, enabled=False, order=1),
        ])
        executor = PipelineExecutor()
        results = executor.execute(spec, {"input": sample_gdf})
        assert "filter" in results
        assert "skip" not in results

    def test_linear_conditional_when_true(self, sample_gdf):
        """Step with when=True should execute."""
        spec = PipelineSpec(steps=[
            StepSpec(
                id="buf", capability="buffer", params={"distance": 50},
                when=AttrPredicate(field="value", op="gt", value=5),
            ),
        ])
        executor = PipelineExecutor()
        results = executor.execute(spec, {"input": sample_gdf})
        assert results["buf"].geometry.iloc[0].geom_type == "Polygon"

    def test_linear_conditional_when_false(self, sample_gdf):
        """Step with when=False should pass through unchanged."""
        spec = PipelineSpec(steps=[
            StepSpec(
                id="buf", capability="buffer", params={"distance": 50},
                when=AttrPredicate(field="value", op="gt", value=99999),
            ),
        ])
        executor = PipelineExecutor()
        results = executor.execute(spec, {"input": sample_gdf})
        # Should pass through — geometry remains Point
        assert results["buf"].geometry.iloc[0].geom_type == "Point"

    def test_linear_empty_gdf(self):
        """Pipeline with empty input should not crash."""
        empty = gpd.GeoDataFrame(
            {"value": [], "geometry": []},
            geometry="geometry",
            crs="EPSG:4326",
        )
        spec = PipelineSpec(steps=[
            StepSpec(id="buf", capability="buffer", params={"distance": 10}),
        ])
        executor = PipelineExecutor()
        results = executor.execute(spec, {"input": empty})
        assert len(results["buf"]) == 0


# ===========================================================================
# PipelineExecutor — DAG mode
# ===========================================================================


class TestPipelineExecutorDAG:
    def test_dag_filter_then_buffer(self, sample_gdf):
        spec = PipelineSpec(steps=[
            StepSpec(id="filter", capability="filter", params={"expression": "value > 50"}),
            StepSpec(id="buffer", capability="buffer", params={"distance": 100}, input="filter"),
        ])
        executor = PipelineExecutor()
        results = executor.execute(spec, {"input": sample_gdf})
        assert "filter" in results
        assert "buffer" in results
        assert len(results["buffer"]) == 2

    def test_dag_multi_step_chain(self, sample_gdf):
        spec = PipelineSpec(steps=[
            StepSpec(id="s1", capability="filter", params={"expression": "value > 20"}),
            StepSpec(id="s2", capability="buffer", params={"distance": 50}, input="s1"),
            StepSpec(id="s3", capability="centroid", params={}, input="s2"),
        ])
        executor = PipelineExecutor()
        results = executor.execute(spec, {"input": sample_gdf})
        assert len(results["s1"]) == 4  # B=25, C=50, D=75, E=100
        assert results["s2"].geometry.iloc[0].geom_type == "Polygon"
        assert results["s3"].geometry.iloc[0].geom_type == "Point"

    def test_dag_filter_ref_layer_then_use_as_ref(self, sample_gdf):
        """A step can pre-filter a ref layer, then a later step consumes that
        filtered subset via ``ref_layer``. Mirrors the flood-risk scenario:
        filter ``cours_eau`` by toponym first, then use the subset as the
        spatial reference for the building filter."""
        from shapely.geometry import LineString
        # Two "rivers": A near y=0 axis, B far away at y=100
        rivers = gpd.GeoDataFrame(
            {
                "name": ["A", "B"],
                "geometry": [
                    LineString([(-10.0, 0.0), (10.0, 0.0)]),
                    LineString([(-10.0, 100.0), (10.0, 100.0)]),
                ],
            },
            crs="EPSG:4326",
        )

        spec = PipelineSpec(
            steps=[
                # Pre-filter the ref layer on attribute (no spatial)
                StepSpec(
                    id="keep_river_a",
                    capability="filter",
                    params={"expression": "name == 'A'"},
                    input="rivers",
                ),
                # Spatial filter of buildings using the filtered rivers
                StepSpec(
                    id="near_river_a",
                    capability="filter",
                    params={
                        "spatial_predicate": "intersects",
                        "ref_layer": "keep_river_a",
                        "buffer_distance": 2.0,
                        "crs_meters": "EPSG:4326",
                    },
                ),
            ],
        )
        executor = PipelineExecutor()
        results = executor.execute(spec, {"input": sample_gdf, "rivers": rivers})
        # A-river runs along y=0 with a 2° buffer → only points with y<=2 match.
        # sample_gdf points: (0,0),(1,1),(2,2),(3,3),(4,4) → (0,0),(1,1),(2,2)
        assert len(results["keep_river_a"]) == 1
        assert len(results["near_river_a"]) == 3


# ===========================================================================
# Integration: load v1 file → execute
# ===========================================================================


class TestPipelineE2E:
    def test_load_existing_v1_and_execute(self, sample_gdf):
        """Load an existing v1 example file and execute it."""
        spec = load_pipeline("examples/buffer_30m.json")
        executor = PipelineExecutor()
        results = executor.execute(spec, {"input": sample_gdf})
        assert "buffer_30m" in results
        assert results["buffer_30m"].geometry.iloc[0].geom_type == "Polygon"


class TestPipelineExecutorValidation:
    """Closure of the validation-bypass bug (audit 2026-04-16)."""

    def test_linear_step_missing_required_param_raises(self, sample_gdf):
        # reproject requires target_crs — omit it to trigger validation failure
        spec = PipelineSpec(
            version=2,
            name="bad",
            steps=[StepSpec(id="s1", type="capability", capability="reproject", params={})],
        )
        executor = PipelineExecutor()
        with pytest.raises(ValueError, match="invalid params"):
            executor.execute(spec, {"input": sample_gdf})

    def test_linear_step_wrong_param_type_raises(self, sample_gdf):
        spec = PipelineSpec(
            version=2,
            name="bad",
            steps=[
                StepSpec(
                    id="s1",
                    type="capability",
                    capability="reproject",
                    params={"target_crs": 4326},  # int instead of string
                ),
            ],
        )
        executor = PipelineExecutor()
        with pytest.raises(ValueError, match="invalid params"):
            executor.execute(spec, {"input": sample_gdf})

    def test_dag_step_missing_required_param_raises(self, sample_gdf):
        spec = PipelineSpec(
            version=2,
            name="bad_dag",
            steps=[
                StepSpec(
                    id="s1",
                    type="capability",
                    capability="filter",
                    params={"expression": "value > 10"},
                ),
                StepSpec(
                    id="s2",
                    type="capability",
                    capability="reproject",
                    params={},
                    input="s1",
                ),
            ],
        )
        executor = PipelineExecutor()
        with pytest.raises(ValueError, match="invalid params"):
            executor.execute(spec, {"input": sample_gdf})
