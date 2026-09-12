"""Tests for capabilities/surface_sampling.py + calibrate_detour_bands."""

from __future__ import annotations

import math

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString, Polygon

from gispulse.capabilities.routing import CalibrateDetourBandsCapability
from gispulse.capabilities.surface_sampling import SampleSurfaceAlongLinesCapability


@pytest.fixture(autouse=True)
def pro_tier(monkeypatch):
    """Both capabilities require Pro tier — activate it for every test."""
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv("GISPULSE_LICENSE_KEY", "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


_CRS = "EPSG:3857"


def _band(x0: float, x1: float, cls: str, y0: float = -10.0, y1: float = 10.0):
    """Vertical strip polygon [x0, x1] × [y0, y1] tagged with a class."""
    return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]), cls


def _surfaces(strips) -> gpd.GeoDataFrame:
    geoms, classes = zip(*strips)
    return gpd.GeoDataFrame({"surface_class": list(classes)}, geometry=list(geoms), crs=_CRS)


def _lines(lines) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=list(lines), crs=_CRS)


class TestSampleSurfaceAlongLines:
    def test_segments_cover_the_line_in_order(self):
        """Line 0→100 through two strips + a gap → 4 ordered segments."""
        surfaces = _surfaces([_band(0, 30, "asphalt"), _band(50, 80, "grass")])
        line = _lines([LineString([(0, 0), (100, 0)])])
        result = SampleSurfaceAlongLinesCapability().execute(
            line, ref_gdf=surfaces, fallback_class="unknown"
        )
        assert list(result["surface_class"]) == ["asphalt", "unknown", "grass", "unknown"]
        assert list(result["length_m"]) == pytest.approx([30, 20, 30, 20])
        assert list(result["segment_order"]) == [0, 1, 2, 3]
        assert sum(result["length_m"]) == pytest.approx(100.0)
        assert sum(result["share"]) == pytest.approx(1.0)
        # Sub-line geometries: each segment's geometry has the right length.
        for _, row in result.iterrows():
            assert row.geometry.length == pytest.approx(row["length_m"])

    def test_uncovered_line_is_all_fallback(self):
        surfaces = _surfaces([_band(200, 300, "asphalt")])
        line = _lines([LineString([(0, 0), (100, 0)])])
        result = SampleSurfaceAlongLinesCapability().execute(
            line, ref_gdf=surfaces, fallback_class="void"
        )
        assert list(result["surface_class"]) == ["void"]
        assert result["length_m"].iloc[0] == pytest.approx(100.0)

    def test_overlap_first_layer_wins_by_default(self):
        """Two strips overlap on [40, 60]: the first row of the layer wins."""
        surfaces = _surfaces([_band(0, 60, "asphalt"), _band(40, 100, "grass")])
        line = _lines([LineString([(0, 0), (100, 0)])])
        result = SampleSurfaceAlongLinesCapability().execute(
            line, ref_gdf=surfaces
        )
        assert list(result["surface_class"]) == ["asphalt", "grass"]
        assert list(result["length_m"]) == pytest.approx([60, 40])

    def test_overlap_priority_list_last_wins(self):
        """Same overlap, explicit priority: grass listed after asphalt → wins."""
        surfaces = _surfaces([_band(0, 60, "asphalt"), _band(40, 100, "grass")])
        line = _lines([LineString([(0, 0), (100, 0)])])
        result = SampleSurfaceAlongLinesCapability().execute(
            line, ref_gdf=surfaces, priority=["asphalt", "grass"]
        )
        assert list(result["surface_class"]) == ["asphalt", "grass"]
        assert list(result["length_m"]) == pytest.approx([40, 60])

    def test_unlisted_class_outranks_listed(self):
        """A class absent from the priority list wins over listed ones."""
        surfaces = _surfaces([_band(0, 60, "mystery"), _band(40, 100, "grass")])
        line = _lines([LineString([(0, 0), (100, 0)])])
        result = SampleSurfaceAlongLinesCapability().execute(
            line, ref_gdf=surfaces, priority=["grass"]
        )
        assert list(result["surface_class"]) == ["mystery", "grass"]
        assert list(result["length_m"]) == pytest.approx([60, 40])

    def test_adjacent_same_class_merged(self):
        """Two adjacent strips of one class → a single merged segment."""
        surfaces = _surfaces([_band(0, 50, "asphalt"), _band(50, 100, "asphalt")])
        line = _lines([LineString([(0, 0), (100, 0)])])
        result = SampleSurfaceAlongLinesCapability().execute(line, ref_gdf=surfaces)
        assert list(result["surface_class"]) == ["asphalt"]
        assert result["length_m"].iloc[0] == pytest.approx(100.0)

    def test_multilinestring_parts_share_total(self):
        """A 2-part multi-line: shares are relative to the full row length."""
        surfaces = _surfaces([_band(0, 100, "asphalt")])
        multi = MultiLineString([[(0, 0), (60, 0)], [(0, 5), (40, 5)]])
        result = SampleSurfaceAlongLinesCapability().execute(
            _lines([multi]), ref_gdf=surfaces
        )
        assert set(result["line_id"]) == {0}
        assert sum(result["share"]) == pytest.approx(1.0)
        assert sum(result["length_m"]) == pytest.approx(100.0)

    def test_missing_ref_or_class_col_raise(self):
        line = _lines([LineString([(0, 0), (1, 0)])])
        with pytest.raises(ValueError, match="polygon layer"):
            SampleSurfaceAlongLinesCapability().execute(line)
        surfaces = _surfaces([_band(0, 1, "a")]).rename(
            columns={"surface_class": "other"}
        )
        with pytest.raises(ValueError, match="class_col"):
            SampleSurfaceAlongLinesCapability().execute(line, ref_gdf=surfaces)

    def test_empty_input(self):
        empty = gpd.GeoDataFrame(geometry=[], crs=_CRS)
        surfaces = _surfaces([_band(0, 1, "a")])
        result = SampleSurfaceAlongLinesCapability().execute(empty, ref_gdf=surfaces)
        assert result.empty
        assert list(result.columns) == [
            "line_id",
            "segment_order",
            "surface_class",
            "length_m",
            "share",
            "geometry",
        ]


def _observations(pairs) -> gpd.GeoDataFrame:
    """(straight_m, distance_m) observations with a stub segment geometry."""
    return gpd.GeoDataFrame(
        {
            "straight_m": [s for s, _ in pairs],
            "distance_m": [d for _, d in pairs],
        },
        geometry=[LineString([(0, i), (1, i)]) for i in range(len(pairs))],
        crs=_CRS,
    )


class TestCalibrateDetourBands:
    def test_median_ratio_per_band(self):
        obs = _observations(
            [(50, 60), (80, 120), (90, 117), (500, 600), (800, 1200)]
        )
        result = CalibrateDetourBandsCapability().execute(
            obs,
            bands=[
                {"max_straight_m": 100, "factor": 1.0},
                {"max_straight_m": None, "factor": 1.0},
            ],
        )
        # Band 0 ratios: 1.2, 1.5, 1.3 → median 1.3 ; band 1: 1.2, 1.5 → 1.35.
        assert list(result["band_index"]) == [0, 1]
        assert result["recommended_factor"].iloc[0] == pytest.approx(1.3)
        assert result["recommended_factor"].iloc[1] == pytest.approx(1.35)
        assert list(result["sample_count"]) == [3, 2]
        assert set(result["used_observations"]) == {5}

    def test_band_without_observation_keeps_current_factor(self):
        obs = _observations([(50, 65)])
        result = CalibrateDetourBandsCapability().execute(
            obs,
            bands=[
                {"max_straight_m": 100, "factor": 1.0},
                {"max_straight_m": None, "factor": 1.7},
            ],
        )
        empty_band = result[result["band_index"] == 1].iloc[0]
        assert math.isnan(empty_band["recommended_factor"])
        assert empty_band["applied_factor"] == pytest.approx(1.7)
        assert empty_band["sample_count"] == 0
        assert empty_band.geometry is None

    def test_inclusive_upper_bound(self):
        """An observation at exactly max_straight_m calibrates its own band."""
        obs = _observations([(100, 150)])
        result = CalibrateDetourBandsCapability().execute(
            obs,
            bands=[
                {"max_straight_m": 100, "factor": 1.0},
                {"max_straight_m": None, "factor": 1.0},
            ],
        )
        assert list(result["sample_count"]) == [1, 0]

    def test_skips_short_and_degenerate_observations(self):
        obs = _observations([(0, 10), (5, 10), (50, 65)])
        result = CalibrateDetourBandsCapability().execute(
            obs, bands=[{"max_straight_m": None, "factor": 1.0}], min_straight_m=10.0
        )
        assert set(result["used_observations"]) == {1}
        assert set(result["skipped_observations"]) == {2}
        assert result["recommended_factor"].iloc[0] == pytest.approx(1.3)

    def test_default_band_calibrates_global_factor(self):
        obs = _observations([(100, 140), (200, 240)])
        result = CalibrateDetourBandsCapability().execute(obs)
        assert len(result) == 1
        assert result["current_factor"].iloc[0] == pytest.approx(1.0)
        assert result["recommended_factor"].iloc[0] == pytest.approx(1.3)

    def test_missing_column_raises(self):
        obs = _observations([(100, 140)]).drop(columns=["distance_m"])
        with pytest.raises(ValueError, match="distance_m"):
            CalibrateDetourBandsCapability().execute(obs)

    def test_applied_factor_roundtrips_into_route_pairs(self):
        """applied_factor feeds back into route_pairs as tortuosity_bands."""
        from gispulse.capabilities.routing import RoutePairsCapability

        obs = _observations([(100, 140), (300, 420)])
        calib = CalibrateDetourBandsCapability().execute(obs)
        bands = [
            {"max_straight_m": row["max_straight_m"], "factor": row["applied_factor"]}
            for _, row in calib.iterrows()
        ]
        origins = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (0, 0.1)]).centroid], crs=_CRS)
        dests = gpd.GeoDataFrame(geometry=[LineString([(30, 40), (30, 40.1)]).centroid], crs=_CRS)
        routed = RoutePairsCapability().execute(
            origins, ref_gdf=dests, tortuosity_bands=bands
        )
        assert routed["distance_m"].iloc[0] == pytest.approx(
            routed["straight_m"].iloc[0] * 1.4
        )
