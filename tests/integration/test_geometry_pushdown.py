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


# ===========================================================================
# Lot 3b — aggregating / two-layer / CRS caps
# ===========================================================================


from shapely.geometry import LineString  # noqa: E402

from gispulse.capabilities.vector.diff import SymmetricDifferenceCapability  # noqa: E402
from gispulse.capabilities.vector.reproject import ReprojectCapability  # noqa: E402
from gispulse.capabilities.vector.simplify import SimplifyCapability  # noqa: E402
from gispulse.capabilities.vector.union import UnionCapability  # noqa: E402


def _ref_layer() -> gpd.GeoDataFrame:
    from shapely.geometry import Polygon as _P

    return gpd.GeoDataFrame(
        {"id": [9]},
        geometry=[_P([(3, 3), (8, 3), (8, 8), (3, 8)])],
        crs="EPSG:2154",
    )


def _line_layer() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[
            LineString([(0, 0), (1, 0.1), (2, -0.1), (3, 0.05), (4, 0)]),
        ],
        crs="EPSG:2154",
    )


def test_union_duckdb_matches_python():
    gdf = _polys()
    py = UnionCapability().execute(gdf.copy())
    with DuckDBSession() as eng:
        sql = UnionCapability().execute_with_context(gdf, _ctx(eng, gdf, {}))
    assert len(sql) == len(py) == 1
    a, b = sql.geometry.iloc[0], py.geometry.iloc[0]
    assert abs(a.area - b.area) <= 1e-6 * max(b.area, 1.0)


def test_reproject_duckdb_matches_python():
    gdf = _polys()
    params = {"target_crs": "EPSG:4326"}
    py = ReprojectCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = ReprojectCapability().execute_with_context(gdf, _ctx(eng, gdf, params))
    assert str(sql.crs) == str(py.crs) == "EPSG:4326"
    for a, b in zip(sorted(sql.geometry, key=lambda g: g.centroid.x),
                    sorted(py.geometry, key=lambda g: g.centroid.x)):
        assert abs(a.centroid.x - b.centroid.x) <= 1e-6
        assert abs(a.centroid.y - b.centroid.y) <= 1e-6


def test_symmetric_difference_duckdb_matches_python():
    gdf, ref = _polys(), _ref_layer()
    params = {"ref_gdf": ref}
    py = SymmetricDifferenceCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = SymmetricDifferenceCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    _assert_geom_equivalent(sql, py, rel_tol=1e-6, label="symmetric_difference duckdb")


def test_simplify_duckdb_matches_python():
    gdf = _line_layer()
    params = {"tolerance": 0.5}
    py = SimplifyCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = SimplifyCapability().execute_with_context(gdf, _ctx(eng, gdf, params))
    # GEOS TopologyPreservingSimplifier in both — vertex counts must match.
    assert len(sql) == len(py) == 1
    assert (
        len(sql.geometry.iloc[0].coords) == len(py.geometry.iloc[0].coords)
    )


def test_simplify_non_dp_falls_back_to_python():
    gdf = _line_layer()
    params = {"tolerance": 0.5, "algorithm": "vw"}
    with DuckDBSession() as eng:
        result = SimplifyCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    # The vw path is Python-only; behaviour must equal the plain execute.
    expected = SimplifyCapability().execute(gdf.copy(), **params)
    assert len(result) == len(expected)


# ===========================================================================
# Lot 3c — dissolve + spatial_join
# ===========================================================================


from shapely.geometry import Point  # noqa: E402

from gispulse.capabilities.vector.dissolve import DissolveCapability  # noqa: E402
from gispulse.capabilities.vector.spatial_join import (  # noqa: E402
    SpatialJoinCapability,
)


def _group_layer() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3, 4],
            "name": ["a", "b", "c", "d"],
            "pop": [10, 20, 30, 40],
            "grp": ["x", "x", "y", "y"],
        },
        geometry=[Point(0, 0), Point(1, 1), Point(5, 5), Point(6, 6)],
        crs="EPSG:2154",
    )


def _zone_layer() -> gpd.GeoDataFrame:
    from shapely.geometry import Polygon as _P

    return gpd.GeoDataFrame(
        {"id": [10, 20], "region": ["XX", "YY"]},
        geometry=[
            _P([(-1, -1), (3, -1), (3, 3), (-1, 3)]),
            _P([(4, 4), (8, 4), (8, 8), (4, 8)]),
        ],
        crs="EPSG:2154",
    )


def test_dissolve_by_group_duckdb_matches_python():
    gdf = _group_layer()
    params = {"by": "grp"}
    py = DissolveCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = DissolveCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    assert set(sql.columns) == set(py.columns)
    assert sorted(sql["grp"].tolist()) == sorted(py["grp"].tolist())
    # Each unioned geometry covers the same envelope.
    py_b = dict(zip(py["grp"], py.geometry))
    sql_b = dict(zip(sql["grp"], sql.geometry))
    for grp, geom in py_b.items():
        a = sql_b[grp]
        ref = max(geom.envelope.area, 1e-9)
        sym = geom.symmetric_difference(a).area
        assert sym / ref <= 1e-6, f"dissolve grp={grp}: geometries diverge"


def test_dissolve_no_by_falls_back_to_python():
    """by=None pushes through Python (avoids the .reset_index() 'index' col)."""
    gdf = _group_layer()
    with DuckDBSession() as eng:
        sql = DissolveCapability().execute_with_context(
            gdf, _ctx(eng, gdf, {})
        )
    py = DissolveCapability().execute(gdf.copy())
    assert len(sql) == len(py) == 1
    assert set(sql.columns) == set(py.columns)


def test_spatial_join_duckdb_matches_python():
    gdf = _group_layer()
    ref = _zone_layer()
    params = {"ref_gdf": ref, "predicate": "intersects"}
    py = SpatialJoinCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = SpatialJoinCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    assert set(sql.columns) == set(py.columns), (
        f"spatial_join columns differ — sql {sorted(sql.columns)} vs "
        f"py {sorted(py.columns)}"
    )
    assert len(sql) == len(py)
    # Compare rows by (id_left, id_right) pairs — order-independent.
    pairs_sql = sorted(zip(sql["id_left"], sql["id_right"]))
    pairs_py = sorted(zip(py["id_left"], py["id_right"]))
    assert pairs_sql == pairs_py


def test_spatial_join_crs_mismatch_falls_back_to_python():
    """SQL declines when the two layers don't share a CRS — Python reprojects."""
    gdf = _group_layer()
    ref = _zone_layer().to_crs("EPSG:4326")
    params = {"ref_gdf": ref, "predicate": "intersects"}
    py = SpatialJoinCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = SpatialJoinCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    assert set(sql.columns) == set(py.columns)
    assert len(sql) == len(py)


def test_spatial_join_ref_filter_translates():
    gdf = _group_layer()
    ref = _zone_layer()
    params = {
        "ref_gdf": ref,
        "predicate": "intersects",
        "ref_filter": "region == 'XX'",
    }
    py = SpatialJoinCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = SpatialJoinCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    assert len(sql) == len(py)
    assert set(sql["region"].dropna().unique()) == {"XX"}


# ===========================================================================
# Lot 3d — nearest_neighbor + overlay (intersection, erase)
# ===========================================================================


from gispulse.capabilities.overlay import (  # noqa: E402
    EraseCapability,
    OverlayIntersectionCapability,
)
from gispulse.capabilities.vector.nearest import (  # noqa: E402
    NearestNeighborCapability,
)


def _overlay_a() -> gpd.GeoDataFrame:
    from shapely.geometry import Polygon as _P

    return gpd.GeoDataFrame(
        {"id": [1, 2], "name": ["A", "B"]},
        geometry=[
            _P([(0, 0), (10, 0), (10, 10), (0, 10)]),
            _P([(15, 0), (25, 0), (25, 10), (15, 10)]),
        ],
        crs="EPSG:2154",
    )


def _overlay_b() -> gpd.GeoDataFrame:
    from shapely.geometry import Polygon as _P

    return gpd.GeoDataFrame(
        {"id": [1, 2], "region": ["X", "Y"]},
        geometry=[
            _P([(5, -5), (20, -5), (20, 15), (5, 15)]),
            _P([(8, 2), (12, 2), (12, 8), (8, 8)]),
        ],
        crs="EPSG:2154",
    )


def test_overlay_intersection_duckdb_matches_python():
    a, b = _overlay_a(), _overlay_b()
    params = {"ref_gdf": b}
    py = OverlayIntersectionCapability().execute(a.copy(), **params)
    with DuckDBSession() as eng:
        sql = OverlayIntersectionCapability().execute_with_context(
            a, _ctx(eng, a, params)
        )
    assert set(sql.columns) == set(py.columns)
    assert len(sql) == len(py)
    assert sorted(round(g.area, 6) for g in sql.geometry) == sorted(
        round(g.area, 6) for g in py.geometry
    )


def test_overlay_intersection_non_default_suffix_falls_back():
    a, b = _overlay_a(), _overlay_b()
    params = {"ref_gdf": b, "suffix_left": "_a", "suffix_right": "_b"}
    py = OverlayIntersectionCapability().execute(a.copy(), **params)
    with DuckDBSession() as eng:
        sql = OverlayIntersectionCapability().execute_with_context(
            a, _ctx(eng, a, params)
        )
    assert set(sql.columns) == set(py.columns)


def test_erase_duckdb_matches_python():
    a, b = _overlay_a(), _overlay_b()
    params = {"ref_gdf": b}
    py = EraseCapability().execute(a.copy(), **params)
    with DuckDBSession() as eng:
        sql = EraseCapability().execute_with_context(a, _ctx(eng, a, params))
    assert set(sql.columns) == set(py.columns)
    assert len(sql) == len(py)
    assert sorted(round(g.area, 6) for g in sql.geometry) == sorted(
        round(g.area, 6) for g in py.geometry
    )


def test_nearest_neighbor_duckdb_falls_back_to_python():
    """DuckDB has no <-> operator — the strategy declines, Python runs."""
    a, b = _overlay_a(), _overlay_b()
    params = {"ref_gdf": b, "k": 1}
    py = NearestNeighborCapability().execute(a.copy(), **params)
    with DuckDBSession() as eng:
        sql = NearestNeighborCapability().execute_with_context(
            a, _ctx(eng, a, params)
        )
    assert set(sql.columns) == set(py.columns)
    assert len(sql) == len(py)


@_requires_postgis
def test_nearest_neighbor_postgis_matches_python():
    from gispulse.persistence.postgis import PostGISConnection

    a, b = _overlay_a(), _overlay_b()
    params = {"ref_gdf": b, "k": 1}
    py = NearestNeighborCapability().execute(a.copy(), **params)
    pg = PostGISConnection(dsn=TEST_DSN)
    pg.open()
    try:
        sql = NearestNeighborCapability().execute_with_context(
            a, _ctx(pg, a, params)
        )
    finally:
        pg.close()
    assert set(sql.columns) == set(py.columns)
    assert len(sql) == len(py)


# ===========================================================================
# Lot 3e — temporal_filter + temporal_join (exact strategy)
# ===========================================================================


import pandas as pd  # noqa: E402

from gispulse.capabilities.temporal import (  # noqa: E402
    TemporalFilterCapability,
    TemporalJoinCapability,
)


def _temporal_layer() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3, 4],
            "ts": pd.to_datetime(
                ["2025-01-01", "2025-06-01", "2025-12-01", "2026-03-01"]
            ),
            "name": ["a", "b", "c", "d"],
        },
        geometry=[Point(i, i) for i in range(4)],
        crs="EPSG:4326",
    )


def _temporal_ref() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": pd.to_datetime(["2025-06-01", "2026-03-01"]),
            "id": [10, 20],
            "weather": ["sunny", "rainy"],
        }
    )


def test_temporal_filter_window_duckdb_matches_python():
    gdf = _temporal_layer()
    params = {"time_col": "ts", "start": "2025-06-01", "end": "2025-12-31"}
    py = TemporalFilterCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = TemporalFilterCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    assert set(sql.columns) == set(py.columns)
    assert sorted(sql["id"].tolist()) == sorted(py["id"].tolist())


def test_temporal_filter_invert_drops_nulls_like_python():
    gdf = _temporal_layer()
    params = {
        "time_col": "ts",
        "start": "2025-06-01",
        "end": "2025-12-31",
        "invert": True,
    }
    py = TemporalFilterCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = TemporalFilterCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    assert sorted(sql["id"].tolist()) == sorted(py["id"].tolist())


def test_temporal_filter_open_end_pushed():
    gdf = _temporal_layer()
    params = {"time_col": "ts", "start": "2025-06-01"}
    py = TemporalFilterCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = TemporalFilterCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    assert sorted(sql["id"].tolist()) == sorted(py["id"].tolist())


def test_temporal_join_exact_duckdb_matches_python():
    gdf = _temporal_layer()
    ref = _temporal_ref()
    params = {
        "ref_gdf": ref,
        "left_on": "ts",
        "right_on": "ts",
        "strategy": "exact",
    }
    py = TemporalJoinCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = TemporalJoinCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    assert set(sql.columns) == set(py.columns)
    assert len(sql) == len(py)
    sql_pairs = sorted(
        zip(sql["id"], sql["weather"].fillna("-")), key=lambda t: t[0]
    )
    py_pairs = sorted(
        zip(py["id"], py["weather"].fillna("-")), key=lambda t: t[0]
    )
    assert sql_pairs == py_pairs


def test_temporal_join_asof_falls_back_to_python():
    """Asof strategies (backward / forward / nearest) stay on Python."""
    gdf = _temporal_layer()
    ref = _temporal_ref()
    params = {
        "ref_gdf": ref,
        "left_on": "ts",
        "right_on": "ts",
        "strategy": "backward",
    }
    py = TemporalJoinCapability().execute(gdf.copy(), **params)
    with DuckDBSession() as eng:
        sql = TemporalJoinCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    assert set(sql.columns) == set(py.columns)
    assert len(sql) == len(py)
