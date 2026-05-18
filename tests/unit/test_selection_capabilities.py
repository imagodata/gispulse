"""Unit tests for selection / row-level capabilities."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.capabilities.selection import (
    DeduplicateCapability,
    RandomSampleCapability,
    SortCapability,
    TopNCapability,
)


@pytest.fixture
def cities() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "code_insee": ["75056", "13055", "69123", "75056", "13055"],
            "name": ["Paris", "Marseille", "Lyon", "Paris (dup)", "Marseille (older)"],
            "population": [2_148_000, 870_000, 522_000, 2_140_000, 860_000],
            "updated_at": [3, 2, 1, 1, 1],
            "geometry": [
                Point(2.35, 48.85),
                Point(5.37, 43.30),
                Point(4.83, 45.75),
                Point(2.35, 48.85),
                Point(5.37, 43.30),
            ],
        },
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


class TestSort:
    def test_sort_single_descending(self, cities):
        out = SortCapability().execute(
            cities, by="population", ascending=False,
        )
        assert list(out["population"]) == sorted(cities["population"], reverse=True)

    def test_sort_multi_keys(self, cities):
        out = SortCapability().execute(
            cities, by=["code_insee", "population"], ascending=[True, False],
        )
        # First two are 13055 with descending pop
        assert list(out["code_insee"].iloc[:2]) == ["13055", "13055"]
        assert out["population"].iloc[0] > out["population"].iloc[1]

    def test_unknown_column_raises(self, cities):
        with pytest.raises(KeyError):
            SortCapability().execute(cities, by="ghost")


# ---------------------------------------------------------------------------
# Deduplicate
# ---------------------------------------------------------------------------


class TestDeduplicate:
    def test_dedup_first_keeps_input_order(self, cities):
        out = DeduplicateCapability().execute(cities, keys=["code_insee"], keep="first")
        assert len(out) == 3
        # Default keep='first' preserves first Paris (population 2_148_000)
        paris = out[out["code_insee"] == "75056"]
        assert paris["population"].iloc[0] == 2_148_000

    def test_dedup_with_order_by(self, cities):
        # keep='last' after ascending sort by updated_at means: latest entries win.
        out = DeduplicateCapability().execute(
            cities, keys=["code_insee"], order_by="updated_at",
            ascending=True, keep="last",
        )
        paris = out[out["code_insee"] == "75056"]
        # Paris updated_at=3 (the older row in input but with highest updated_at) → kept
        assert paris["population"].iloc[0] == 2_148_000

    def test_missing_keys_raises(self, cities):
        with pytest.raises(ValueError, match="requires 'keys'"):
            DeduplicateCapability().execute(cities)


# ---------------------------------------------------------------------------
# RandomSample
# ---------------------------------------------------------------------------


class TestRandomSample:
    def test_n_sample_deterministic(self, cities):
        out_a = RandomSampleCapability().execute(cities, n=3, seed=42)
        out_b = RandomSampleCapability().execute(cities, n=3, seed=42)
        assert len(out_a) == 3
        assert (out_a["name"].values == out_b["name"].values).all()

    def test_fraction(self, cities):
        out = RandomSampleCapability().execute(cities, fraction=0.4, seed=1)
        assert len(out) == 2  # 0.4 * 5 = 2

    def test_n_capped_at_layer_size(self, cities):
        out = RandomSampleCapability().execute(cities, n=999, seed=1)
        assert len(out) == 5

    def test_both_n_and_fraction_raises(self, cities):
        with pytest.raises(ValueError, match="either 'n' or 'fraction'"):
            RandomSampleCapability().execute(cities, n=2, fraction=0.5)

    def test_neither_raises(self, cities):
        with pytest.raises(ValueError, match="requires 'n' or 'fraction'"):
            RandomSampleCapability().execute(cities)


# ---------------------------------------------------------------------------
# TopN
# ---------------------------------------------------------------------------


class TestTopN:
    def test_top_n_by_population(self, cities):
        out = TopNCapability().execute(cities, n=2, by="population")
        assert len(out) == 2
        # Default ascending=False → top values
        assert out["population"].iloc[0] == 2_148_000
        assert out["population"].iloc[1] == 2_140_000

    def test_top_n_ascending(self, cities):
        out = TopNCapability().execute(cities, n=2, by="population", ascending=True)
        assert out["population"].iloc[0] == 522_000

    def test_top_n_no_by_keeps_head(self, cities):
        out = TopNCapability().execute(cities, n=3)
        assert list(out["name"]) == ["Paris", "Marseille", "Lyon"]
