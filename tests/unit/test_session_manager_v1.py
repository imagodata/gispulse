"""Tests for SessionManager.run_pipeline and run_pipeline_multi (v1/legacy paths).

test_session_manager_v2 (10 tests) covers run_pipeline_v2 (PipelineSpec-based).
These tests cover the Rule-based run_pipeline + multi-layer execution path,
the _export helper, engine_mode validation, and ref_sources loading.
"""
from __future__ import annotations


import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.core.models import Rule
from gispulse.orchestration.session_manager import (
    MultiLayerResult,
    PipelineResult,
    SessionManager,
)


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "value": [10, 20, 30, 40, 50],
            "geometry": [Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3), Point(4, 4)],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def input_gpkg(tmp_path, sample_gdf) -> str:
    path = tmp_path / "input.gpkg"
    sample_gdf.to_file(path, layer="points", driver="GPKG")
    return str(path)


@pytest.fixture
def filter_rule() -> Rule:
    """Simple filter rule: keep features with value > 20."""
    return Rule(
        name="filter_high",
        capability="filter",
        config={"expression": "value > 20"},
        enabled=True,
    )


# ---------------------------------------------------------------------------
# SessionManager constructor
# ---------------------------------------------------------------------------


class TestSessionManagerInit:
    def test_default_engine_is_python(self):
        sm = SessionManager()
        assert sm.engine_mode == "python"

    def test_explicit_python_engine(self):
        sm = SessionManager(engine="python")
        assert sm.engine_mode == "python"

    def test_explicit_duckdb_engine(self):
        sm = SessionManager(engine="duckdb")
        assert sm.engine_mode == "duckdb"

    def test_invalid_engine_raises(self):
        with pytest.raises(ValueError, match="Unknown engine"):
            SessionManager(engine="sqlite")

    def test_workspace_id_is_generated(self):
        sm1 = SessionManager()
        sm2 = SessionManager()
        assert sm1._workspace_id != sm2._workspace_id
        assert sm1._workspace_id.startswith("session-")


# ---------------------------------------------------------------------------
# run_pipeline (v1 — list of Rules)
# ---------------------------------------------------------------------------


class TestRunPipelineV1:
    def test_executes_filter_rule(self, input_gpkg, filter_rule, tmp_path):
        sm = SessionManager()
        result = sm.run_pipeline(
            input_path=input_gpkg,
            rules=[filter_rule],
            layer="points",
        )
        assert isinstance(result, PipelineResult)
        assert result.features_in == 5
        assert result.features_out == 3  # value > 20 → 30, 40, 50
        assert result.rules_applied == 1

    def test_writes_output_when_path_given(self, input_gpkg, filter_rule, tmp_path):
        sm = SessionManager()
        out = tmp_path / "result.gpkg"
        result = sm.run_pipeline(
            input_path=input_gpkg,
            rules=[filter_rule],
            output_path=out,
            layer="points",
        )
        assert out.exists()
        assert result.output_path == str(out)

    def test_no_rules_returns_input_unchanged(self, input_gpkg):
        sm = SessionManager()
        result = sm.run_pipeline(
            input_path=input_gpkg, rules=[], layer="points"
        )
        assert result.features_in == 5
        assert result.features_out == 5
        assert result.rules_applied == 0

    def test_disabled_rule_skipped(self, input_gpkg, filter_rule):
        disabled = Rule(
            name="disabled",
            capability="filter",
            config={"expression": "value > 1000"},
            enabled=False,
        )
        sm = SessionManager()
        result = sm.run_pipeline(
            input_path=input_gpkg,
            rules=[disabled],
            layer="points",
        )
        # Disabled rule ignored → passthrough
        assert result.features_out == 5

    def test_engine_mode_recorded_in_result(self, input_gpkg, filter_rule):
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=input_gpkg, rules=[filter_rule], layer="points"
        )
        assert result.engine_used == "python"

    def test_crs_parameter_applied(self, tmp_path, sample_gdf):
        """When input has no CRS and crs= is provided, it's applied on load."""
        # Write a GPKG without CRS
        nocrs = sample_gdf.copy()
        nocrs = nocrs.set_crs(None, allow_override=True)
        path = tmp_path / "nocrs.gpkg"
        nocrs.to_file(path, layer="pts", driver="GPKG")

        sm = SessionManager()
        result = sm.run_pipeline(
            input_path=str(path), rules=[], layer="pts", crs="EPSG:4326"
        )
        assert result.features_in == 5


# ---------------------------------------------------------------------------
# _export helper (static method)
# ---------------------------------------------------------------------------


class TestExport:
    def test_none_output_returns_none(self, sample_gdf, tmp_path):
        result = SessionManager._export(
            input_path=tmp_path / "in.gpkg",
            output_path=None,
            output_layer=None,
            result_gdf=sample_gdf,
        )
        assert result is None

    def test_writes_output_and_returns_path(self, sample_gdf, tmp_path):
        out = tmp_path / "out.gpkg"
        result = SessionManager._export(
            input_path=tmp_path / "in.gpkg",
            output_path=out,
            output_layer="result",
            result_gdf=sample_gdf,
        )
        assert result == str(out)
        assert out.exists()

    def test_creates_parent_dir(self, sample_gdf, tmp_path):
        out = tmp_path / "nested" / "dir" / "out.gpkg"
        SessionManager._export(
            input_path=tmp_path / "in.gpkg",
            output_path=out,
            output_layer=None,
            result_gdf=sample_gdf,
        )
        assert out.exists()


# ---------------------------------------------------------------------------
# run_pipeline_multi — multi-layer execution
# ---------------------------------------------------------------------------


@pytest.fixture
def multi_layer_gpkg(tmp_path, sample_gdf) -> str:
    """GPKG with 2 layers: 'points' and 'others'."""
    path = tmp_path / "multi.gpkg"
    sample_gdf.to_file(path, layer="points", driver="GPKG")
    sample_gdf.iloc[:3].to_file(path, layer="others", driver="GPKG", mode="a")
    return str(path)


class TestRunPipelineMulti:
    def test_processes_all_layers(self, multi_layer_gpkg, filter_rule):
        sm = SessionManager()
        result = sm.run_pipeline_multi(
            input_path=multi_layer_gpkg,
            rules=[filter_rule],
        )
        assert isinstance(result, MultiLayerResult)
        assert "points" in result.layers
        assert "others" in result.layers
        # points (5 rows, value>20 → 3), others (3 rows, value>20 → 1)
        assert result.total_features_in == 8
        assert result.total_features_out == 4

    def test_per_layer_results_populated(self, multi_layer_gpkg, filter_rule):
        sm = SessionManager()
        result = sm.run_pipeline_multi(
            input_path=multi_layer_gpkg,
            rules=[filter_rule],
        )
        assert len(result.layer_results) == 2
        pts_result = result.layer_results["points"]
        assert isinstance(pts_result, PipelineResult)
        assert pts_result.features_in == 5
        assert pts_result.features_out == 3

    def test_target_layer_filtering(self, multi_layer_gpkg):
        """A rule with target_layer should only apply to that layer."""
        targeted = Rule(
            name="pts_only",
            capability="filter",
            config={"expression": "value > 20", "target_layer": "points"},
            enabled=True,
        )
        sm = SessionManager()
        result = sm.run_pipeline_multi(
            input_path=multi_layer_gpkg,
            rules=[targeted],
        )
        # points filtered, others unchanged
        assert result.layers["points"].shape[0] == 3
        assert result.layers["others"].shape[0] == 3  # passthrough

    def test_writes_output_when_path_given(
        self, multi_layer_gpkg, filter_rule, tmp_path
    ):
        sm = SessionManager()
        out = tmp_path / "out.gpkg"
        result = sm.run_pipeline_multi(
            input_path=multi_layer_gpkg,
            rules=[filter_rule],
            output_path=out,
        )
        assert out.exists()
        assert result.output_path == str(out)

    def test_no_output_path_leaves_files_unwritten(
        self, multi_layer_gpkg, filter_rule
    ):
        sm = SessionManager()
        result = sm.run_pipeline_multi(
            input_path=multi_layer_gpkg,
            rules=[filter_rule],
            output_path=None,
        )
        assert result.output_path is None


# ---------------------------------------------------------------------------
# Dataclass defaults
# ---------------------------------------------------------------------------


class TestResultDataclasses:
    def test_pipeline_result_defaults(self):
        import geopandas as gpd
        empty = gpd.GeoDataFrame()
        r = PipelineResult(gdf=empty)
        assert r.output_path is None
        assert r.rules_applied == 0
        assert r.features_in == 0
        assert r.features_out == 0
        assert r.engine_used == "python"
        assert r.layers_loaded == []

    def test_multi_layer_result_defaults(self):
        r = MultiLayerResult(layers={})
        assert r.output_path is None
        assert r.rules_applied == 0
        assert r.total_features_in == 0
        assert r.engine_used == "python"
        assert r.layer_results == {}
        assert r.styles_copied == 0
