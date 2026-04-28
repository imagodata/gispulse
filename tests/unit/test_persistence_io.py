"""
Unit tests for persistence.io — format detection, read/write vector, dataset_from_file.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from persistence.io import (
    dataset_from_file,
    detect_format,
    read_vector,
    supported_extensions,
    write_vector,
)


class TestFormatDetection:
    def test_detect_gpkg(self) -> None:
        assert detect_format("data/parcels.gpkg") == "GPKG"

    def test_detect_geojson(self) -> None:
        assert detect_format("data/points.geojson") == "GeoJSON"

    def test_detect_json_as_geojson(self) -> None:
        assert detect_format("data/features.json") == "GeoJSON"

    def test_detect_shapefile(self) -> None:
        assert detect_format("data/roads.shp") == "ESRI Shapefile"

    def test_detect_flatgeobuf(self) -> None:
        assert detect_format("data/zones.fgb") == "FlatGeobuf"

    def test_detect_parquet(self) -> None:
        assert detect_format("data/layers.parquet") == "Parquet"

    def test_detect_unknown_returns_none(self) -> None:
        result = detect_format("data/file.xyz")
        assert result is None

    def test_supported_extensions_includes_common(self) -> None:
        exts = supported_extensions()
        assert ".gpkg" in exts
        assert ".geojson" in exts
        assert ".shp" in exts


class TestReadWriteVector:
    @pytest.fixture()
    def sample_gdf(self) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {"name": ["A", "B", "C"], "value": [1, 2, 3]},
            geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
            crs="EPSG:4326",
        )

    def test_write_and_read_gpkg(self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path) -> None:
        path = str(tmp_path / "test.gpkg")
        write_vector(sample_gdf, path)
        result = read_vector(path)
        assert len(result) == 3
        assert "name" in result.columns
        assert result.crs is not None

    def test_write_and_read_geojson(self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path) -> None:
        path = str(tmp_path / "test.geojson")
        write_vector(sample_gdf, path)
        result = read_vector(path)
        assert len(result) == 3

    def test_write_and_read_fgb(self, sample_gdf: gpd.GeoDataFrame, tmp_path: Path) -> None:
        path = str(tmp_path / "test.fgb")
        write_vector(sample_gdf, path)
        result = read_vector(path)
        assert len(result) == 3

    def test_read_nonexistent_raises(self) -> None:
        with pytest.raises(Exception):
            read_vector("/nonexistent/file.gpkg")


class TestDatasetFromFile:
    @pytest.fixture()
    def gpkg_file(self, tmp_path: Path) -> str:
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        )
        path = str(tmp_path / "sample.gpkg")
        gdf.to_file(path, driver="GPKG")
        return path

    def test_creates_dataset_from_gpkg(self, gpkg_file: str) -> None:
        ds = dataset_from_file(gpkg_file)
        assert ds.name is not None
        assert ds.source_path == gpkg_file
        assert ds.crs is not None
        assert ds.format is not None

    def test_nonexistent_file_raises(self) -> None:
        with pytest.raises(Exception):
            dataset_from_file("/nonexistent/file.gpkg")
