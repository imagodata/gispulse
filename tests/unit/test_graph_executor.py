"""Tests for the GraphExecutor DAG engine."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from core.models import EdgeDef, NodeDef, NodeType
from orchestration.graph_executor import GraphExecutor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeCapability:
    """Minimal capability stub for testing."""

    def __init__(self, name: str, transform=None):
        self.name = name
        self._transform = transform

    def execute(self, gdf=None, **kwargs):
        if self._transform:
            return self._transform(gdf=gdf, **kwargs)
        if gdf is not None:
            return gdf.copy()
        # multi-input: return first GDF found
        for v in kwargs.values():
            if isinstance(v, gpd.GeoDataFrame):
                return v.copy()
        return gpd.GeoDataFrame()


def _make_gdf(n: int = 3) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"name": [f"feat_{i}" for i in range(n)]},
        geometry=[Point(i, i) for i in range(n)],
        crs="EPSG:4326",
    )


def _buffer_transform(gdf=None, **kwargs):
    distance = kwargs.get("distance", 1.0)
    result = gdf.copy()
    result["geometry"] = result.geometry.buffer(distance)
    return result


def _filter_transform(gdf=None, **kwargs):
    field = kwargs.get("field", "name")
    value = kwargs.get("value", "")
    return gpd.GeoDataFrame(gdf[gdf[field] == value])


CAPS = {
    "buffer": FakeCapability("buffer", _buffer_transform),
    "filter": FakeCapability("filter", _filter_transform),
    "intersect": FakeCapability("intersect"),
    "union": FakeCapability("union"),
}


def cap_getter(name: str):
    return CAPS[name]


@pytest.fixture
def executor():
    return GraphExecutor(capability_getter=cap_getter)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLinearPipeline:
    """Test simple linear A → B → C pipeline."""

    def test_two_node_pipeline(self, executor):
        gdf = _make_gdf(5)
        nodes = [
            NodeDef(id="src", node_type=NodeType.DATASET),
            NodeDef(id="buf", node_type=NodeType.CAPABILITY, capability="buffer",
                    params={"distance": 0.5}),
        ]
        edges = [EdgeDef(source="src", target="buf")]
        results = executor.execute(nodes, edges, {"src": gdf})

        assert "buf" in results
        assert len(results["buf"]) == 5
        # buffered geometries should be polygons, not points
        assert results["buf"].geometry.iloc[0].geom_type == "Polygon"

    def test_three_node_chain(self, executor):
        gdf = _make_gdf(3)
        nodes = [
            NodeDef(id="src", node_type=NodeType.DATASET),
            NodeDef(id="buf", node_type=NodeType.CAPABILITY, capability="buffer",
                    params={"distance": 1.0}),
            NodeDef(id="out", node_type=NodeType.ARTIFACT),
        ]
        edges = [
            EdgeDef(source="src", target="buf"),
            EdgeDef(source="buf", target="out"),
        ]
        results = executor.execute(nodes, edges, {"src": gdf})
        assert "out" in results
        assert len(results["out"]) == 3


class TestMultiInput:
    """Test multi-input nodes (e.g., intersect with layer_a + layer_b)."""

    def test_two_inputs_merge(self, executor):
        gdf_a = _make_gdf(2)
        gdf_b = _make_gdf(3)
        nodes = [
            NodeDef(id="a", node_type=NodeType.DATASET),
            NodeDef(id="b", node_type=NodeType.DATASET),
            NodeDef(id="inter", node_type=NodeType.CAPABILITY, capability="intersect"),
        ]
        edges = [
            EdgeDef(source="a", target="inter", handle="in:layer_a"),
            EdgeDef(source="b", target="inter", handle="in:layer_b"),
        ]
        results = executor.execute(nodes, edges, {"a": gdf_a, "b": gdf_b})
        assert "inter" in results


class TestBranchNode:
    """Test conditional branching."""

    def test_branch_filters(self, executor):
        gdf = _make_gdf(5)
        nodes = [
            NodeDef(id="src", node_type=NodeType.DATASET),
            NodeDef(id="branch", node_type=NodeType.BRANCH,
                    params={"condition_field": "name", "condition_op": "eq",
                            "condition_value": "feat_2"}),
        ]
        edges = [EdgeDef(source="src", target="branch")]
        results = executor.execute(nodes, edges, {"src": gdf})

        assert "branch" in results
        assert len(results["branch"]) == 1
        assert results["branch"].iloc[0]["name"] == "feat_2"


class TestAggregateNode:
    """Test merging multiple GDFs."""

    def test_aggregate_two_sources(self, executor):
        gdf_a = _make_gdf(2)
        gdf_b = _make_gdf(3)
        nodes = [
            NodeDef(id="a", node_type=NodeType.DATASET),
            NodeDef(id="b", node_type=NodeType.DATASET),
            NodeDef(id="agg", node_type=NodeType.AGGREGATE),
        ]
        edges = [
            EdgeDef(source="a", target="agg"),
            EdgeDef(source="b", target="agg"),
        ]
        results = executor.execute(nodes, edges, {"a": gdf_a, "b": gdf_b})
        assert "agg" in results
        assert len(results["agg"]) == 5


class TestParameterResolution:
    """Test $param substitution."""

    def test_param_in_capability(self, executor):
        gdf = _make_gdf(3)
        nodes = [
            NodeDef(id="src", node_type=NodeType.DATASET),
            NodeDef(id="buf", node_type=NodeType.CAPABILITY, capability="buffer",
                    params={"distance": "$buf_dist"}),
        ]
        edges = [EdgeDef(source="src", target="buf")]
        results = executor.execute(nodes, edges, {"src": gdf}, {"buf_dist": 2.0})

        assert "buf" in results
        assert results["buf"].geometry.iloc[0].geom_type == "Polygon"


class TestCycleDetection:
    """Test that cycles raise ValueError."""

    def test_cycle_raises(self, executor):
        nodes = [
            NodeDef(id="a", node_type=NodeType.CAPABILITY, capability="buffer"),
            NodeDef(id="b", node_type=NodeType.CAPABILITY, capability="buffer"),
        ]
        edges = [
            EdgeDef(source="a", target="b"),
            EdgeDef(source="b", target="a"),
        ]
        with pytest.raises(ValueError, match="Cycle detected"):
            executor.execute(nodes, edges, {})


class TestEmptyGraph:
    """Edge case: empty graph."""

    def test_empty_nodes(self, executor):
        results = executor.execute([], [], {})
        assert results == {}
