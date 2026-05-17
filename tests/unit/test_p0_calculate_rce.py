"""Regression guards: CalculateCapability `np` namespace is restricted to
pure-math ufuncs. Attempting to reach `np.save` / `np.load` / any other
attribute outside the whitelist raises AttributeError via _SafeNamespace.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.capabilities.registry import get as cap_get
import gispulse.capabilities as capabilities  # noqa: F401 — force register decorators to run


@pytest.fixture
def tmp_pwn_path(tmp_path: Path) -> Path:
    """Disposable target path for the write-primitive probe."""
    return tmp_path / "rce_probe"


@pytest.fixture
def three_points() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"v": [1, 2, 3], "geometry": [Point(i, 0) for i in range(3)]},
        crs="EPSG:3857",
    )


def test_calculate_cannot_call_np_save(three_points, tmp_pwn_path):
    """`np.save` must be rejected — the SafeNamespace raises AttributeError on
    any attribute outside the curated ufunc whitelist."""
    cap = cap_get("calculate")
    with pytest.raises(AttributeError):
        cap.execute(
            three_points,
            expressions={"x": f'np.save({str(tmp_pwn_path)!r}, v) or 0'},
        )
    assert not tmp_pwn_path.with_suffix(".npy").exists()


def test_calculate_cannot_access_np_load(three_points):
    """`np.load` must be rejected — it can read arbitrary files."""
    cap = cap_get("calculate")
    with pytest.raises(AttributeError):
        cap.execute(three_points, expressions={"x": "np.load('/etc/hostname') or 0"})


def test_calculate_blocks_chained_np_call(three_points, tmp_pwn_path):
    """Regression guard — `np.asarray(v).tofile(path)` is already blocked by the
    AST attribute-allowlist (only ``np.<X>`` directly listed in _CALC_ALLOWED
    is callable). This guard ensures the hardening work on `np.save`/`np.load`
    does not accidentally weaken this already-safe path.
    """
    cap = cap_get("calculate")
    with pytest.raises(AttributeError):
        cap.execute(
            three_points,
            expressions={"x": f'np.asarray(v).tofile({str(tmp_pwn_path)!r}) or 0'},
        )
    assert not tmp_pwn_path.exists()


def test_calculate_allows_safe_math(three_points):
    """Regression guard — pure arithmetic must still work after the lockdown."""
    cap = cap_get("calculate")
    out = cap.execute(three_points, expressions={"double": "v * 2"})
    assert list(out["double"]) == [2, 4, 6]
