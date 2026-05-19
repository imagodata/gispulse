"""Result-for-result harness for the attribute SQL push-down (ELT Lot 2, #245).

For each of the 12 ``schema`` / ``selection`` capabilities equipped with
DuckDB/PostGIS strategies, this verifies that the SQL push-down path
produces the *same result* as the proven Python/GeoPandas implementation.

- DuckDB vs Python — runs unconditionally.
- DuckDB vs PostGIS — runs only when ``GISPULSE_TEST_DSN`` is set.

It also checks the opportunistic fallback: an untranslatable expression
and a non-SQL engine both fall back to Python silently.
"""

from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from gispulse.capabilities.schema import (
    AddFieldCapability,
    AttributeJoinCapability,
    CaseWhenCapability,
    CastFieldCapability,
    CoalesceFieldsCapability,
    DropFieldCapability,
    RenameFieldCapability,
    SelectColumnsCapability,
)
from gispulse.capabilities.selection import (
    DeduplicateCapability,
    SortCapability,
    TopNCapability,
)
from gispulse.capabilities.strategy import ExecutionContext
from gispulse.capabilities.vector.calculate import CalculateCapability
from gispulse.persistence.duckdb_engine import DuckDBSession

TEST_DSN: str | None = os.environ.get("GISPULSE_TEST_DSN")
_requires_postgis = pytest.mark.skipif(
    TEST_DSN is None,
    reason="GISPULSE_TEST_DSN not set — skipping the DuckDB↔PostGIS cross-check",
)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _layer() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [3, 1, 2, 4],
            "name": ["c", "a", "b", "d"],
            "pop": [30, 10, 20, 40],
            "grp": ["x", "x", "y", "y"],
        },
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3)],
        crs="EPSG:4326",
    )


# (capability, params) — every case is SQL-translatable on purpose.
_CASES: list[tuple[object, dict]] = [
    (SelectColumnsCapability(), {"fields": ["id", "name"]}),
    (DropFieldCapability(), {"fields": ["grp"]}),
    (RenameFieldCapability(), {"mapping": {"pop": "population"}}),
    (AddFieldCapability(), {"fields": [{"name": "tag", "dtype": "string", "default": "z"}]}),
    (CastFieldCapability(), {"casts": {"pop": "float"}}),
    (CoalesceFieldsCapability(), {"sources": ["name", "grp"], "target_col": "label"}),
    (
        CaseWhenCapability(),
        {
            "target_col": "tier",
            "cases": [
                {"when": "pop > 25", "then": "big"},
                {"when": "pop > 15", "then": "mid"},
            ],
            "else_": "small",
        },
    ),
    (CalculateCapability(), {"expressions": {"dbl": "pop * 2", "ratio": "pop / 10"}}),
    (SortCapability(), {"by": ["pop"], "ascending": True}),
    (TopNCapability(), {"n": 2, "by": "pop", "ascending": False}),
    (DeduplicateCapability(), {"keys": ["grp"], "order_by": ["pop"], "keep": "last"}),
]


def _ctx(engine, gdf, params):
    return ExecutionContext(engine=engine, feature_count=len(gdf), params=dict(params))


def _normalise(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Drop geometry, coerce dtypes away, sort — for order/dtype-free compare."""
    geom = gdf.geometry.name if hasattr(gdf, "geometry") else None
    df = pd.DataFrame(gdf.drop(columns=[geom], errors="ignore"))
    df = df.astype(object).where(pd.notna(df), None)
    return df.sort_values(by=list(df.columns)).reset_index(drop=True)


def _assert_equivalent(sql_result, py_result, label: str) -> None:
    a, b = _normalise(sql_result), _normalise(py_result)
    assert set(a.columns) == set(b.columns), (
        f"{label}: column sets differ — SQL {sorted(a.columns)} vs "
        f"Python {sorted(b.columns)}"
    )
    a = a[sorted(a.columns)]
    b = b[sorted(b.columns)]
    a = a.sort_values(by=list(a.columns)).reset_index(drop=True)
    b = b.sort_values(by=list(b.columns)).reset_index(drop=True)
    assert len(a) == len(b), f"{label}: row count {len(a)} != {len(b)}"
    assert a.values.tolist() == b.values.tolist(), (
        f"{label}: values differ\nSQL:\n{a}\nPython:\n{b}"
    )


# ---------------------------------------------------------------------------
# DuckDB vs Python — always runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "capability, params",
    _CASES,
    ids=[c.name for c, _ in _CASES],
)
def test_duckdb_pushdown_matches_python(capability, params):
    gdf = _layer()
    python_result = capability.execute(gdf, **params)
    with DuckDBSession() as eng:
        sql_result = capability.execute_with_context(gdf, _ctx(eng, gdf, params))
    _assert_equivalent(sql_result, python_result, f"{capability.name} duckdb")


def test_attribute_join_duckdb_matches_python():
    gdf = _layer()
    ref = pd.DataFrame({"id": [1, 2, 3, 4], "region": ["A", "B", "C", "D"]})
    params = {"ref_gdf": ref, "left_on": "id", "columns": ["region"]}
    python_result = AttributeJoinCapability().execute(gdf, **params)
    with DuckDBSession() as eng:
        sql_result = AttributeJoinCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    _assert_equivalent(sql_result, python_result, "attribute_join duckdb")


# ---------------------------------------------------------------------------
# Opportunistic fallback to Python
# ---------------------------------------------------------------------------


def test_untranslatable_expression_falls_back_to_python():
    """A calculate expression outside the SQL subset still produces the
    correct result via the Python strategy."""
    gdf = _layer()
    # `.clip()` is a method call — not SQL-translatable.
    params = {"expressions": {"capped": "pop.clip(upper=25)"}}
    with DuckDBSession() as eng:
        result = CalculateCapability().execute_with_context(
            gdf, _ctx(eng, gdf, params)
        )
    assert result["capped"].tolist() == [25, 10, 20, 25]


def test_non_sql_engine_uses_python(monkeypatch):
    """A capability on a non-DuckDB/PostGIS engine runs the Python path."""

    class _FakeEngine:
        backend_name = "gpkg"

    gdf = _layer()
    ctx = ExecutionContext(
        engine=_FakeEngine(), feature_count=len(gdf), params={"fields": ["id"]}
    )
    result = SelectColumnsCapability().execute_with_context(gdf, ctx)
    assert set(result.columns) == {"id", "geometry"}


# ---------------------------------------------------------------------------
# DuckDB vs PostGIS — requires GISPULSE_TEST_DSN
# ---------------------------------------------------------------------------


@_requires_postgis
@pytest.mark.parametrize(
    "capability, params",
    _CASES,
    ids=[c.name for c, _ in _CASES],
)
def test_postgis_pushdown_matches_duckdb(capability, params):
    from gispulse.persistence.postgis import PostGISConnection

    gdf = _layer()
    with DuckDBSession() as duck:
        duck_result = capability.execute_with_context(gdf, _ctx(duck, gdf, params))
    pg = PostGISConnection(dsn=TEST_DSN)
    pg.open()
    try:
        pg_result = capability.execute_with_context(gdf, _ctx(pg, gdf, params))
    finally:
        pg.close()
    _assert_equivalent(duck_result, pg_result, f"{capability.name} x-engine")
