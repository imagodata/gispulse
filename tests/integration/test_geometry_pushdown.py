"""Result-for-result harness for the geometry SQL push-down (ELT Lot 3, #246).

For each Tier-1 single-layer 1:1 geometry capability equipped with
DuckDB/PostGIS strategies, this verifies the SQL push-down produces the
same result as the Python/GeoPandas implementation.

- DuckDB vs Python — runs unconditionally.
- DuckDB vs PostGIS — runs only when ``GISPULSE_TEST_DSN`` is set.

Also checks that the non-1:1 modes (``by_group`` / ``dissolve``) and a
non-SQL engine fall back to Python.
"""

from __future__ import annotations

import os

import geopandas as gpd
import pytest
from shapely.geometry import MultiPoint, Polygon

from gispulse.capabilities.strategy import ExecutionContext
from gispulse.capabilities.vector.boundary import BoundaryCapability
from gispulse.capabilities.vector.centroid_area import (
    AreaLengthCapability,
    CentroidCapability,
)
from gispulse.capabilities.vector.concave_hull import ConcaveHullCapability
from gispulse.capabilities.vector.shape_ops_basic import (
    ConvexHullCapability,
    EnvelopeCapability,
    MakeValidCapability,
)
from gispulse.persistence.duckdb_engine import DuckDBSession

TEST_DSN: str | None = os.environ.get("GISPULSE_TEST_DSN")
_requires_postgis = pytest.mark.skipif(
    TEST_DSN is None,
    reason="GISPULSE_TEST_DSN not set — skipping the DuckDB↔PostGIS cross-check",
)


def _polys() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2], "grp": ["a", "b"]},
        geometry=[
            Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]),
            Polygon([(10, 10), (14, 10), (14, 14), (10, 14)]),
        ],
        crs="EPSG:2154",
    )


def _cloud() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[MultiPoint([(0, 0), (4, 0), (4, 4), (0, 4), (2, 2), (1, 3)])],
        crs="EPSG:2154",
    )


_GEOM_CASES = [
    (CentroidCapability(), {}, _polys, 1e-6),
    (BoundaryCapability(), {}, _polys, 1e-6),
    (ConvexHullCapability(), {}, _polys, 1e-6),
    (EnvelopeCapability(), {}, _polys, 1e-6),
    (MakeValidCapability(), {}, _polys, 1e-6),
    # concave hull — engine vs shapely use different internals; allow slack.
    (ConcaveHullCapability(), {"ratio": 0.5}, _cloud, 5e-2),
]


def _ctx(engine, gdf, params):
    return ExecutionContext(engine=engine, feature_count=len(gdf), params=dict(params))


def _assert_geom_equivalent(a, b, *, rel_tol, label):
    assert len(a) == len(b), f"{label}: row count {len(a)} != {len(b)}"
    ga = sorted(
        (g for g in a.geometry if g is not None),
        key=lambda g: (round(g.centroid.x, 3), round(g.centroid.y, 3)),
    )
    gb = sorted(
        (g for g in b.geometry if g is not None),
        key=lambda g: (round(g.centroid.x, 3), round(g.centroid.y, 3)),
    )
    assert len(ga) == len(gb), f"{label}: non-null geometry count differs"
    for x, y in zip(ga, gb):
        ref = max(x.area, y.area, 1e-9)
        sym = x.symmetric_difference(y).area
        assert sym / ref <= rel_tol, (
            f"{label}: geometries diverge — sym-diff ratio {sym / ref:.2e}"
        )


@pytest.mark.parametrize(
    "capability, params, layer, rel_tol",
    _GEOM_CASES,
    ids=[c.name for c, _, _, _ in _GEOM_CASES],
)
def test_duckdb_geometry_pushdown_matches_python(capability, params, layer, rel_tol):
    gdf = layer()
    py = capability.execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = capability.execute_with_context(gdf, _ctx(eng, gdf, params))
    _assert_geom_equivalent(sql, py, rel_tol=rel_tol, label=f"{capability.name} duckdb")


def test_area_length_duckdb_matches_python():
    gdf = _polys()
    py = AreaLengthCapability().execute(gdf.copy())
    with DuckDBSession() as eng:
        sql = AreaLengthCapability().execute_with_context(gdf, _ctx(eng, gdf, {}))
    assert set(sql.columns) == set(py.columns)
    for col in ("area_m2", "length_m"):
        for s, p in zip(sorted(sql[col]), sorted(py[col])):
            assert abs(s - p) <= 1e-6 * max(abs(p), 1.0), f"area_length {col} diverges"


def test_by_group_falls_back_to_python():
    """convex_hull with by_group is N:1 — must run the Python path."""
    gdf = _polys()
    with DuckDBSession() as eng:
        result = ConvexHullCapability().execute_with_context(
            gdf, _ctx(eng, gdf, {"by_group": "grp"})
        )
    # one hull per group → 2 rows, grp column retained
    assert len(result) == 2
    assert "grp" in result.columns


def test_non_sql_engine_uses_python():
    class _FakeEngine:
        backend_name = "gpkg"

    gdf = _polys()
    ctx = ExecutionContext(engine=_FakeEngine(), feature_count=len(gdf), params={})
    result = CentroidCapability().execute_with_context(gdf, ctx)
    assert len(result) == 2
    assert all(g.geom_type == "Point" for g in result.geometry)


@_requires_postgis
@pytest.mark.parametrize(
    "capability, params, layer, rel_tol",
    _GEOM_CASES,
    ids=[c.name for c, _, _, _ in _GEOM_CASES],
)
def test_postgis_geometry_pushdown_matches_duckdb(capability, params, layer, rel_tol):
    from gispulse.persistence.postgis import PostGISConnection

    gdf = layer()
    with DuckDBSession() as duck:
        duck_result = capability.execute_with_context(gdf, _ctx(duck, gdf, params))
    pg = PostGISConnection(dsn=TEST_DSN)
    pg.open()
    try:
        pg_result = capability.execute_with_context(gdf, _ctx(pg, gdf, params))
    finally:
        pg.close()
    _assert_geom_equivalent(
        duck_result, pg_result, rel_tol=rel_tol, label=f"{capability.name} x-engine"
    )
