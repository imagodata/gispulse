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
