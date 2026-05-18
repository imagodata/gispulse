"""Unit tests for MergeLayersCapability + executor ref_layers (list) plumbing."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, Polygon

import gispulse.capabilities as capabilities  # noqa: F401 — register everything
from gispulse.capabilities.registry import get as get_capability
from gispulse.capabilities.vector import MergeLayersCapability
from gispulse.core.pipeline import load_pipeline
from gispulse.orchestration.pipeline_executor import PipelineExecutor


def _poly(size: float, attrs: dict) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        attrs,
        geometry=[Polygon([(0, 0), (size, 0), (size, size), (0, size)])],
        crs="EPSG:4326",
    )


def test_registry_exposes_merge_layers():
    assert isinstance(get_capability("merge_layers"), MergeLayersCapability)


def test_merge_stacks_features_and_preserves_attributes():
    cap = MergeLayersCapability()
    a = _poly(1, {"cost_budget": [500]})
    b = _poly(2, {"cost_budget": [750]})
    c = _poly(3, {"cost_budget": [1500]})

    out = cap.execute(a, ref_gdfs=[b, c])

    assert len(out) == 3
    assert out["cost_budget"].tolist() == [500, 750, 1500]
    assert out.crs == a.crs


def test_merge_no_ref_returns_copy():
    cap = MergeLayersCapability()
    a = _poly(1, {"cost_budget": [500]})
    out = cap.execute(a, ref_gdfs=None)
    assert len(out) == 1
    assert out["cost_budget"].tolist() == [500]


def test_merge_reprojects_mismatched_crs():
    cap = MergeLayersCapability()
    a = _poly(1, {"name": ["a"]})
    b = _poly(2, {"name": ["b"]}).to_crs("EPSG:3857")
    out = cap.execute(a, ref_gdfs=[b])
    assert len(out) == 2
    assert out.crs == a.crs


def test_merge_tolerates_empty_and_none_refs():
    cap = MergeLayersCapability()
    a = _poly(1, {"name": ["a"]})
    empty = gpd.GeoDataFrame({"name": []}, geometry=[], crs="EPSG:4326")
    out = cap.execute(a, ref_gdfs=[empty, None])  # type: ignore[list-item]
    assert len(out) == 1


def test_pipeline_resolves_ref_layers_list_to_ref_gdfs():
    """End-to-end: merge_layers step → spatial_aggregate picks min cost_budget."""
    spec_dict = {
        "version": 2,
        "name": "test_merge_pipeline",
        "ref_layers": {
            "iso_500": "iso_500",
            "iso_750": "iso_750",
            "iso_1000": "iso_1000",
            "buildings": "buildings",
        },
        "steps": [
            {
                "id": "merged_rings",
                "type": "capability",
                "capability": "merge_layers",
                "params": {"ref_layers": ["iso_750", "iso_1000"]},
                "input": "iso_500",
            },
            {
                "id": "smallest_ring",
                "type": "capability",
                "capability": "spatial_aggregate",
                "params": {
                    "ref_layer": "merged_rings",
                    "predicate": "intersects",
                    "agg": {"ring": ["cost_budget", "min"]},
                },
                "input": "buildings",
            },
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(spec_dict, f)
        path = Path(f.name)
    try:
        spec = load_pipeline(path, validate=False)
        inputs = {
            "iso_500": _poly(2, {"cost_budget": [500]}),
            "iso_750": _poly(4, {"cost_budget": [750]}),
            "iso_1000": _poly(6, {"cost_budget": [1000]}),
            "buildings": gpd.GeoDataFrame(
                {"name": ["inner", "mid", "outer", "beyond"]},
                geometry=[Point(1, 1), Point(3, 3), Point(5, 5), Point(7, 7)],
                crs="EPSG:4326",
            ),
        }
        out = PipelineExecutor().execute(spec, inputs)
    finally:
        path.unlink()

    assert len(out["merged_rings"]) == 3
    result = out["smallest_ring"].set_index("name")["ring"].to_dict()
    assert result["inner"] == 500
    assert result["mid"] == 750
    assert result["outer"] == 1000
    # Beyond all rings → no intersecting ref feature → NaN
    import math

    assert math.isnan(result["beyond"])
