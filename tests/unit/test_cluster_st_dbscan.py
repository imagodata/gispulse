"""Unit tests for cluster_st_dbscan (capability J)."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

pytest.importorskip("sklearn", reason="scikit-learn not installed")

import gispulse
from gispulse.capabilities.clustering import STDBSCANClusterCapability


def _run(gdf, **params):
    return STDBSCANClusterCapability().execute(gdf, **params)


def _points(coords, times) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"timestamp": pd.to_datetime(times)},
        geometry=[Point(*c) for c in coords],
        crs="EPSG:3857",
    )


def test_time_separates_spatially_close_points():
    # Four points at the SAME location, in two time bursts an hour+ apart.
    # Pure spatial DBSCAN would merge all four; ST-DBSCAN splits by time.
    pts = _points(
        [(0, 0), (1, 0), (0, 0), (1, 0)],
        [
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:30",
            "2026-01-01T02:00:00",
            "2026-01-01T02:00:30",
        ],
    )
    out = _run(pts, eps_m=10.0, eps_time=300.0, min_samples=2)
    labels = out["cluster"].tolist()
    assert labels[0] == labels[1]      # first burst together
    assert labels[2] == labels[3]      # second burst together
    assert labels[0] != labels[2]      # but the two bursts are distinct
    assert -1 not in labels


def test_space_separates_temporally_close_points():
    # Same instant, but far apart in space → different clusters.
    pts = _points(
        [(0, 0), (1, 0), (1000, 0), (1001, 0)],
        ["2026-01-01T00:00:00"] * 4,
    )
    out = _run(pts, eps_m=10.0, eps_time=600.0, min_samples=2)
    labels = out["cluster"].tolist()
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]


def test_all_together_when_thresholds_large():
    pts = _points(
        [(0, 0), (1, 0), (2, 0)],
        ["2026-01-01T00:00:00", "2026-01-01T00:01:00", "2026-01-01T00:02:00"],
    )
    out = _run(pts, eps_m=10.0, eps_time=3600.0, min_samples=2)
    assert set(out["cluster"]) == {0}


def test_nat_is_noise():
    pts = _points(
        [(0, 0), (1, 0), (2, 0)],
        ["2026-01-01T00:00:00", "2026-01-01T00:00:30", None],
    )
    out = _run(pts, eps_m=10.0, eps_time=300.0, min_samples=2)
    labels = out["cluster"].tolist()
    assert labels[0] == labels[1] != -1
    assert labels[2] == -1            # NaT → never a neighbor → noise


def test_missing_time_col_raises():
    pts = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:3857")
    with pytest.raises(ValueError, match="time_col"):
        _run(pts, time_col="timestamp")


def test_invalid_eps_time_raises():
    pts = _points([(0, 0)], ["2026-01-01T00:00:00"])
    with pytest.raises(ValueError, match="eps_time"):
        _run(pts, eps_time=0.0)


def test_empty_gdf():
    empty = gpd.GeoDataFrame({"timestamp": []}, geometry=[], crs="EPSG:3857")
    out = _run(empty)
    assert "cluster" in out.columns
    assert len(out) == 0


def test_via_apply():
    pts = _points(
        [(0, 0), (1, 0)],
        ["2026-01-01T00:00:00", "2026-01-01T00:00:30"],
    )
    out = gispulse.apply(
        "cluster_st_dbscan", pts, eps_m=10.0, eps_time=300.0, min_samples=2
    )
    assert out["cluster"].tolist() == [0, 0]
