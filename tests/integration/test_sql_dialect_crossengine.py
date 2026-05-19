"""Cross-dialect result-for-result harness — ELT Lot 1 (#244).

Verifies that the SQL emitted by ``gispulse.persistence.spatial_queries``
produces *equal results* across engines for the three SQL-pushed
geometry operations (``buffer`` / ``clip`` / ``intersects``).

Two axes of comparison, both "result for result":

1. **DuckDB SQL vs the GeoPandas/Shapely reference** — runs
   unconditionally. This is the always-on correctness gate: it catches a
   builder regression even when no PostGIS server is around.
2. **DuckDB SQL vs PostGIS SQL** — runs only when ``GISPULSE_TEST_DSN``
   points at a live PostGIS, otherwise skipped. This is the genuine
   cross-engine check.

The harness deliberately drives the engines through the *generated SQL*,
not the capability strategies, so it tests the dialect layer in
isolation.
"""

from __future__ import annotations

import os

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from gispulse.persistence.duckdb_engine import DuckDBSession
from gispulse.persistence.sql_dialect import get_dialect
from gispulse.persistence.spatial_queries import (
    buffer_select,
    clip_select,
    intersects_select,
)

TEST_DSN: str | None = os.environ.get("GISPULSE_TEST_DSN")

_requires_postgis = pytest.mark.skipif(
    TEST_DSN is None,
    reason="GISPULSE_TEST_DSN not set — skipping the DuckDB↔PostGIS cross-check",
)


# ---------------------------------------------------------------------------
# Fixtures — inputs in a metric CRS (EPSG:2154) so buffers stay planar
# ---------------------------------------------------------------------------


@pytest.fixture
def points() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3]},
        geometry=[
            Point(652000, 6862000),
            Point(653000, 6863000),
            Point(700000, 6900000),
        ],
        crs="EPSG:2154",
    )


@pytest.fixture
def squares() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            Polygon([(20, 0), (30, 0), (30, 10), (20, 10)]),
        ],
        crs="EPSG:2154",
    )


@pytest.fixture
def mask() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        geometry=[Polygon([(5, -5), (25, -5), (25, 15), (5, 15)])],
        crs="EPSG:2154",
    )


# ---------------------------------------------------------------------------
# Comparison helper
# ---------------------------------------------------------------------------


def _assert_same_geometries(
    a: gpd.GeoDataFrame,
    b: gpd.GeoDataFrame,
    *,
    rel_tol: float,
    label: str,
) -> None:
    """Assert two result sets hold the same geometries.

    Order-independent: both sides are sorted by centroid. Each geometry
    pair must agree to within *rel_tol* of its area, measured as the
    symmetric-difference area ratio — robust for both exact polygon
    overlay (clip) and faceted curves (buffer), where two engines pick
    different but equivalent vertex sets.
    """
    assert len(a) == len(b), f"{label}: row count {len(a)} != {len(b)}"
    if len(a) == 0:
        return

    def _sorted(gdf: gpd.GeoDataFrame) -> list:
        geoms = list(gdf.geometry)
        return sorted(geoms, key=lambda g: (round(g.centroid.x, 3), round(g.centroid.y, 3)))

    for ga, gb in zip(_sorted(a), _sorted(b)):
        ref_area = max(ga.area, gb.area, 1e-9)
        sym = ga.symmetric_difference(gb).area
        assert sym / ref_area <= rel_tol, (
            f"{label}: geometries diverge — symmetric-difference ratio "
            f"{sym / ref_area:.2e} exceeds {rel_tol:.0e}"
        )


def _registered_table(engine, name: str) -> str:
    """Return the table name an engine's ``register`` actually creates."""
    if engine.backend_name == "postgis":
        # PostGISConnection.register writes to a `_gispulse_tmp_` table.
        return f"_gispulse_tmp_{name}"
    return name


def _finish(query, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Apply the same geometry-column promotion the strategies apply."""
    if query.geom_column in gdf.columns:
        gdf = gdf.set_geometry(query.geom_column).rename_geometry("geometry")
    return gdf


# ---------------------------------------------------------------------------
# SQL execution per engine
# ---------------------------------------------------------------------------


def _run_buffer(engine, gdf: gpd.GeoDataFrame, distance: float) -> gpd.GeoDataFrame:
    engine.register("_input", gdf)
    q = buffer_select(
        get_dialect(engine.backend_name),
        source_table=_registered_table(engine, "_input"),
        distance=distance,
    )
    return _finish(q, engine.sql_to_gdf(q.sql))


def _run_intersects(
    engine, gdf: gpd.GeoDataFrame, ref: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    engine.register("_is_input", gdf)
    engine.register("_is_ref", ref)
    q = intersects_select(
        get_dialect(engine.backend_name),
        source_table=_registered_table(engine, "_is_input"),
        ref_table=_registered_table(engine, "_is_ref"),
    )
    return _finish(q, engine.sql_to_gdf(q.sql))


def _run_clip(
    engine, gdf: gpd.GeoDataFrame, mask_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    engine.register("_clip_input", gdf)
    engine.register("_clip_mask", mask_gdf)
    q = clip_select(
        get_dialect(engine.backend_name),
        source_table=_registered_table(engine, "_clip_input"),
        mask_table=_registered_table(engine, "_clip_mask"),
    )
    return _finish(q, engine.sql_to_gdf(q.sql))


# ---------------------------------------------------------------------------
# Axis 1 — DuckDB SQL vs the Shapely reference (always runs)
# ---------------------------------------------------------------------------


class TestDuckDBMatchesShapely:
    """The generated DuckDB SQL must agree with the GeoPandas reference."""

    def test_buffer(self, points):
        with DuckDBSession() as eng:
            sql_result = _run_buffer(eng, points, 100.0)
        reference = points.copy()
        reference["geometry"] = points.geometry.buffer(100.0)
        # Buffer is a curved op: DuckDB and Shapely facet the circle with
        # a different number of segments (32-gon vs 64-gon), so the tol
        # is set at the facet-discretization level, not machine epsilon.
        _assert_same_geometries(
            sql_result, reference, rel_tol=2e-2, label="buffer"
        )

    def test_intersects(self, squares, mask):
        with DuckDBSession() as eng:
            sql_result = _run_intersects(eng, squares, mask)
        ref_geom = mask.geometry.union_all()
        reference = squares[squares.geometry.intersects(ref_geom)]
        _assert_same_geometries(
            sql_result, reference, rel_tol=1e-6, label="intersects"
        )

    def test_clip(self, squares, mask):
        with DuckDBSession() as eng:
            sql_result = _run_clip(eng, squares, mask)
        reference = gpd.clip(squares, mask)
        _assert_same_geometries(
            sql_result, reference, rel_tol=1e-6, label="clip"
        )


# ---------------------------------------------------------------------------
# Axis 2 — DuckDB SQL vs PostGIS SQL (requires GISPULSE_TEST_DSN)
# ---------------------------------------------------------------------------


@_requires_postgis
class TestDuckDBMatchesPostGIS:
    """The same builder, run on both SQL engines, must agree result-for-result."""

    @pytest.fixture
    def postgis(self):
        from gispulse.persistence.postgis import PostGISConnection

        engine = PostGISConnection(dsn=TEST_DSN)
        engine.open()
        try:
            yield engine
        finally:
            engine.close()

    def test_buffer(self, points, postgis):
        with DuckDBSession() as duck:
            duck_result = _run_buffer(duck, points, 100.0)
        pg_result = _run_buffer(postgis, points, 100.0)
        # Facet-discretization tolerance — see TestDuckDBMatchesShapely.
        _assert_same_geometries(
            duck_result, pg_result, rel_tol=2e-2, label="buffer x-engine"
        )

    def test_intersects(self, squares, mask, postgis):
        with DuckDBSession() as duck:
            duck_result = _run_intersects(duck, squares, mask)
        pg_result = _run_intersects(postgis, squares, mask)
        _assert_same_geometries(
            duck_result, pg_result, rel_tol=1e-6, label="intersects x-engine"
        )

    def test_clip(self, squares, mask, postgis):
        with DuckDBSession() as duck:
            duck_result = _run_clip(duck, squares, mask)
        pg_result = _run_clip(postgis, squares, mask)
        _assert_same_geometries(
            duck_result, pg_result, rel_tol=1e-6, label="clip x-engine"
        )
