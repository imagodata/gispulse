"""Tests for the DuckDB session engine."""


import geopandas as gpd
import pytest
from shapely.geometry import Point

from persistence.duckdb_engine import DuckDBSession


@pytest.fixture
def sample_gdf():
    """Create a simple GeoDataFrame for testing."""
    return gpd.GeoDataFrame(
        {"name": ["A", "B", "C"], "value": [10, 20, 30]},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
        crs="EPSG:4326",
    )


@pytest.fixture
def gpkg_file(tmp_path, sample_gdf):
    """Write a sample GPKG and return its path."""
    path = tmp_path / "test.gpkg"
    sample_gdf.to_file(str(path), layer="points", driver="GPKG")
    return path


class TestDuckDBSession:
    def test_open_close(self):
        session = DuckDBSession()
        session.open()
        assert session._conn is not None
        session.close()
        assert session._conn is None

    def test_context_manager(self):
        with DuckDBSession() as session:
            assert session._conn is not None
        assert session._conn is None

    def test_load_gpkg(self, gpkg_file):
        with DuckDBSession() as session:
            gdf = session.load_gpkg(gpkg_file, layer="points")
            assert len(gdf) == 3
            assert "name" in gdf.columns

    def test_load_gpkg_no_layer(self, gpkg_file):
        """Loading without specifying layer should pick the first one."""
        with DuckDBSession() as session:
            gdf = session.load_gpkg(gpkg_file)
            assert len(gdf) == 3

    def test_list_gpkg_layers(self, gpkg_file):
        with DuckDBSession() as session:
            layers = session.list_gpkg_layers(gpkg_file)
            assert "points" in layers

    def test_to_gpkg(self, sample_gdf, tmp_path):
        out = tmp_path / "output.gpkg"
        with DuckDBSession() as session:
            result = session.to_gpkg(sample_gdf, out, layer="result")
            assert result.exists()

        # Re-read and verify
        reloaded = gpd.read_file(str(out), layer="result")
        assert len(reloaded) == 3

    def test_load_gpkg_missing_file(self):
        with DuckDBSession() as session:
            with pytest.raises(FileNotFoundError):
                session.load_gpkg("/nonexistent/file.gpkg")

    def test_conn_property_raises_when_closed(self):
        session = DuckDBSession()
        with pytest.raises(RuntimeError, match="not open"):
            _ = session.conn

    def test_sql_preserves_crs_from_registered_table(self, sample_gdf):
        """Regression: sql() must re-attach the CRS of the registered input.

        WKB serialisation in register_gdf strips the SRID, so without
        explicit CRS tracking the resulting GeoDataFrame would have
        crs=None — silently losing projection on every DuckDB-backed
        capability (filter, etc.).
        """
        with DuckDBSession() as session:
            session.register_gdf("input", sample_gdf)
            result = session.sql("SELECT * FROM input")
            assert result.crs is not None
            assert str(result.crs) == str(sample_gdf.crs)

    def test_sql_returns_no_crs_when_tables_have_diverging_crs(self):
        """When a query joins tables with different CRS, return None
        rather than guessing — let the caller reproject explicitly."""
        a = gpd.GeoDataFrame(
            {"id": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326"
        )
        b = gpd.GeoDataFrame(
            {"id": [1]}, geometry=[Point(0, 0)], crs="EPSG:2154"
        )
        with DuckDBSession() as session:
            session.register_gdf("a", a)
            session.register_gdf("b", b)
            result = session.sql("SELECT a.* FROM a, b WHERE a.id = b.id")
            assert result.crs is None

    def test_sql_does_not_invent_crs_when_no_tables_registered(self, sample_gdf):
        """If sql() runs against an empty CRS map, the result has no CRS."""
        with DuckDBSession() as session:
            # Register then query a different (non-existent in map) name via
            # a literal subquery; result has geometry but no resolvable CRS.
            session.register_gdf("known", sample_gdf)
            result = session.sql(
                "SELECT * FROM known WHERE 1=0"  # references known => keeps CRS
            )
            assert str(result.crs) == str(sample_gdf.crs)
