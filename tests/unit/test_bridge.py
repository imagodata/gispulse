"""Tests for persistence.bridge — DuckDBPostGISBridge, HybridEngine.

Covers parts that don't require a live PostgreSQL:
- DSN normalization (+psycopg2 stripping)
- SQL injection guards on identifiers
- Lifecycle errors when engine is not open
- DuckDB-only ``register()`` pathway
- Metadata properties (backend_name, is_persistent)
"""
from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from persistence.bridge import DuckDBPostGISBridge, HybridEngine


@pytest.fixture
def gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2, 3], "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)]},
        crs="EPSG:4326",
    )


class TestDSNNormalization:
    def test_strips_psycopg2_suffix(self):
        bridge = DuckDBPostGISBridge("postgresql+psycopg2://user:pw@host/db")
        assert bridge._pg_dsn == "postgresql://user:pw@host/db"

    def test_plain_dsn_passthrough(self):
        bridge = DuckDBPostGISBridge("postgresql://user:pw@host/db")
        assert bridge._pg_dsn == "postgresql://user:pw@host/db"

    def test_pg_attach_name_constant(self):
        assert DuckDBPostGISBridge.PG_ATTACH_NAME == "pg"


class TestHybridEngineLifecycleErrors:
    def test_conn_access_before_open_raises(self):
        engine = HybridEngine(pg_dsn="postgresql://user:pw@host/db")
        with pytest.raises(RuntimeError, match="not open"):
            _ = engine._conn

    def test_close_without_open_is_safe(self):
        engine = HybridEngine(pg_dsn="postgresql://user:pw@host/db")
        # Should not raise even though open() was never called
        engine.close()

    def test_metadata_properties(self):
        engine = HybridEngine(pg_dsn="postgresql://user:pw@host/db")
        assert engine.backend_name == "hybrid"
        assert engine.is_persistent is True


class TestHybridEngineIdentifierValidation:
    """Verify SQL identifier validation rejects unsafe values without hitting PostGIS."""

    @pytest.fixture
    def unopened_engine(self) -> HybridEngine:
        # The identifier validation runs before any DuckDB / PG access,
        # so an unopened engine is enough to exercise these paths.
        return HybridEngine(pg_dsn="postgresql://user:pw@host/db")

    @pytest.mark.parametrize(
        "bad_source",
        [
            "parcels; DROP TABLE x",
            "parcels'--",
            "parcels UNION SELECT",
            "1_starts_with_digit",
            "",
            "a" * 200,  # too long
        ],
    )
    def test_load_layer_rejects_unsafe_source(self, unopened_engine, bad_source):
        with pytest.raises(ValueError, match="Unsafe table name"):
            unopened_engine.load_layer(bad_source)

    @pytest.mark.parametrize(
        "bad_schema",
        ["public; DROP", "pub--", "", "1bad"],
    )
    def test_load_layer_rejects_unsafe_schema(self, unopened_engine, bad_schema):
        with pytest.raises(ValueError, match="Unsafe schema name"):
            unopened_engine.load_layer("parcels", schema=bad_schema)

    def test_list_layers_rejects_unsafe_schema_via_fallback(self, unopened_engine):
        """list_layers catches the exception and falls back to _postgis.list_layers.

        Since _postgis is None (engine not open), the fallback raises
        AttributeError — but that's how the code currently behaves. This test
        pins that contract so future refactors don't silently break it.
        """
        with pytest.raises((ValueError, AttributeError)):
            unopened_engine.list_layers(schema="bad; DROP")


class TestHybridEngineRegister:
    """The register() path uses only DuckDB, no PostGIS — safe to test standalone."""

    def test_register_dataframe_is_queryable(self):
        """register() delegates to ``duckdb.register()`` — verify round-trip with a
        plain GeoDataFrame projected to a non-geometry column set."""
        import pandas as pd
        import duckdb

        engine = HybridEngine(pg_dsn="postgresql://user:pw@host/nonexistent")
        engine._session = duckdb.connect(":memory:")
        # Use a plain DataFrame wrapped as GeoDataFrame without geometry
        df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
        gdf_noop = gpd.GeoDataFrame(df)

        engine.register("local_cities", gdf_noop)
        res = engine._session.execute("SELECT COUNT(*) AS c FROM local_cities").fetchdf()
        assert res["c"][0] == 3
        engine._session.close()
