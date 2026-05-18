"""Validation that ``Capability.execute_safe`` rejects typo'd kwargs.

Beta finding P2-3 (reclassed P1 in EPIC-1 v1.2.0): the ``**_`` placeholder
on every capability's ``execute()`` method silently swallowed typos like
``AddFieldCapability(fild="...")`` — the GDF was returned unchanged with
no warning, masking pipeline-config bugs.

The fix is a single dispatcher-level guard (`Capability.execute_safe`)
that introspects the concrete subclass's ``execute()`` signature and
raises :class:`UnknownParameterError` for kwargs that don't match a
declared parameter. Calling ``execute()`` directly is still permissive —
that path is reserved for unit tests and intentional bypass.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.capabilities.base import Capability, UnknownParameterError


# ---------------------------------------------------------------------------
# Fixture capability with the ubiquitous ``**_`` placeholder
# ---------------------------------------------------------------------------


class _BufferCapability(Capability):
    """Minimal capability mirroring the legacy ``**_`` swallow pattern."""

    name = "buffer_test"
    description = "Test fixture"

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        distance: float = 1.0,
        cap_style: str = "round",
        **_,
    ) -> gpd.GeoDataFrame:
        out = gdf.copy()
        out["distance_used"] = distance
        out["cap_style_used"] = cap_style
        return out


@pytest.fixture
def gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_execute_safe_accepts_declared_kwargs(gdf):
    out = _BufferCapability().execute_safe(gdf, distance=5.0, cap_style="flat")
    assert out["distance_used"].iloc[0] == 5.0
    assert out["cap_style_used"].iloc[0] == "flat"


def test_execute_safe_accepts_no_kwargs(gdf):
    out = _BufferCapability().execute_safe(gdf)
    assert out["distance_used"].iloc[0] == 1.0  # default


def test_execute_safe_accepts_partial_kwargs(gdf):
    out = _BufferCapability().execute_safe(gdf, distance=2.5)
    assert out["distance_used"].iloc[0] == 2.5
    assert out["cap_style_used"].iloc[0] == "round"  # default


# ---------------------------------------------------------------------------
# Reject path — the actual P2-3 / P1 fix
# ---------------------------------------------------------------------------


def test_execute_safe_rejects_typo_kwarg(gdf):
    with pytest.raises(UnknownParameterError) as exc:
        _BufferCapability().execute_safe(gdf, distence=5.0)  # typo
    msg = str(exc.value)
    assert "distence" in msg
    assert "buffer_test" in msg
    # Error message includes the accepted parameter list to help authors
    # spot the right name without digging into source.
    assert "distance" in msg
    assert "cap_style" in msg


def test_execute_safe_rejects_multiple_typos(gdf):
    with pytest.raises(UnknownParameterError) as exc:
        _BufferCapability().execute_safe(gdf, distence=5.0, kap_style="flat")
    msg = str(exc.value)
    assert "distence" in msg
    assert "kap_style" in msg


def test_execute_safe_unknown_parameter_error_is_typeerror(gdf):
    """Subclassing TypeError keeps existing ``except TypeError`` blocks working."""
    with pytest.raises(TypeError):
        _BufferCapability().execute_safe(gdf, fild="foo")


# ---------------------------------------------------------------------------
# Direct execute() bypass remains permissive (legacy contract)
# ---------------------------------------------------------------------------


def test_execute_direct_still_swallows_unknown_kwargs(gdf):
    """``execute()`` itself keeps the ``**_`` permissive contract.

    Only ``execute_safe`` validates. This preserves backwards-compat for
    unit tests that intentionally probe a capability with extra kwargs.
    """
    out = _BufferCapability().execute(gdf, distance=3.0, fild="ignored")
    assert out["distance_used"].iloc[0] == 3.0


# ---------------------------------------------------------------------------
# Real capability — sanity check via execute_safe through the registry
# ---------------------------------------------------------------------------


def test_execute_safe_via_real_capability_top_n(gdf):
    """Smoke-test against a registered capability to make sure the
    introspection works on real signatures (not just the test fixture).
    """
    from gispulse.capabilities.selection import TopNCapability

    gdf_pop = gdf.copy()
    gdf_pop["population"] = [1000, 2000]

    out = TopNCapability().execute_safe(gdf_pop, n=1, by="population")
    assert len(out) == 1
    assert out["population"].iloc[0] == 2000

    with pytest.raises(UnknownParameterError) as exc:
        TopNCapability().execute_safe(gdf_pop, n=1, bay="population")  # typo
    assert "bay" in str(exc.value)
    assert "top_n" in str(exc.value)
