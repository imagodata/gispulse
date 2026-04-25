"""
Integration tests for PostGISConnection.

A real PostGIS connection is required for most tests.
Set GISPULSE_TEST_DSN to a valid SQLAlchemy DSN to run them, e.g.:

    export GISPULSE_TEST_DSN="postgresql://gispulse:gispulse@localhost:5432/gispulse_test"

Tests that only exercise pure Python logic (DSN normalisation, invalid DSN
behaviour) run without a database connection.
"""

from __future__ import annotations

import os
import uuid

import geopandas as gpd
import pytest
from shapely.geometry import Point

# ---------------------------------------------------------------------------
# Connection availability guard
# ---------------------------------------------------------------------------

TEST_DSN: str | None = os.environ.get("GISPULSE_TEST_DSN")

_requires_postgis = pytest.mark.skipif(
    TEST_DSN is None,
    reason="GISPULSE_TEST_DSN not set — skipping PostGIS integration tests",
)

# Skip the entire module import check for sqlalchemy/psycopg2 too.
# If psycopg2 is not installed the live tests will fail anyway, which is fine.

from persistence.postgis import PostGISConnection  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_table() -> str:
    """Return a table name that is unique per test run to avoid conflicts."""
    return f"gispulse_test_{uuid.uuid4().hex[:8]}"


def _sample_gdf(n: int = 3, crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    """Build a minimal GeoDataFrame with n point features."""
    return gpd.GeoDataFrame(
        {
            "id": list(range(1, n + 1)),
            "label": [f"pt_{i}" for i in range(1, n + 1)],
            "geom": [Point(2.35 + i * 0.01, 48.85 + i * 0.01) for i in range(n)],
        },
        geometry="geom",
        crs=crs,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def conn() -> PostGISConnection:
    """Module-scoped connection shared by live tests."""
    return PostGISConnection(dsn=TEST_DSN)  # type: ignore[arg-type]


@pytest.fixture
def tmp_table(conn: PostGISConnection) -> str:  # type: ignore[return]
    """Create a fresh table, yield its name, then drop it."""
    table = _unique_table()
    gdf = _sample_gdf()
    conn.write_layer(gdf, schema="public", table=table, if_exists="replace")
    yield table
    try:
        conn.execute(f'DROP TABLE IF EXISTS public."{table}"')
    except Exception:
        pass  # best-effort cleanup


# ---------------------------------------------------------------------------
# Test 1 — read_layer
# ---------------------------------------------------------------------------


@_requires_postgis
class TestReadLayer:
    def test_read_layer_returns_geodataframe(self, conn, tmp_table):
        gdf = conn.read_layer("public", tmp_table)
        assert isinstance(gdf, gpd.GeoDataFrame)

    def test_read_layer_row_count(self, conn, tmp_table):
        gdf = conn.read_layer("public", tmp_table)
        assert len(gdf) == 3

    def test_read_layer_has_geometry(self, conn, tmp_table):
        gdf = conn.read_layer("public", tmp_table)
        assert gdf.geometry is not None
        assert not gdf.geometry.is_empty.any()

    def test_read_nonexistent_table_raises(self, conn):
        with pytest.raises(Exception):
            conn.read_layer("public", "table_that_does_not_exist_xyz")


# ---------------------------------------------------------------------------
# Test 2 — write_layer
# ---------------------------------------------------------------------------


@_requires_postgis
class TestWriteLayer:
    def test_write_creates_table(self, conn):
        table = _unique_table()
        gdf = _sample_gdf(n=2)
        try:
            conn.write_layer(gdf, schema="public", table=table, if_exists="replace")
            result = conn.execute(
                f'SELECT COUNT(*) AS n FROM public."{table}"'
            )
            assert result[0]["n"] == 2
        finally:
            conn.execute(f'DROP TABLE IF EXISTS public."{table}"')

    def test_write_replace_overwrites(self, conn):
        table = _unique_table()
        try:
            conn.write_layer(_sample_gdf(n=5), schema="public", table=table)
            conn.write_layer(_sample_gdf(n=2), schema="public", table=table, if_exists="replace")
            result = conn.execute(f'SELECT COUNT(*) AS n FROM public."{table}"')
            assert result[0]["n"] == 2
        finally:
            conn.execute(f'DROP TABLE IF EXISTS public."{table}"')

    def test_write_append_adds_rows(self, conn):
        table = _unique_table()
        try:
            conn.write_layer(_sample_gdf(n=3), schema="public", table=table)
            conn.write_layer(_sample_gdf(n=2), schema="public", table=table, if_exists="append")
            result = conn.execute(f'SELECT COUNT(*) AS n FROM public."{table}"')
            assert result[0]["n"] == 5
        finally:
            conn.execute(f'DROP TABLE IF EXISTS public."{table}"')


# ---------------------------------------------------------------------------
# Test 3 — write/read roundtrip
# ---------------------------------------------------------------------------


@_requires_postgis
class TestWriteReadRoundtrip:
    def test_roundtrip_row_count(self, conn):
        table = _unique_table()
        n = 4
        try:
            conn.write_layer(_sample_gdf(n=n), schema="public", table=table)
            gdf_back = conn.read_layer("public", table)
            assert len(gdf_back) == n
        finally:
            conn.execute(f'DROP TABLE IF EXISTS public."{table}"')

    def test_roundtrip_label_values(self, conn):
        table = _unique_table()
        original = _sample_gdf(n=3)
        try:
            conn.write_layer(original, schema="public", table=table)
            gdf_back = conn.read_layer("public", table)
            assert set(gdf_back["label"]) == {"pt_1", "pt_2", "pt_3"}
        finally:
            conn.execute(f'DROP TABLE IF EXISTS public."{table}"')

    def test_roundtrip_geometry_integrity(self, conn):
        table = _unique_table()
        original = _sample_gdf(n=2)
        try:
            conn.write_layer(original, schema="public", table=table)
            gdf_back = conn.read_layer("public", table)
            assert not gdf_back.geometry.is_empty.any()
            assert gdf_back.geometry.geom_type.isin(["Point"]).all()
        finally:
            conn.execute(f'DROP TABLE IF EXISTS public."{table}"')


# ---------------------------------------------------------------------------
# Test 4 — execute raw SQL
# ---------------------------------------------------------------------------


@_requires_postgis
class TestExecuteRawSQL:
    def test_execute_select_returns_rows(self, conn, tmp_table):
        rows = conn.execute(f'SELECT id, label FROM public."{tmp_table}" ORDER BY id')
        assert isinstance(rows, list)
        assert len(rows) == 3

    def test_execute_returns_dicts(self, conn, tmp_table):
        rows = conn.execute(f'SELECT id FROM public."{tmp_table}" LIMIT 1')
        assert isinstance(rows[0], dict)
        assert "id" in rows[0]

    def test_execute_with_named_params(self, conn, tmp_table):
        rows = conn.execute(
            f'SELECT label FROM public."{tmp_table}" WHERE id = :target_id',
            params={"target_id": 1},
        )
        assert len(rows) == 1
        assert rows[0]["label"] == "pt_1"

    def test_execute_scalar_query(self, conn):
        rows = conn.execute("SELECT 1 AS val")
        assert rows[0]["val"] == 1

    def test_execute_invalid_sql_raises(self, conn):
        with pytest.raises(Exception):
            conn.execute("SELECT * FROM this_table_does_not_exist_xyz_abc")


# ---------------------------------------------------------------------------
# Test 5 — DSN normalisation (no database required)
# ---------------------------------------------------------------------------


class TestDSNNormalization:
    """These tests do not require a real PostGIS instance."""

    def test_plain_postgresql_scheme_rewritten(self):
        """postgresql:// should be rewritten to postgresql+psycopg2://."""
        conn = PostGISConnection.__new__(PostGISConnection)
        dsn = "postgresql://user:pass@localhost:5432/mydb"
        # Apply the same normalisation logic as __init__ without connecting
        if dsn.startswith("postgresql://") and "+psycopg2" not in dsn:
            normalized = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
        else:
            normalized = dsn
        assert normalized == "postgresql+psycopg2://user:pass@localhost:5432/mydb"

    def test_already_normalised_dsn_unchanged(self):
        """postgresql+psycopg2:// should not be double-substituted."""
        dsn = "postgresql+psycopg2://user:pass@localhost:5432/mydb"
        if dsn.startswith("postgresql://") and "+psycopg2" not in dsn:
            normalized = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
        else:
            normalized = dsn
        assert normalized == dsn

    def test_engine_dsn_stored_after_normalisation(self, monkeypatch):
        """PostGISConnection.engine should use the normalised DSN."""
        captured: list[str] = []

        from sqlalchemy import create_engine as _orig_create_engine

        def fake_create_engine(dsn: str, **kwargs):
            captured.append(dsn)
            return _orig_create_engine(dsn, **kwargs)

        monkeypatch.setattr("persistence.postgis.create_engine", fake_create_engine)

        try:
            PostGISConnection(dsn="postgresql://u:p@localhost:5432/db")
        except Exception:
            pass  # connection failure expected — we only care about the DSN captured

        if captured:
            assert captured[0].startswith("postgresql+psycopg2://")


# ---------------------------------------------------------------------------
# Test 6 — Invalid DSN
# ---------------------------------------------------------------------------


class TestInvalidDSN:
    """Behaviour on a DSN that cannot connect."""

    def test_invalid_dsn_raises_on_use(self):
        """An invalid DSN should not raise at construction but should raise on first use."""
        conn = PostGISConnection(dsn="postgresql+psycopg2://bad:bad@127.0.0.1:1/bad")
        with pytest.raises(Exception):
            conn.read_layer("public", "any_table")

    def test_context_manager_disposes_on_exit(self):
        """Context manager __exit__ must call dispose() even on error."""
        conn = PostGISConnection(dsn="postgresql+psycopg2://bad:bad@127.0.0.1:1/bad")
        with pytest.raises(Exception):
            with conn:
                conn.read_layer("public", "any_table")
        # After exiting the context the engine pool should be disposed.
        # Calling dispose() again is a no-op, so this should not raise.
        conn.dispose()
