"""Regression guard: KDEHeatmapCapability hard cap on grid size.

Beta-test finding 2026-04-24 v3 Part 2 (US-7). Trivial DoS via params:
``bandwidth=1000, cell_size=1`` on a single point inflates the padded
bounds to 2001 x 2001 cells = ~4M cells, ~1GB RAM, 15s — no guard.

The fix adds a configurable ``GISPULSE_KDE_MAX_CELLS`` env-tunable hard
cap (default 1_000_000). Above the cap, the capability raises ValueError
with the offending parameters in the message.
"""
from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.capabilities.registry import get as cap_get
import gispulse.capabilities as capabilities  # noqa: F401


@pytest.fixture
def single_point() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"geometry": [Point(0, 0)]},
        crs="EPSG:3857",
    )


def test_kde_grid_blowup_rejected_at_default_cap(single_point):
    """bandwidth=1000, cell_size=1 → 4M cells, must raise ValueError."""
    cap = cap_get("kde_heatmap")
    with pytest.raises(ValueError, match=r"GISPULSE_KDE_MAX_CELLS"):
        cap.execute(single_point, bandwidth=1000.0, cell_size=1.0)


def test_kde_safe_params_pass(single_point):
    """Reasonable params must still succeed (grid << 1M cells)."""
    cap = cap_get("kde_heatmap")
    out = cap.execute(single_point, bandwidth=100.0, cell_size=50.0)
    assert "density" in out.columns
    assert len(out) > 0


def test_kde_env_override_relaxes_cap(monkeypatch, single_point):
    """Operators can raise the cap via env var when they explicitly accept the cost."""
    monkeypatch.setenv("GISPULSE_KDE_MAX_CELLS", "5000000")
    cap = cap_get("kde_heatmap")
    out = cap.execute(single_point, bandwidth=1000.0, cell_size=1.0)
    assert len(out) > 1_000_000


def test_kde_env_override_tightens_cap(monkeypatch, single_point):
    """Operators can also tighten the cap via env var."""
    monkeypatch.setenv("GISPULSE_KDE_MAX_CELLS", "100")
    cap = cap_get("kde_heatmap")
    with pytest.raises(ValueError, match=r"GISPULSE_KDE_MAX_CELLS=100"):
        cap.execute(single_point, bandwidth=100.0, cell_size=10.0)
