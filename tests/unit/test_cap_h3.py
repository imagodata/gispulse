"""Tests for the H3 aggregation capability (gispulse-cap-h3).

Covers the single-metric API (back-compat) and the multi-metric extension
that aggregates several columns in a SINGLE H3 pass (one ``latlng_to_cell``
+ one ``cell_to_boundary``) — so a caller wanting ``count`` + ``sum`` no
longer pays for two full passes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Runtime needs the h3 lib (a plugin dep) — skip cleanly where it isn't installed
# (e.g. the engine-only `unit` CI job). The `plugins` CI job installs it.
pytest.importorskip("h3")

# Make the bundled plugin importable without an editable install (same pattern
# as test_src_cadastre.py), so this file runs under the `plugins` CI job.
_PKG = Path(__file__).resolve().parents[2] / "plugins" / "gispulse-cap-h3"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point  # noqa: E402

from gispulse_cap_h3.capabilities import H3AggregateCapability  # noqa: E402


def _toy_points() -> gpd.GeoDataFrame:
    # Two co-located points (same r5 cell) + one distant point.
    return gpd.GeoDataFrame(
        {"weight": [1.0, 3.0, 5.0]},
        geometry=[Point(4.3500, 50.8500), Point(4.3501, 50.8501), Point(3.2000, 51.2000)],
        crs="EPSG:4326",
    )


def test_single_metric_is_backward_compatible() -> None:
    out = H3AggregateCapability().execute(_toy_points(), resolution=5, agg_column="weight", agg_func="sum")
    assert set(out.columns) >= {"h3_index", "value", "geometry"}
    assert len(out) == 2
    assert set(out["value"]) == {4.0, 5.0}  # 1+3 colocalisés, 5 isolé
    assert out.geometry.iloc[0].geom_type == "Polygon"
    assert out.crs is not None and out.crs.to_epsg() == 4326


def test_count_without_column_uses_size() -> None:
    out = H3AggregateCapability().execute(_toy_points(), resolution=5)
    assert set(out["value"]) == {2, 1}


def test_multi_metric_single_pass() -> None:
    out = H3AggregateCapability().execute(
        _toy_points(),
        resolution=5,
        aggregations={"count": (None, "count"), "weight_sum": ("weight", "sum")},
    )
    assert set(out.columns) >= {"h3_index", "count", "weight_sum", "geometry"}
    assert len(out) == 2
    big = out.sort_values("count", ascending=False).iloc[0]
    assert big["count"] == 2
    assert big["weight_sum"] == 4.0
    assert out.geometry.iloc[0].geom_type == "Polygon"
    assert out.crs is not None and out.crs.to_epsg() == 4326


def test_multi_metric_missing_column_raises() -> None:
    # A missing source column must NOT silently become a count (plausible-but-wrong).
    with pytest.raises(ValueError, match="not found"):
        H3AggregateCapability().execute(
            _toy_points(),
            resolution=5,
            aggregations={"revenue_sum": ("revenue", "sum")},  # no 'revenue' column
        )


def test_empty_aggregations_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        H3AggregateCapability().execute(_toy_points(), resolution=5, aggregations={})
