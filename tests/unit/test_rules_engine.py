"""Unit tests for the GISPulse rule engine."""

from __future__ import annotations


import geopandas as gpd
import pytest
from shapely.geometry import Point

from core.models import Rule
from persistence.repository import InMemoryRepository
from rules.engine import RuleEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def point_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "value": [5, 15, 25],
            "geometry": [Point(2.35, 48.85), Point(2.30, 48.87), Point(2.40, 48.90)],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def engine(repo) -> RuleEngine:
    return RuleEngine(repository=repo)


# ---------------------------------------------------------------------------
# Single rule application
# ---------------------------------------------------------------------------


class TestRuleEngineApply:
    def test_apply_filter_rule(self, engine, point_gdf):
        rule = Rule(
            name="filter_high_value",
            capability="filter",
            config={"expression": "value > 10"},
        )
        result = engine.apply(rule, point_gdf)
        assert len(result) == 2
        assert all(result["value"] > 10)

    def test_apply_reproject_rule(self, engine, point_gdf):
        rule = Rule(
            name="to_lambert",
            capability="reproject",
            config={"target_crs": "EPSG:2154"},
        )
        result = engine.apply(rule, point_gdf)
        assert result.crs.to_epsg() == 2154

    def test_apply_buffer_rule(self, engine, point_gdf):
        rule = Rule(
            name="buffer_500",
            capability="buffer",
            config={"distance": 500},
        )
        result = engine.apply(rule, point_gdf)
        assert len(result) == len(point_gdf)
        for geom in result.geometry:
            assert geom.geom_type in ("Polygon", "MultiPolygon")

    def test_apply_unknown_capability_raises(self, engine, point_gdf):
        rule = Rule(
            name="bad_rule",
            capability="nonexistent_capability",
            config={},
        )
        # Validation now raises ValueError before reaching the registry KeyError
        with pytest.raises((KeyError, ValueError)):
            engine.apply(rule, point_gdf)


# ---------------------------------------------------------------------------
# Multiple rules (pipeline)
# ---------------------------------------------------------------------------


class TestRuleEngineApplyAll:
    def test_apply_all_respects_order(self, engine, point_gdf):
        """Filter first (order=1), then reproject (order=2)."""
        rules = [
            Rule(
                name="reproject_last",
                capability="reproject",
                config={"target_crs": "EPSG:2154", "order": 2},
            ),
            Rule(
                name="filter_first",
                capability="filter",
                config={"expression": "value > 10", "order": 1},
            ),
        ]
        result = engine.apply_all(rules, point_gdf)
        # 2 features remain after filtering
        assert len(result) == 2
        # CRS was reprojected to 2154 last
        assert result.crs.to_epsg() == 2154

    def test_apply_all_skips_disabled(self, engine, point_gdf):
        rules = [
            Rule(
                name="disabled_filter",
                capability="filter",
                config={"expression": "value > 100", "order": 1},
                enabled=False,
            ),
            Rule(
                name="enabled_reproject",
                capability="reproject",
                config={"target_crs": "EPSG:3857", "order": 2},
                enabled=True,
            ),
        ]
        result = engine.apply_all(rules, point_gdf)
        # Disabled filter is skipped — all 3 features survive
        assert len(result) == 3
        assert result.crs.to_epsg() == 3857

    def test_apply_all_empty_rules(self, engine, point_gdf):
        result = engine.apply_all([], point_gdf)
        assert len(result) == len(point_gdf)

    def test_apply_all_chain(self, engine, point_gdf):
        """Buffer + union pipeline."""
        rules = [
            Rule(
                name="buffer",
                capability="buffer",
                config={"distance": 100, "order": 1},
            ),
            Rule(
                name="union",
                capability="union",
                config={"order": 2},
            ),
        ]
        result = engine.apply_all(rules, point_gdf)
        # Union collapses all to 1 row
        assert len(result) == 1
