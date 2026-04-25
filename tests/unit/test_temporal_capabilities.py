"""Unit tests for temporal capabilities (filter, join)."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from capabilities.temporal import (
    TemporalFilterCapability,
    TemporalJoinCapability,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sensor_readings() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "sensor_id": ["A", "A", "B", "B", "B"],
            "captured_at": pd.to_datetime([
                "2026-01-01 09:00",
                "2026-01-01 10:00",
                "2026-01-01 09:30",
                "2026-01-01 10:30",
                "2026-01-01 11:00",
            ]),
            "value": [1.0, 2.0, 3.0, 4.0, 5.0],
            "geometry": [Point(i, i) for i in range(5)],
        },
        crs="EPSG:2154",
    )


@pytest.fixture
def weather_observations() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": pd.to_datetime([
                "2026-01-01 08:55",
                "2026-01-01 09:55",
                "2026-01-01 10:55",
            ]),
            "temperature": [5.0, 7.0, 9.0],
        },
    )


# ---------------------------------------------------------------------------
# TemporalFilter
# ---------------------------------------------------------------------------


class TestTemporalFilter:
    def test_window_inclusive(self, sensor_readings):
        out = TemporalFilterCapability().execute(
            sensor_readings,
            time_col="captured_at",
            start="2026-01-01 09:30",
            end="2026-01-01 10:30",
        )
        assert len(out) == 3  # 09:30, 10:00, 10:30

    def test_exclusive_bounds(self, sensor_readings):
        out = TemporalFilterCapability().execute(
            sensor_readings,
            time_col="captured_at",
            start="2026-01-01 09:30",
            end="2026-01-01 10:30",
            include_start=False,
            include_end=False,
        )
        assert len(out) == 1  # only 10:00

    def test_invert(self, sensor_readings):
        out = TemporalFilterCapability().execute(
            sensor_readings,
            time_col="captured_at",
            start="2026-01-01 10:00",
            invert=True,
        )
        # rows strictly < 10:00 only (default include_start=True for the
        # window kept; invert flips → keep < 10:00)
        assert len(out) == 2
        assert (out["captured_at"] < pd.Timestamp("2026-01-01 10:00")).all()

    def test_missing_window_raises(self, sensor_readings):
        with pytest.raises(ValueError, match="at least one"):
            TemporalFilterCapability().execute(sensor_readings, time_col="captured_at")

    def test_unknown_col_raises(self, sensor_readings):
        with pytest.raises(KeyError):
            TemporalFilterCapability().execute(
                sensor_readings, time_col="ghost", start="2026-01-01",
            )


# ---------------------------------------------------------------------------
# TemporalJoin
# ---------------------------------------------------------------------------


class TestTemporalJoin:
    def test_backward_asof(self, sensor_readings, weather_observations):
        out = TemporalJoinCapability().execute(
            sensor_readings,
            ref_gdf=weather_observations,
            left_on="captured_at",
            right_on="ts",
            strategy="backward",
        )
        assert "temperature" in out.columns
        # 09:00 → 08:55 → 5.0
        # 10:00 → 09:55 → 7.0
        # 09:30 → 08:55 → 5.0
        # 10:30 → 09:55 → 7.0
        # 11:00 → 10:55 → 9.0
        assert out.loc[out["captured_at"] == pd.Timestamp("2026-01-01 09:00"), "temperature"].iloc[0] == 5.0
        assert out.loc[out["captured_at"] == pd.Timestamp("2026-01-01 11:00"), "temperature"].iloc[0] == 9.0

    def test_tolerance(self, sensor_readings, weather_observations):
        out = TemporalJoinCapability().execute(
            sensor_readings,
            ref_gdf=weather_observations,
            left_on="captured_at",
            right_on="ts",
            strategy="backward",
            tolerance="10min",  # too tight for several pairs
        )
        # 09:00 → 08:55 (5 min) ✓
        # 09:30 → 08:55 (35 min) ✗
        # 10:00 → 09:55 (5 min) ✓
        # 10:30 → 09:55 (35 min) ✗
        # 11:00 → 10:55 (5 min) ✓
        matched = out["temperature"].notna().sum()
        assert matched == 3

    def test_exact_strategy(self):
        left = gpd.GeoDataFrame(
            {"ts": pd.to_datetime(["2026-01-01", "2026-01-02"]),
             "geometry": [Point(0, 0), Point(1, 1)]},
            crs="EPSG:2154",
        )
        right = pd.DataFrame(
            {"ts": pd.to_datetime(["2026-01-01", "2026-01-03"]),
             "v": [10, 30]},
        )
        out = TemporalJoinCapability().execute(
            left, ref_gdf=right, left_on="ts", strategy="exact",
        )
        assert out.loc[out["ts"] == pd.Timestamp("2026-01-01"), "v"].iloc[0] == 10
        # No exact match for 2026-01-02 → NaN
        assert pd.isna(out.loc[out["ts"] == pd.Timestamp("2026-01-02"), "v"].iloc[0])

    def test_by_group(self, sensor_readings):
        # Per-sensor weather: A has temp 7 at 09:55, B has temp 8 at 09:55.
        ref = pd.DataFrame(
            {
                "sensor_id": ["A", "B"],
                "ts": pd.to_datetime(["2026-01-01 09:55", "2026-01-01 09:55"]),
                "temp": [7.0, 8.0],
            },
        )
        out = TemporalJoinCapability().execute(
            sensor_readings, ref_gdf=ref,
            left_on="captured_at", right_on="ts",
            by="sensor_id", strategy="backward",
        )
        a_at_10 = out[(out["sensor_id"] == "A") & (out["captured_at"] == pd.Timestamp("2026-01-01 10:00"))]
        b_at_10 = out[(out["sensor_id"] == "B") & (out["captured_at"] == pd.Timestamp("2026-01-01 10:30"))]
        assert a_at_10["temp"].iloc[0] == 7.0
        assert b_at_10["temp"].iloc[0] == 8.0

    def test_unknown_strategy_raises(self, sensor_readings, weather_observations):
        with pytest.raises(ValueError, match="strategy must be"):
            TemporalJoinCapability().execute(
                sensor_readings, ref_gdf=weather_observations,
                left_on="captured_at", right_on="ts",
                strategy="bogus",
            )
