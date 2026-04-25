"""Integration tests for multi-format vector I/O (persistence.io)."""

from __future__ import annotations

import os

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon, LineString

pytest.importorskip("fiona", reason="fiona not installed")

from persistence.io import (  # noqa: E402
    dataset_from_file,
    detect_format,
    list_layers,
    read_vector,
    supported_extensions,
    write_vector,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_points_gdf():
    """Simple points GeoDataFrame for write/read tests."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "name": ["Paris", "Lyon", "Marseille"],
            "geometry": [
                Point(2.35, 48.85),
                Point(4.83, 45.76),
                Point(5.37, 43.30),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def sample_polygons_gdf():
    """Simple polygons GeoDataFrame."""
    return gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "zone": ["A", "B"],
            "geometry": [
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
            ],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def sample_lines_gdf():
    """Simple lines GeoDataFrame."""
    return gpd.GeoDataFrame(
        {
            "id": [1],
            "geometry": [LineString([(0, 0), (1, 1), (2, 0)])],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def tmp_gpkg(tmp_path, sample_points_gdf):
    """Temporary GPKG file."""
    path = str(tmp_path / "test.gpkg")
    sample_points_gdf.to_file(path, layer="cities", driver="GPKG")
    return path


@pytest.fixture
def tmp_geojson(tmp_path, sample_points_gdf):
    """Temporary GeoJSON file."""
    path = str(tmp_path / "test.geojson")
    sample_points_gdf.to_file(path, driver="GeoJSON")
    return path


@pytest.fixture
def tmp_shapefile(tmp_path, sample_points_gdf):
    """Temporary Shapefile."""
    path = str(tmp_path / "test.shp")
    sample_points_gdf.to_file(path, driver="ESRI Shapefile")
    return path


@pytest.fixture
def tmp_flatgeobuf(tmp_path, sample_points_gdf):
    """Temporary FlatGeobuf file."""
    path = str(tmp_path / "test.fgb")
    sample_points_gdf.to_file(path, driver="FlatGeobuf")
    return path


@pytest.fixture
def tmp_csv_latlon(tmp_path):
    """Temporary CSV with lat/lon columns."""
    path = str(tmp_path / "points.csv")
    with open(path, "w") as f:
        f.write("id,name,latitude,longitude\n")
        f.write("1,Paris,48.85,2.35\n")
        f.write("2,Lyon,45.76,4.83\n")
        f.write("3,Marseille,43.30,5.37\n")
    return path


@pytest.fixture
def tmp_csv_wkt(tmp_path):
    """Temporary CSV with WKT geometry column."""
    path = str(tmp_path / "wkt.csv")
    with open(path, "w") as f:
        f.write("id,name,geometry\n")
        f.write('1,Paris,"POINT (2.35 48.85)"\n')
        f.write('2,Lyon,"POINT (4.83 45.76)"\n')
    return path


# ---------------------------------------------------------------------------
# detect_format
# ---------------------------------------------------------------------------


class TestDetectFormat:
    def test_gpkg(self):
        assert detect_format("data/file.gpkg") == "GPKG"

    def test_geojson(self):
        assert detect_format("data/file.geojson") == "GeoJSON"

    def test_shapefile(self):
        assert detect_format("data/file.shp") == "ESRI Shapefile"

    def test_flatgeobuf(self):
        assert detect_format("output.fgb") == "FlatGeobuf"

    def test_csv(self):
        assert detect_format("data.csv") == "CSV"

    def test_parquet(self):
        assert detect_format("big.parquet") == "Parquet"

    def test_unknown_returns_none(self):
        assert detect_format("file.xyz") is None

    def test_case_insensitive(self):
        assert detect_format("FILE.GPKG") == "GPKG"


class TestSupportedExtensions:
    def test_returns_list(self):
        exts = supported_extensions()
        assert isinstance(exts, list)
        assert ".gpkg" in exts
        assert ".geojson" in exts
        assert ".shp" in exts
        assert ".fgb" in exts
        assert ".csv" in exts


# ---------------------------------------------------------------------------
# read_vector — GPKG
# ---------------------------------------------------------------------------


class TestReadVectorGpkg:
    def test_read_gpkg(self, tmp_gpkg):
        gdf = read_vector(tmp_gpkg, layer="cities")
        assert isinstance(gdf, gpd.GeoDataFrame)
        assert len(gdf) == 3
        assert gdf.crs.to_epsg() == 4326

    def test_read_gpkg_default_layer(self, tmp_gpkg):
        gdf = read_vector(tmp_gpkg)
        assert len(gdf) == 3


# ---------------------------------------------------------------------------
# read_vector — GeoJSON
# ---------------------------------------------------------------------------


class TestReadVectorGeoJSON:
    def test_read_geojson(self, tmp_geojson):
        gdf = read_vector(tmp_geojson)
        assert len(gdf) == 3
        assert gdf.crs.to_epsg() == 4326

    def test_geojson_has_columns(self, tmp_geojson):
        gdf = read_vector(tmp_geojson)
        assert "name" in gdf.columns


# ---------------------------------------------------------------------------
# read_vector — Shapefile
# ---------------------------------------------------------------------------


class TestReadVectorShapefile:
    def test_read_shapefile(self, tmp_shapefile):
        gdf = read_vector(tmp_shapefile)
        assert len(gdf) == 3
        assert gdf.crs.to_epsg() == 4326


# ---------------------------------------------------------------------------
# read_vector — FlatGeobuf
# ---------------------------------------------------------------------------


class TestReadVectorFlatGeobuf:
    def test_read_fgb(self, tmp_flatgeobuf):
        gdf = read_vector(tmp_flatgeobuf)
        assert len(gdf) == 3


# ---------------------------------------------------------------------------
# read_vector — CSV
# ---------------------------------------------------------------------------


class TestReadVectorCSV:
    def test_read_csv_latlon(self, tmp_csv_latlon):
        gdf = read_vector(tmp_csv_latlon)
        assert isinstance(gdf, gpd.GeoDataFrame)
        assert len(gdf) == 3
        assert gdf.geometry.iloc[0].geom_type == "Point"

    def test_csv_crs_default(self, tmp_csv_latlon):
        gdf = read_vector(tmp_csv_latlon)
        assert gdf.crs.to_epsg() == 4326

    def test_read_csv_wkt(self, tmp_csv_wkt):
        gdf = read_vector(tmp_csv_wkt)
        assert len(gdf) == 2
        assert gdf.geometry.iloc[0].geom_type == "Point"

    def test_csv_rows_limit(self, tmp_csv_latlon):
        gdf = read_vector(tmp_csv_latlon, rows=2)
        assert len(gdf) == 2

    def test_csv_no_geo_columns_raises(self, tmp_path):
        path = str(tmp_path / "nogeo.csv")
        with open(path, "w") as f:
            f.write("id,value\n1,100\n2,200\n")
        with pytest.raises(ValueError, match="Cannot detect"):
            read_vector(path)


# ---------------------------------------------------------------------------
# read_vector — errors
# ---------------------------------------------------------------------------


class TestReadVectorErrors:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            read_vector("/nonexistent/file.geojson")

    def test_unsupported_format(self, tmp_path):
        path = str(tmp_path / "file.xyz")
        with open(path, "w") as f:
            f.write("dummy")
        with pytest.raises(ValueError, match="Unsupported format"):
            read_vector(path)


# ---------------------------------------------------------------------------
# read_vector — bbox and rows
# ---------------------------------------------------------------------------


class TestReadVectorFilters:
    def test_bbox_filter(self, tmp_geojson):
        # BBox around Paris only (2.3-2.4, 48.8-48.9)
        gdf = read_vector(tmp_geojson, bbox=(2.3, 48.8, 2.4, 48.9))
        assert len(gdf) >= 1
        assert len(gdf) < 3

    def test_rows_limit(self, tmp_geojson):
        gdf = read_vector(tmp_geojson, rows=1)
        assert len(gdf) == 1


# ---------------------------------------------------------------------------
# write_vector
# ---------------------------------------------------------------------------


class TestWriteVector:
    def test_write_geojson(self, tmp_path, sample_points_gdf):
        path = str(tmp_path / "out.geojson")
        write_vector(sample_points_gdf, path)
        assert os.path.exists(path)
        loaded = read_vector(path)
        assert len(loaded) == 3

    def test_write_gpkg(self, tmp_path, sample_points_gdf):
        path = str(tmp_path / "out.gpkg")
        write_vector(sample_points_gdf, path, layer="test")
        loaded = read_vector(path, layer="test")
        assert len(loaded) == 3

    def test_write_shapefile(self, tmp_path, sample_points_gdf):
        path = str(tmp_path / "out.shp")
        write_vector(sample_points_gdf, path)
        assert os.path.exists(path)
        loaded = read_vector(path)
        assert len(loaded) == 3

    def test_write_flatgeobuf(self, tmp_path, sample_points_gdf):
        path = str(tmp_path / "out.fgb")
        write_vector(sample_points_gdf, path)
        loaded = read_vector(path)
        assert len(loaded) == 3

    def test_write_creates_parent_dirs(self, tmp_path, sample_points_gdf):
        path = str(tmp_path / "sub" / "dir" / "out.geojson")
        write_vector(sample_points_gdf, path)
        assert os.path.exists(path)

    def test_write_unsupported_raises(self, tmp_path, sample_points_gdf):
        path = str(tmp_path / "out.dxf")
        with pytest.raises(ValueError, match="Cannot write"):
            write_vector(sample_points_gdf, path)


# ---------------------------------------------------------------------------
# write/read roundtrip across formats
# ---------------------------------------------------------------------------


class TestRoundtrip:
    @pytest.mark.parametrize("ext", [".gpkg", ".geojson", ".shp", ".fgb"])
    def test_roundtrip_points(self, tmp_path, sample_points_gdf, ext):
        path = str(tmp_path / f"roundtrip{ext}")
        layer = "data" if ext == ".gpkg" else None
        write_vector(sample_points_gdf, path, layer=layer)
        loaded = read_vector(path, layer=layer)
        assert len(loaded) == len(sample_points_gdf)
        assert loaded.crs.to_epsg() == 4326

    @pytest.mark.parametrize("ext", [".gpkg", ".geojson", ".fgb"])
    def test_roundtrip_polygons(self, tmp_path, sample_polygons_gdf, ext):
        path = str(tmp_path / f"roundtrip_poly{ext}")
        layer = "zones" if ext == ".gpkg" else None
        write_vector(sample_polygons_gdf, path, layer=layer)
        loaded = read_vector(path, layer=layer)
        assert len(loaded) == 2


# ---------------------------------------------------------------------------
# list_layers (io module)
# ---------------------------------------------------------------------------


class TestListLayersIO:
    def test_gpkg_layers(self, tmp_gpkg):
        layers = list_layers(tmp_gpkg)
        assert "cities" in layers

    def test_single_layer_format(self, tmp_geojson):
        layers = list_layers(tmp_geojson)
        assert layers == [""]


# ---------------------------------------------------------------------------
# dataset_from_file
# ---------------------------------------------------------------------------


class TestDatasetFromFile:
    def test_gpkg(self, tmp_gpkg):
        ds = dataset_from_file(tmp_gpkg)
        assert ds.name == "test"
        assert ds.format == "GPKG"
        assert ds.data_category == "vector"
        assert ds.metadata["layer_count"] == 1
        assert ds.metadata["layers"][0]["feature_count"] == 3

    def test_geojson(self, tmp_geojson):
        ds = dataset_from_file(tmp_geojson)
        assert ds.format == "GeoJSON"
        assert ds.data_category == "vector"
        assert ds.metadata["layer_count"] == 1

    def test_shapefile(self, tmp_shapefile):
        ds = dataset_from_file(tmp_shapefile)
        assert ds.format == "ESRI Shapefile"
        assert ds.metadata["layers"][0]["feature_count"] == 3

    def test_flatgeobuf(self, tmp_flatgeobuf):
        ds = dataset_from_file(tmp_flatgeobuf)
        assert ds.format == "FlatGeobuf"

    def test_csv(self, tmp_csv_latlon):
        ds = dataset_from_file(tmp_csv_latlon)
        assert ds.data_category == "tabular_geo"
        assert ds.format == "CSV"

    def test_unsupported_raises(self, tmp_path):
        path = str(tmp_path / "file.xyz")
        with open(path, "w") as f:
            f.write("dummy")
        with pytest.raises(ValueError):
            dataset_from_file(path)


# ---------------------------------------------------------------------------
# GeoParquet (if pyarrow available)
# ---------------------------------------------------------------------------


class TestGeoParquet:
    @pytest.fixture
    def tmp_parquet(self, tmp_path, sample_points_gdf):
        pytest.importorskip("pyarrow", reason="pyarrow not installed")
        path = str(tmp_path / "test.parquet")
        sample_points_gdf.to_parquet(path)
        return path

    def test_read_parquet(self, tmp_parquet):
        gdf = read_vector(tmp_parquet)
        assert len(gdf) == 3

    def test_write_parquet(self, tmp_path, sample_points_gdf):
        pytest.importorskip("pyarrow", reason="pyarrow not installed")
        path = str(tmp_path / "out.parquet")
        write_vector(sample_points_gdf, path)
        loaded = read_vector(path)
        assert len(loaded) == 3

    def test_dataset_from_parquet(self, tmp_parquet):
        ds = dataset_from_file(tmp_parquet)
        assert ds.format == "Parquet"
        assert ds.metadata["layer_count"] == 1
