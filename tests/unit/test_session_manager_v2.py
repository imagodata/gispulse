"""Tests for SessionManager.run_pipeline_v2 (#405) and capability TypedDicts (#407)."""

from __future__ import annotations

import json
import pytest
import tempfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

from core.pipeline import PipelineSpec, StepSpec
from orchestration.session_manager import SessionManager


@pytest.fixture
def sample_gdf():
    """GeoDataFrame with 5 points and numeric values."""
    return gpd.GeoDataFrame(
        {
            "name": ["A", "B", "C", "D", "E"],
            "value": [10, 25, 50, 75, 100],
            "geometry": [Point(i, i) for i in range(5)],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def input_gpkg(sample_gdf, tmp_path):
    """Write sample GDF to a temporary GPKG and return the path."""
    path = tmp_path / "input.gpkg"
    sample_gdf.to_file(str(path), driver="GPKG")
    return path


# ---------------------------------------------------------------------------
# SessionManager.run_pipeline_v2
# ---------------------------------------------------------------------------


class TestRunPipelineV2:
    """#405 — SessionManager delegates to PipelineExecutor for v2."""

    def test_linear_pipeline(self, input_gpkg):
        """Linear v2 pipeline (filter) runs via PipelineExecutor."""
        spec = PipelineSpec(
            version=2,
            name="test_linear",
            steps=[
                StepSpec(id="filter", capability="filter",
                         params={"expression": "value > 30"}),
            ],
        )

        sm = SessionManager(engine="python")
        result = sm.run_pipeline_v2(
            input_path=input_gpkg,
            spec=spec,
        )

        assert result.features_in == 5
        assert result.features_out == 3  # D(75), C(50), E(100) — values > 30
        assert result.rules_applied == 1
        assert result.engine_used == "python"

    def test_linear_pipeline_with_output(self, input_gpkg, tmp_path):
        """v2 pipeline exports result to file."""
        output_path = tmp_path / "output.gpkg"
        spec = PipelineSpec(
            version=2,
            name="test_export",
            steps=[
                StepSpec(id="filter", capability="filter",
                         params={"expression": "value >= 50"}),
            ],
        )

        sm = SessionManager(engine="python")
        result = sm.run_pipeline_v2(
            input_path=input_gpkg,
            spec=spec,
            output_path=output_path,
        )

        assert result.output_path == str(output_path)
        assert output_path.exists()
        # Verify the output file
        out_gdf = gpd.read_file(str(output_path))
        assert len(out_gdf) == 3

    def test_dag_pipeline(self, input_gpkg):
        """DAG v2 pipeline (step references another)."""
        spec = PipelineSpec(
            version=2,
            name="test_dag",
            steps=[
                StepSpec(id="filter", capability="filter",
                         params={"expression": "value > 20"}),
                StepSpec(id="centroid", capability="centroid",
                         params={}, input="filter"),
            ],
        )

        assert spec.is_dag is True

        sm = SessionManager(engine="python")
        result = sm.run_pipeline_v2(
            input_path=input_gpkg,
            spec=spec,
        )

        assert result.features_in == 5
        # All features pass filter (value > 20: 25, 50, 75, 100)
        assert result.features_out == 4

    def test_empty_pipeline(self, input_gpkg):
        """Pipeline with no enabled steps returns original data."""
        spec = PipelineSpec(
            version=2,
            name="empty",
            steps=[
                StepSpec(id="disabled", capability="filter",
                         params={"expression": "value > 999"}, enabled=False),
            ],
        )

        sm = SessionManager(engine="python")
        result = sm.run_pipeline_v2(input_path=input_gpkg, spec=spec)

        assert result.features_in == 5
        assert result.features_out == 5


# ---------------------------------------------------------------------------
# #407 — TypedDict for capability params
# ---------------------------------------------------------------------------


class TestCapabilityTypedDicts:
    """Verify TypedDict definitions match capability schemas."""

    def test_imports(self):
        from core.capability_params import (
            BufferParams,
            FilterParams,
            SpatialJoinParams,
            DissolveParams,
            CentroidParams,
            ClipParams,
            AreaLengthParams,
            CalculateParams,
            IntersectsParams,
            ReprojectParams,
            PARAMS_TYPE_MAP,
        )
        assert len(PARAMS_TYPE_MAP) == 10

    def test_buffer_params_typing(self):
        from core.capability_params import BufferParams
        params: BufferParams = {"distance": 50.0}
        assert params["distance"] == 50.0

    def test_filter_params_typing(self):
        from core.capability_params import FilterParams
        params: FilterParams = {"expression": "area > 1000"}
        assert params["expression"] == "area > 1000"

    def test_spatial_join_params_typing(self):
        from core.capability_params import SpatialJoinParams
        params: SpatialJoinParams = {
            "ref_layer": "zones",
            "predicate": "intersects",
            "columns": ["zone_name"],
        }
        assert params["ref_layer"] == "zones"

    def test_params_map_covers_main_capabilities(self):
        """All 5 main capabilities have TypedDict entries."""
        from core.capability_params import PARAMS_TYPE_MAP
        for cap in ["filter", "buffer", "spatial_join", "dissolve", "centroid"]:
            assert cap in PARAMS_TYPE_MAP, f"{cap} not in PARAMS_TYPE_MAP"

    def test_calculate_params_typing(self):
        from core.capability_params import CalculateParams
        params: CalculateParams = {
            "expressions": {"area_ha": "area_m2 / 10000"},
        }
        assert "area_ha" in params["expressions"]
