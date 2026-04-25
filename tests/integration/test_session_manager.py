"""
Comprehensive E2E tests for SessionManager and DuckDB engine path.

Covers:
- Python mode: filter, buffer, multi-rule pipeline, cross-layer, empty result
- DuckDB mode: same scenarios with engine="duckdb"
- Output export: with/without output_path, GPKG and GeoJSON
- PipelineResult metadata: features_in/out, rules_applied, engine_used, layers_loaded
- CLI --engine flag: python, duckdb, invalid value
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, box
from typer.testing import CliRunner

from gispulse.cli import app
from core.models import Rule
from orchestration.session_manager import PipelineResult, SessionManager

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_gpkg(tmp_path: Path) -> Path:
    """Four-point GPKG with name and area attributes."""
    gdf = gpd.GeoDataFrame(
        {"name": ["A", "B", "C", "D"], "area": [50, 150, 200, 10]},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3)],
        crs="EPSG:4326",
    )
    path = tmp_path / "input.gpkg"
    gdf.to_file(str(path), layer="data", driver="GPKG")
    return path


@pytest.fixture
def multi_layer_gpkg(tmp_path: Path) -> Path:
    """Two-layer GPKG: parcels (boxes) + zones (overlap reference)."""
    parcels = gpd.GeoDataFrame(
        {"name": ["A", "B"], "zone": ["green", "industrial"]},
        geometry=[box(0, 0, 1, 1), box(2, 2, 3, 3)],
        crs="EPSG:4326",
    )
    zones = gpd.GeoDataFrame(
        {"risk": ["high"]},
        geometry=[box(0.5, 0.5, 1.5, 1.5)],
        crs="EPSG:4326",
    )
    path = tmp_path / "multi.gpkg"
    parcels.to_file(str(path), layer="parcels", driver="GPKG")
    zones.to_file(str(path), layer="zones", driver="GPKG", mode="a")
    return path


@pytest.fixture
def filter_rules_list() -> list[Rule]:
    """Rule objects: filter area > 100 (keeps B=150 and C=200)."""
    return [
        Rule(
            name="filter_large",
            capability="filter",
            config={"expression": "area > 100", "order": 0},
            enabled=True,
        )
    ]


@pytest.fixture
def buffer_rules_list() -> list[Rule]:
    """Rule objects: buffer 1 degree (in EPSG:4326 units)."""
    return [
        Rule(
            name="buffer_1deg",
            capability="buffer",
            config={"distance": 1, "order": 0},
            enabled=True,
        )
    ]


@pytest.fixture
def filter_then_buffer_rules() -> list[Rule]:
    """Two-rule pipeline: filter first, then buffer."""
    return [
        Rule(
            name="filter_large",
            capability="filter",
            config={"expression": "area > 100", "order": 0},
            enabled=True,
        ),
        Rule(
            name="buffer_1deg",
            capability="buffer",
            config={"distance": 1, "order": 1},
            enabled=True,
        ),
    ]


@pytest.fixture
def cross_layer_rules() -> list[Rule]:
    """Rule objects: intersects with ref_layer=zones."""
    return [
        Rule(
            name="intersect_zones",
            capability="intersects",
            config={"ref_layer": "zones", "order": 0},
            enabled=True,
        )
    ]


@pytest.fixture
def no_match_rules() -> list[Rule]:
    """Filter rule that matches nothing (area > 99999)."""
    return [
        Rule(
            name="filter_nothing",
            capability="filter",
            config={"expression": "area > 99999", "order": 0},
            enabled=True,
        )
    ]


# ---------------------------------------------------------------------------
# JSON rule-file fixtures (for CLI tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def filter_rules(tmp_path: Path) -> Path:
    rules = [{"name": "filter_large", "capability": "filter", "config": {"expression": "area > 100"}}]
    path = tmp_path / "filter_rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


@pytest.fixture
def buffer_rules(tmp_path: Path) -> Path:
    rules = [{"name": "buffer_1deg", "capability": "buffer", "config": {"distance": 1}}]
    path = tmp_path / "buffer_rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. SessionManager — Python mode
# ---------------------------------------------------------------------------


class TestSessionManagerPythonMode:
    """Python-only mode (engine='python'): no DuckDB dependency."""

    def test_filter_rule_reduces_features(
        self, sample_gpkg: Path, filter_rules_list: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_rules_list,
            layer="data",
        )
        assert isinstance(result, PipelineResult)
        assert len(result.gdf) == 2
        assert set(result.gdf["name"]) == {"B", "C"}

    def test_buffer_rule_changes_geometry_type(
        self, sample_gpkg: Path, buffer_rules_list: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=buffer_rules_list,
            layer="data",
        )
        assert len(result.gdf) == 4
        assert all(g.geom_type == "Polygon" for g in result.gdf.geometry)

    def test_multi_rule_pipeline_filter_then_buffer(
        self, sample_gpkg: Path, filter_then_buffer_rules: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_then_buffer_rules,
            layer="data",
        )
        # filter keeps B and C, then buffer converts them to polygons
        assert len(result.gdf) == 2
        assert all(g.geom_type == "Polygon" for g in result.gdf.geometry)
        assert set(result.gdf["name"]) == {"B", "C"}

    def test_cross_layer_intersects(
        self, multi_layer_gpkg: Path, cross_layer_rules: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=multi_layer_gpkg,
            rules=cross_layer_rules,
            layer="parcels",
        )
        # Only parcel A intersects zones (box(0.5,0.5,1.5,1.5) overlaps box(0,0,1,1))
        assert len(result.gdf) == 1
        assert result.gdf.iloc[0]["name"] == "A"

    def test_empty_result_when_filter_matches_nothing(
        self, sample_gpkg: Path, no_match_rules: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=no_match_rules,
            layer="data",
        )
        assert len(result.gdf) == 0
        assert result.features_out == 0

    def test_disabled_rules_are_skipped(self, sample_gpkg: Path) -> None:
        rules = [
            Rule(
                name="filter_disabled",
                capability="filter",
                config={"expression": "area > 100", "order": 0},
                enabled=False,
            )
        ]
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(input_path=sample_gpkg, rules=rules, layer="data")
        # Disabled rule must not run, all 4 features pass through
        assert len(result.gdf) == 4


# ---------------------------------------------------------------------------
# 2. SessionManager — DuckDB mode
# ---------------------------------------------------------------------------


class TestSessionManagerDuckDBMode:
    """DuckDB-accelerated mode (engine='duckdb').

    Results must be equivalent to Python mode for the same inputs.
    """

    def test_filter_rule_reduces_features(
        self, sample_gpkg: Path, filter_rules_list: list[Rule]
    ) -> None:
        sm = SessionManager(engine="duckdb")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_rules_list,
            layer="data",
        )
        assert len(result.gdf) == 2
        assert set(result.gdf["name"]) == {"B", "C"}

    def test_buffer_rule_changes_geometry_type(
        self, sample_gpkg: Path, buffer_rules_list: list[Rule]
    ) -> None:
        sm = SessionManager(engine="duckdb")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=buffer_rules_list,
            layer="data",
        )
        assert len(result.gdf) == 4
        assert all(g.geom_type == "Polygon" for g in result.gdf.geometry)

    def test_multi_rule_pipeline_filter_then_buffer(
        self, sample_gpkg: Path, filter_then_buffer_rules: list[Rule]
    ) -> None:
        sm = SessionManager(engine="duckdb")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_then_buffer_rules,
            layer="data",
        )
        assert len(result.gdf) == 2
        assert all(g.geom_type == "Polygon" for g in result.gdf.geometry)

    def test_cross_layer_intersects(
        self, multi_layer_gpkg: Path, cross_layer_rules: list[Rule]
    ) -> None:
        sm = SessionManager(engine="duckdb")
        result = sm.run_pipeline(
            input_path=multi_layer_gpkg,
            rules=cross_layer_rules,
            layer="parcels",
        )
        assert len(result.gdf) == 1
        assert result.gdf.iloc[0]["name"] == "A"

    def test_empty_result_when_filter_matches_nothing(
        self, sample_gpkg: Path, no_match_rules: list[Rule]
    ) -> None:
        sm = SessionManager(engine="duckdb")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=no_match_rules,
            layer="data",
        )
        assert len(result.gdf) == 0
        assert result.features_out == 0

    def test_duckdb_session_created_and_cleaned_up(
        self, sample_gpkg: Path, filter_rules_list: list[Rule], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify DuckDB session lifecycle: open() and close() are both called."""
        from persistence import duckdb_engine as duckdb_mod

        open_calls: list[str] = []
        close_calls: list[str] = []

        original_open = duckdb_mod.DuckDBSession.open
        original_close = duckdb_mod.DuckDBSession.close

        def patched_open(self_inner: duckdb_mod.DuckDBSession) -> None:
            open_calls.append("open")
            original_open(self_inner)

        def patched_close(self_inner: duckdb_mod.DuckDBSession) -> None:
            close_calls.append("close")
            original_close(self_inner)

        monkeypatch.setattr(duckdb_mod.DuckDBSession, "open", patched_open)
        monkeypatch.setattr(duckdb_mod.DuckDBSession, "close", patched_close)

        sm = SessionManager(engine="duckdb")
        sm.run_pipeline(input_path=sample_gpkg, rules=filter_rules_list, layer="data")

        assert open_calls == ["open"], "DuckDB session must be opened exactly once"
        assert close_calls == ["close"], "DuckDB session must be closed exactly once (even on success)"

    def test_duckdb_session_closed_on_exception(
        self, sample_gpkg: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify DuckDB session is closed even when rule application raises."""
        from persistence import duckdb_engine as duckdb_mod
        from rules import engine as engine_mod

        close_calls: list[str] = []
        original_close = duckdb_mod.DuckDBSession.close

        def patched_close(self_inner: duckdb_mod.DuckDBSession) -> None:
            close_calls.append("close")
            original_close(self_inner)

        def exploding_apply_all(*args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated rule failure")

        monkeypatch.setattr(duckdb_mod.DuckDBSession, "close", patched_close)
        monkeypatch.setattr(engine_mod.RuleEngine, "apply_all", exploding_apply_all)

        broken_rule = [
            Rule(name="boom", capability="filter", config={"expression": "True"}, enabled=True)
        ]
        sm = SessionManager(engine="duckdb")
        with pytest.raises(RuntimeError, match="simulated rule failure"):
            sm.run_pipeline(input_path=sample_gpkg, rules=broken_rule, layer="data")

        assert close_calls == ["close"], "DuckDB session must be closed even when an exception is raised"

    def test_python_and_duckdb_produce_equivalent_results(
        self, sample_gpkg: Path, filter_rules_list: list[Rule]
    ) -> None:
        """Filter output must be identical across engines."""
        py_result = SessionManager(engine="python").run_pipeline(
            input_path=sample_gpkg, rules=filter_rules_list, layer="data"
        )
        duck_result = SessionManager(engine="duckdb").run_pipeline(
            input_path=sample_gpkg, rules=filter_rules_list, layer="data"
        )
        assert len(py_result.gdf) == len(duck_result.gdf)
        assert set(py_result.gdf["name"]) == set(duck_result.gdf["name"])


# ---------------------------------------------------------------------------
# 3. SessionManager — output export
# ---------------------------------------------------------------------------


class TestSessionManagerOutputExport:
    """Output path handling and multi-format export."""

    def test_with_output_path_writes_gpkg(
        self, sample_gpkg: Path, filter_rules_list: list[Rule], tmp_path: Path
    ) -> None:
        output = tmp_path / "result.gpkg"
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_rules_list,
            output_path=output,
            layer="data",
        )
        assert output.exists(), "Output GPKG file must be created"
        assert result.output_path == str(output)
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2

    def test_without_output_path_returns_gdf_no_file(
        self, sample_gpkg: Path, filter_rules_list: list[Rule], tmp_path: Path
    ) -> None:
        expected_output = tmp_path / "result.gpkg"
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_rules_list,
            layer="data",
        )
        assert result.output_path is None
        assert isinstance(result.gdf, gpd.GeoDataFrame)
        assert len(result.gdf) == 2
        # No output file should have been written (input.gpkg in tmp_path is the fixture file)
        assert not expected_output.exists(), "SessionManager must not write a file when output_path is None"

    def test_output_geojson(
        self, sample_gpkg: Path, filter_rules_list: list[Rule], tmp_path: Path
    ) -> None:
        output = tmp_path / "result.geojson"
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_rules_list,
            output_path=output,
            layer="data",
        )
        assert output.exists()
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2
        assert result.output_path == str(output)

    def test_output_creates_parent_dirs(
        self, sample_gpkg: Path, filter_rules_list: list[Rule], tmp_path: Path
    ) -> None:
        output = tmp_path / "nested" / "deep" / "result.gpkg"
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_rules_list,
            output_path=output,
            layer="data",
        )
        assert output.exists(), "Nested output path must be created automatically"

    def test_output_layer_name_used(
        self, sample_gpkg: Path, filter_rules_list: list[Rule], tmp_path: Path
    ) -> None:
        output = tmp_path / "result.gpkg"
        sm = SessionManager(engine="python")
        sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_rules_list,
            output_path=output,
            layer="data",
            output_layer="filtered_parcels",
        )
        import fiona
        layers = fiona.listlayers(str(output))
        assert "filtered_parcels" in layers

    def test_duckdb_output_gpkg(
        self, sample_gpkg: Path, filter_rules_list: list[Rule], tmp_path: Path
    ) -> None:
        output = tmp_path / "duck_result.gpkg"
        sm = SessionManager(engine="duckdb")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_rules_list,
            output_path=output,
            layer="data",
        )
        assert output.exists()
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2

    def test_duckdb_output_geojson(
        self, sample_gpkg: Path, buffer_rules_list: list[Rule], tmp_path: Path
    ) -> None:
        output = tmp_path / "duck_result.geojson"
        sm = SessionManager(engine="duckdb")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=buffer_rules_list,
            output_path=output,
            layer="data",
        )
        assert output.exists()
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 4
        assert all(g.geom_type == "Polygon" for g in gdf.geometry)


# ---------------------------------------------------------------------------
# 4. PipelineResult metadata
# ---------------------------------------------------------------------------


class TestPipelineResultMetadata:
    """Verify all metadata fields on PipelineResult are accurate."""

    def test_features_in_reflects_input_count(
        self, sample_gpkg: Path, filter_rules_list: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg, rules=filter_rules_list, layer="data"
        )
        assert result.features_in == 4

    def test_features_out_reflects_output_count(
        self, sample_gpkg: Path, filter_rules_list: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg, rules=filter_rules_list, layer="data"
        )
        assert result.features_out == 2

    def test_rules_applied_counts_enabled_rules(
        self, sample_gpkg: Path, filter_then_buffer_rules: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg, rules=filter_then_buffer_rules, layer="data"
        )
        assert result.rules_applied == 2

    def test_rules_applied_excludes_disabled_rules(self, sample_gpkg: Path) -> None:
        rules = [
            Rule(
                name="filter_enabled",
                capability="filter",
                config={"expression": "area > 100", "order": 0},
                enabled=True,
            ),
            Rule(
                name="buffer_disabled",
                capability="buffer",
                config={"distance": 1, "order": 1},
                enabled=False,
            ),
        ]
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(input_path=sample_gpkg, rules=rules, layer="data")
        assert result.rules_applied == 1

    def test_engine_used_python(
        self, sample_gpkg: Path, filter_rules_list: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg, rules=filter_rules_list, layer="data"
        )
        assert result.engine_used == "python"

    def test_engine_used_duckdb(
        self, sample_gpkg: Path, filter_rules_list: list[Rule]
    ) -> None:
        sm = SessionManager(engine="duckdb")
        result = sm.run_pipeline(
            input_path=sample_gpkg, rules=filter_rules_list, layer="data"
        )
        assert result.engine_used == "duckdb"

    def test_layers_loaded_contains_primary_layer(
        self, sample_gpkg: Path, filter_rules_list: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg, rules=filter_rules_list, layer="data"
        )
        assert "data" in result.layers_loaded

    def test_layers_loaded_contains_ref_layer_for_cross_layer_op(
        self, multi_layer_gpkg: Path, cross_layer_rules: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=multi_layer_gpkg, rules=cross_layer_rules, layer="parcels"
        )
        assert "parcels" in result.layers_loaded
        assert "zones" in result.layers_loaded

    def test_output_path_none_when_not_specified(
        self, sample_gpkg: Path, filter_rules_list: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg, rules=filter_rules_list, layer="data"
        )
        assert result.output_path is None

    def test_output_path_set_when_specified(
        self, sample_gpkg: Path, filter_rules_list: list[Rule], tmp_path: Path
    ) -> None:
        output = tmp_path / "out.gpkg"
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg,
            rules=filter_rules_list,
            output_path=output,
            layer="data",
        )
        assert result.output_path == str(output)

    def test_empty_result_metadata_consistency(
        self, sample_gpkg: Path, no_match_rules: list[Rule]
    ) -> None:
        sm = SessionManager(engine="python")
        result = sm.run_pipeline(
            input_path=sample_gpkg, rules=no_match_rules, layer="data"
        )
        assert result.features_in == 4
        assert result.features_out == 0
        assert result.rules_applied == 1


# ---------------------------------------------------------------------------
# 5. SessionManager constructor validation
# ---------------------------------------------------------------------------


class TestSessionManagerConstructor:
    def test_invalid_engine_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown engine"):
            SessionManager(engine="invalid")

    def test_invalid_engine_name_mentioned_in_error(self) -> None:
        with pytest.raises(ValueError, match="postgis"):
            SessionManager(engine="postgis")

    def test_engine_mode_property_python(self) -> None:
        sm = SessionManager(engine="python")
        assert sm.engine_mode == "python"

    def test_engine_mode_property_duckdb(self) -> None:
        sm = SessionManager(engine="duckdb")
        assert sm.engine_mode == "duckdb"

    def test_default_engine_is_python(self) -> None:
        sm = SessionManager()
        assert sm.engine_mode == "python"


# ---------------------------------------------------------------------------
# 6. CLI --engine flag
# ---------------------------------------------------------------------------


class TestCLIEngineFlag:
    """Test the --engine / -e CLI option."""

    def test_engine_python_is_default(
        self, sample_gpkg: Path, filter_rules: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "out.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(output),
            "--layer", "data",
        ])
        assert result.exit_code == 0, result.output
        assert "engine: python" in result.output.lower()

    def test_engine_python_explicit(
        self, sample_gpkg: Path, filter_rules: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "out.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(output),
            "--layer", "data",
            "--engine", "python",
        ])
        assert result.exit_code == 0, result.output
        assert output.exists()
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2

    def test_engine_python_short_flag(
        self, sample_gpkg: Path, filter_rules: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "out.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(output),
            "--layer", "data",
            "-e", "python",
        ])
        assert result.exit_code == 0, result.output
        assert output.exists()

    def test_engine_duckdb_flag(
        self, sample_gpkg: Path, filter_rules: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "out.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(output),
            "--layer", "data",
            "--engine", "duckdb",
        ])
        assert result.exit_code == 0, result.output
        assert output.exists()
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 2

    def test_engine_duckdb_reported_in_output(
        self, sample_gpkg: Path, filter_rules: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "out.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(output),
            "--layer", "data",
            "--engine", "duckdb",
        ])
        assert result.exit_code == 0, result.output
        assert "engine: duckdb" in result.output.lower()

    def test_engine_duckdb_and_python_produce_same_output(
        self, sample_gpkg: Path, filter_rules: Path, tmp_path: Path
    ) -> None:
        out_py = tmp_path / "py.gpkg"
        out_duck = tmp_path / "duck.gpkg"

        runner.invoke(app, [
            "run", str(sample_gpkg), "--rules", str(filter_rules),
            "--output", str(out_py), "--layer", "data", "--engine", "python",
        ])
        runner.invoke(app, [
            "run", str(sample_gpkg), "--rules", str(filter_rules),
            "--output", str(out_duck), "--layer", "data", "--engine", "duckdb",
        ])

        gdf_py = gpd.read_file(str(out_py))
        gdf_duck = gpd.read_file(str(out_duck))
        assert len(gdf_py) == len(gdf_duck)
        assert set(gdf_py["name"]) == set(gdf_duck["name"])

    def test_engine_invalid_exits_nonzero(
        self, sample_gpkg: Path, filter_rules: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "out.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(output),
            "--layer", "data",
            "--engine", "invalid",
        ])
        assert result.exit_code != 0

    def test_engine_invalid_error_message(
        self, sample_gpkg: Path, filter_rules: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "out.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(output),
            "--layer", "data",
            "--engine", "invalid",
        ])
        # Error message should mention the invalid engine name or the valid choices
        combined = (result.output or "") + (str(result.exception) if result.exception else "")
        assert "invalid" in combined.lower() or "engine" in combined.lower()

    def test_engine_postgis_exits_nonzero(
        self, sample_gpkg: Path, filter_rules: Path, tmp_path: Path
    ) -> None:
        """'postgis' is not a valid CLI engine choice (not yet wired)."""
        output = tmp_path / "out.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(filter_rules),
            "--output", str(output),
            "--layer", "data",
            "--engine", "postgis",
        ])
        assert result.exit_code != 0

    def test_buffer_pipeline_with_duckdb_engine(
        self, sample_gpkg: Path, buffer_rules: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "buffered.gpkg"
        result = runner.invoke(app, [
            "run", str(sample_gpkg),
            "--rules", str(buffer_rules),
            "--output", str(output),
            "--layer", "data",
            "--engine", "duckdb",
        ])
        assert result.exit_code == 0, result.output
        assert output.exists()
        gdf = gpd.read_file(str(output))
        assert len(gdf) == 4
        assert all(g.geom_type == "Polygon" for g in gdf.geometry)
