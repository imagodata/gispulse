"""Unit tests for attribute-logic capabilities: lookup_table / coalesce_fields / case_when."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.capabilities.schema import (
    CaseWhenCapability,
    CoalesceFieldsCapability,
    LookupTableCapability,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def communes() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "code_dep": ["75", "13", "69", "33", "99"],
            "name": ["Paris", "Marseille", "Lyon", "Bordeaux", "Atlantis"],
            "population": [2_148_000, 870_000, 522_000, 254_000, 0],
            "geometry": [Point(i, i) for i in range(5)],
        },
        crs="EPSG:2154",
    )


@pytest.fixture
def naming() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "preferred_name": ["Alpha", None, None, "Delta"],
            "official_name": [None, "BetaOff", None, "DeltaOff"],
            "fallback_name": ["X", "Y", "Z", "W"],
            "geometry": [Point(i, i) for i in range(4)],
        },
        crs="EPSG:2154",
    )


# ---------------------------------------------------------------------------
# LookupTableCapability
# ---------------------------------------------------------------------------


class TestLookupTable:
    def test_basic_mapping(self, communes):
        out = LookupTableCapability().execute(
            communes,
            source_col="code_dep",
            target_col="region",
            mapping={"75": "IDF", "13": "PACA", "69": "ARA", "33": "NAQ"},
            default="Unknown",
        )
        assert "region" in out.columns
        assert out.loc[out["code_dep"] == "75", "region"].iloc[0] == "IDF"
        assert out.loc[out["code_dep"] == "99", "region"].iloc[0] == "Unknown"

    def test_default_passthrough(self, communes):
        out = LookupTableCapability().execute(
            communes,
            source_col="code_dep",
            target_col="region",
            mapping={"75": "IDF"},
            default="__source__",
        )
        # Unmatched keeps the original value
        assert out.loc[out["code_dep"] == "13", "region"].iloc[0] == "13"
        assert out.loc[out["code_dep"] == "75", "region"].iloc[0] == "IDF"

    def test_overwrite_source_when_no_target(self, communes):
        out = LookupTableCapability().execute(
            communes,
            source_col="code_dep",
            mapping={"75": "IDF"},
            default="__source__",
        )
        # No target_col → overwrites source
        assert out.loc[out["name"] == "Paris", "code_dep"].iloc[0] == "IDF"
        assert out.loc[out["name"] == "Lyon", "code_dep"].iloc[0] == "69"

    def test_missing_source_raises(self, communes):
        with pytest.raises(KeyError):
            LookupTableCapability().execute(
                communes, source_col="ghost", mapping={"x": 1},
            )

    def test_empty_mapping_raises(self, communes):
        with pytest.raises(ValueError, match="non-empty 'mapping'"):
            LookupTableCapability().execute(
                communes, source_col="code_dep", mapping={},
            )


# ---------------------------------------------------------------------------
# CoalesceFieldsCapability
# ---------------------------------------------------------------------------


class TestCoalesceFields:
    def test_first_non_null_wins(self, naming):
        out = CoalesceFieldsCapability().execute(
            naming,
            sources=["preferred_name", "official_name", "fallback_name"],
            target_col="display_name",
        )
        # row 0: preferred=Alpha → Alpha
        # row 1: preferred=None, official=BetaOff → BetaOff
        # row 2: preferred=None, official=None, fallback=Z → Z
        # row 3: preferred=Delta → Delta
        assert list(out["display_name"]) == ["Alpha", "BetaOff", "Z", "Delta"]

    def test_protect_geometry(self, naming):
        with pytest.raises(ValueError, match="geometry column"):
            CoalesceFieldsCapability().execute(
                naming, sources=["preferred_name"], target_col="geometry",
            )

    def test_unknown_source_raises(self, naming):
        with pytest.raises(KeyError):
            CoalesceFieldsCapability().execute(
                naming, sources=["ghost"], target_col="x",
            )

    def test_empty_sources_raises(self, naming):
        with pytest.raises(ValueError, match="requires 'sources'"):
            CoalesceFieldsCapability().execute(naming, target_col="x")


# ---------------------------------------------------------------------------
# CaseWhenCapability
# ---------------------------------------------------------------------------


class TestCaseWhen:
    def test_first_match_wins(self, communes):
        out = CaseWhenCapability().execute(
            communes,
            target_col="tier",
            cases=[
                {"when": "population > 1_000_000", "then": "large"},
                {"when": "population > 100_000",   "then": "medium"},
            ],
            else_="small",
        )
        # Paris: 2.1M → large; Marseille: 870k → medium; Atlantis: 0 → small
        tiers = dict(zip(out["name"], out["tier"]))
        assert tiers["Paris"] == "large"
        assert tiers["Marseille"] == "medium"
        assert tiers["Lyon"] == "medium"
        assert tiers["Bordeaux"] == "medium"
        assert tiers["Atlantis"] == "small"

    def test_else_default_when_no_else_key(self, communes):
        out = CaseWhenCapability().execute(
            communes,
            target_col="flag",
            cases=[{"when": "population > 5_000_000", "then": "huge"}],
        )
        # No matches and no else_ → all None
        assert out["flag"].isna().all()

    def test_no_overwrite_after_first_match(self, communes):
        # Both cases match Paris; first should win.
        out = CaseWhenCapability().execute(
            communes,
            target_col="tier",
            cases=[
                {"when": "population > 100_000", "then": "first"},
                {"when": "population > 10_000",  "then": "second"},
            ],
        )
        # Paris matches both — but 'first' wins
        assert out.loc[out["name"] == "Paris", "tier"].iloc[0] == "first"

    def test_protect_geometry(self, communes):
        with pytest.raises(ValueError, match="geometry column"):
            CaseWhenCapability().execute(
                communes, target_col="geometry",
                cases=[{"when": "population > 0", "then": 1}],
            )

    def test_invalid_when_raises(self, communes):
        with pytest.raises(ValueError, match="forbidden pattern"):
            CaseWhenCapability().execute(
                communes, target_col="tier",
                cases=[{"when": "__import__('os').system('ls')", "then": 1}],
            )

    def test_missing_when_raises(self, communes):
        with pytest.raises(ValueError, match="non-empty 'when'"):
            CaseWhenCapability().execute(
                communes, target_col="tier",
                cases=[{"then": 1}],
            )

    def test_empty_cases_raises(self, communes):
        with pytest.raises(ValueError, match="at least one"):
            CaseWhenCapability().execute(communes, target_col="tier", cases=[])
