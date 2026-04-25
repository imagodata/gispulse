"""Shared test fixtures for GISPulse test suite.

Centralises common spatial fixtures (GeoDataFrames) to avoid duplication
across test modules.

Also seeds three env vars at *import time* so tests don't emit "config not
set" UserWarnings on every `create_app()`. Real values come from the
environment when CI/dev sets them; the defaults below are inert sentinels
safe enough for unit tests:

  - GISPULSE_CORS_ORIGINS — empty CORS list ("none") closes cross-origin.
  - GISPULSE_API_KEYS     — an opaque test key; tests that need real auth
                            override per-call.
  - GISPULSE_JWT_SECRET   — 32-byte random hex (only matters if a code path
                            uses JWT signing; no-op for capability tests).

Setting them via os.environ.setdefault preserves any value the user already
exported.
"""

from __future__ import annotations

import os
import secrets

# Seed before any GISPulse import touches core.config — this is why this
# block sits above the `import gispulse / capabilities / ...` lines.
os.environ.setdefault("GISPULSE_CORS_ORIGINS", "http://test")
# Empty default = auth disabled (matches prod behaviour when GISPULSE_API_KEYS
# is unset). Tests that exercise the auth path opt-in by setting the env
# var via monkeypatch. A non-empty default would activate auth globally and
# force every TestClient call to carry an X-API-Key header — which broke
# 32+ tests on 2026-04-25.
os.environ.setdefault("GISPULSE_API_KEYS", "")
# 32-byte hex (64 chars) — exceeds any reasonable min-length check.
os.environ.setdefault("GISPULSE_JWT_SECRET", secrets.token_hex(32))
# Default test tier = Pro with licence verification skipped.
# Tests that explicitly cover tier-gating behaviour override via
# monkeypatch.setenv (cf. test_full_pipeline.py, test_oidc.py, test_audit.py).
# Without this default, tests that hit Pro-gated endpoints (triggers,
# pipelines /execute, raster, network, cron) would all get HTTP 402.
os.environ.setdefault("GISPULSE_TIER", "pro")
os.environ.setdefault("GISPULSE_LICENCE_SKIP_VERIFY", "true")
# Format-valid fake key (Pro, expires 2030). Signature is zero bytes —
# only loadable when GISPULSE_LICENCE_SKIP_VERIFY=true.
os.environ.setdefault(
    "GISPULSE_LICENSE_KEY",
    "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ"
    ".AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
)

import pytest
import geopandas as gpd
from shapely.geometry import Point, Polygon


@pytest.fixture(autouse=True)
def _reset_tier_env(monkeypatch):
    """Reset GISPULSE_TIER and GISPULSE_LICENSE_KEY at the start of each test.

    Some legacy tests mutate ``os.environ`` directly without cleanup
    (e.g. ``os.environ["GISPULSE_TIER"] = "community"`` in
    ``test_capabilities_validation.py``). That used to be harmless when
    the suite-wide default was already "community", but became a
    cross-test pollution source once Pro became the default tier (needed
    after Pro-gating triggers_router and pipelines_router).
    """
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
    monkeypatch.setenv(
        "GISPULSE_LICENSE_KEY",
        "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ"
        ".AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    yield


@pytest.fixture
def point_gdf() -> gpd.GeoDataFrame:
    """Simple GeoDataFrame with 3 points in EPSG:4326."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "name": ["a", "b", "c"],
            "value": [10, 20, 30],
            "geometry": [
                Point(2.3522, 48.8566),  # Paris
                Point(2.3000, 48.8700),
                Point(2.4000, 48.9000),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def polygon_gdf() -> gpd.GeoDataFrame:
    """GeoDataFrame with 2 polygons in EPSG:4326."""
    poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    poly2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])
    return gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "category": ["A", "B"],
            "area_ha": [1.0, 2.0],
            "geometry": [poly1, poly2],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def mask_gdf() -> gpd.GeoDataFrame:
    """Small mask polygon for spatial filtering tests."""
    mask = Polygon([(1.5, -0.5), (2.5, -0.5), (2.5, 1.5), (1.5, 1.5)])
    return gpd.GeoDataFrame({"geometry": [mask]}, crs="EPSG:4326")


@pytest.fixture
def empty_gdf() -> gpd.GeoDataFrame:
    """Empty GeoDataFrame with geometry column."""
    return gpd.GeoDataFrame(
        {"id": [], "geometry": []},
        geometry="geometry",
        crs="EPSG:4326",
    )


@pytest.fixture
def projected_point_gdf() -> gpd.GeoDataFrame:
    """GeoDataFrame with 3 points in a projected CRS (EPSG:2154 - Lambert 93).

    Use for metric operations (buffer, area, distance) to avoid geographic CRS warnings.
    """
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "name": ["a", "b", "c"],
            "value": [10, 20, 30],
            "geometry": [
                Point(652000, 6862000),
                Point(652500, 6862500),
                Point(653000, 6863000),
            ],
        },
        crs="EPSG:2154",
    )
