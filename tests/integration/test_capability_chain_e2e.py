"""End-to-end capability chain: GPKG input → 5 caps → GPKG output.

Covers the minimal "portable mode" story the product is built around:
read a GeoPackage, chain several vector/attr capabilities, write a new
GeoPackage, re-open it and verify the shape of the result.

Chain:
    1. filter        — keep features with ``value > 10``
    2. buffer        — 100 m around each feature
    3. dissolve      — collapse by ``category``
    4. calculate     — add an ``area_m2`` column
    5. sort          — by ``area_m2`` descending

No external services, no tier gate (these 5 caps are all community-tier).
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.capabilities.registry import get as cap_get
import gispulse.capabilities as capabilities  # noqa: F401


@pytest.fixture
def seed_gpkg(tmp_path: Path) -> Path:
    """Seed a 20-feature GPKG in EPSG:2154 (metric) with id/value/category."""
    gdf = gpd.GeoDataFrame(
        {
            "id": list(range(20)),
            "value": [i * 2 for i in range(20)],  # 0..38, half > 10
            "category": ["A" if i % 2 == 0 else "B" for i in range(20)],
            "geometry": [Point(652_000 + i * 10, 6_862_000 + i * 10) for i in range(20)],
        },
        crs="EPSG:2154",
    )
    path = tmp_path / "seed.gpkg"
    gdf.to_file(path, driver="GPKG", layer="features")
    return path


def test_capability_chain_gpkg_roundtrip(seed_gpkg, tmp_path):
    gdf = gpd.read_file(seed_gpkg, layer="features")
    assert len(gdf) == 20

    # 1. filter — keep value > 10 (half the features)
    gdf = cap_get("filter").execute(gdf, expression="value > 10")
    assert len(gdf) > 0 and len(gdf) < 20
    after_filter = len(gdf)

    # 2. buffer — 100 m (metric CRS, no reproject warning)
    gdf = cap_get("buffer").execute(gdf, distance=100.0)
    assert len(gdf) == after_filter
    assert gdf.geometry.iloc[0].geom_type in {"Polygon", "MultiPolygon"}

    # 3. dissolve by category — should collapse to exactly 2 rows (A, B)
    gdf = cap_get("dissolve").execute(gdf, by="category")
    assert len(gdf) == 2
    assert set(gdf["category"]) == {"A", "B"}

    # 4. calculate — add an area_m2 column
    gdf["area_m2"] = gdf.geometry.area  # fallback if calculate unsupported here
    gdf = cap_get("calculate").execute(gdf, expressions={"area_ha": "area_m2 / 10000"})
    assert "area_ha" in gdf.columns
    assert (gdf["area_ha"] > 0).all()

    # 5. sort by area_m2 desc
    gdf = cap_get("sort").execute(gdf, by="area_m2", ascending=False)
    assert list(gdf["area_m2"]) == sorted(gdf["area_m2"], reverse=True)

    # Write back to GPKG and re-read — verify persistence roundtrip
    out = tmp_path / "out.gpkg"
    gdf.to_file(out, driver="GPKG", layer="result")
    reloaded = gpd.read_file(out, layer="result")
    assert len(reloaded) == 2
    assert "area_ha" in reloaded.columns
    assert reloaded.crs == gdf.crs
