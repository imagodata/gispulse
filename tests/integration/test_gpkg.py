"""Integration tests for GeoPackage persistence helpers."""

from __future__ import annotations

import os

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

# fiona est une dépendance optionnelle — skip le module entier si absent
pytest.importorskip("fiona", reason="fiona not installed")

from persistence.gpkg import (  # noqa: E402
    dataset_from_gpkg,
    list_layers,
    read_gpkg,
    write_gpkg,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_gpkg(tmp_path) -> str:
    """Create a temporary GPKG file with a single point layer."""
    path = str(tmp_path / "test.gpkg")
    gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "name": ["a", "b", "c"],
            "geometry": [
                Point(2.35, 48.85),
                Point(2.30, 48.87),
                Point(2.40, 48.90),
            ],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(path, layer="points", driver="GPKG")
    return path


@pytest.fixture
def tmp_gpkg_multi(tmp_path) -> str:
    """Create a temporary GPKG with two layers (points and polygons)."""
    path = str(tmp_path / "multi.gpkg")
    points_gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "geometry": [Point(2.35, 48.85), Point(2.30, 48.87)],
        },
        crs="EPSG:4326",
    )
    polygons_gdf = gpd.GeoDataFrame(
        {
            "id": [10, 20],
            "category": ["A", "B"],
            "geometry": [
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
            ],
        },
        crs="EPSG:4326",
    )
    points_gdf.to_file(path, layer="points", driver="GPKG")
    polygons_gdf.to_file(path, layer="polygons", driver="GPKG", mode="a")
    return path


# ---------------------------------------------------------------------------
# list_layers
# ---------------------------------------------------------------------------


class TestListLayers:
    def test_single_layer(self, tmp_gpkg):
        layers = list_layers(tmp_gpkg)
        assert layers == ["points"]

    def test_multi_layer(self, tmp_gpkg_multi):
        layers = list_layers(tmp_gpkg_multi)
        assert set(layers) == {"points", "polygons"}

    def test_nonexistent_raises(self, tmp_path):
        with pytest.raises(Exception):
            list_layers(str(tmp_path / "ghost.gpkg"))


# ---------------------------------------------------------------------------
# read_gpkg
# ---------------------------------------------------------------------------


class TestReadGpkg:
    def test_read_named_layer(self, tmp_gpkg):
        gdf = read_gpkg(tmp_gpkg, layer="points")
        assert isinstance(gdf, gpd.GeoDataFrame)
        assert len(gdf) == 3
        assert gdf.crs.to_epsg() == 4326

    def test_read_default_layer(self, tmp_gpkg):
        gdf = read_gpkg(tmp_gpkg)
        assert len(gdf) == 3

    def test_read_polygon_layer(self, tmp_gpkg_multi):
        gdf = read_gpkg(tmp_gpkg_multi, layer="polygons")
        assert len(gdf) == 2
        assert gdf.geometry.iloc[0].geom_type == "Polygon"


# ---------------------------------------------------------------------------
# write_gpkg
# ---------------------------------------------------------------------------


class TestWriteGpkg:
    def test_write_creates_file(self, tmp_path):
        path = str(tmp_path / "output.gpkg")
        gdf = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(2.35, 48.85)]},
            crs="EPSG:4326",
        )
        write_gpkg(gdf, path, layer="result")
        assert os.path.exists(path)

    def test_write_read_roundtrip(self, tmp_path):
        path = str(tmp_path / "roundtrip.gpkg")
        gdf = gpd.GeoDataFrame(
            {
                "id": [1, 2],
                "label": ["x", "y"],
                "geometry": [Point(1.0, 2.0), Point(3.0, 4.0)],
            },
            crs="EPSG:4326",
        )
        write_gpkg(gdf, path, layer="test_layer")
        loaded = read_gpkg(path, layer="test_layer")
        assert len(loaded) == 2
        assert list(loaded["label"]) == ["x", "y"]

    def test_write_append_mode(self, tmp_path):
        path = str(tmp_path / "append.gpkg")
        gdf1 = gpd.GeoDataFrame(
            {"id": [1], "geometry": [Point(0.0, 0.0)]}, crs="EPSG:4326"
        )
        gdf2 = gpd.GeoDataFrame(
            {"id": [2], "geometry": [Point(1.0, 1.0)]}, crs="EPSG:4326"
        )
        write_gpkg(gdf1, path, layer="layer1")
        write_gpkg(gdf2, path, layer="layer2", mode="a")
        layers = list_layers(path)
        assert set(layers) == {"layer1", "layer2"}


# ---------------------------------------------------------------------------
# dataset_from_gpkg
# ---------------------------------------------------------------------------


class TestDatasetFromGpkg:
    def test_creates_dataset(self, tmp_gpkg):
        ds = dataset_from_gpkg(tmp_gpkg)
        assert ds.name == "test"
        assert ds.source_path == tmp_gpkg
        assert ds.format == "GPKG"
        assert ds.data_category == "vector"

    def test_dataset_has_layer_metadata(self, tmp_gpkg):
        ds = dataset_from_gpkg(tmp_gpkg)
        assert "layers" in ds.metadata
        assert ds.metadata["layer_count"] == 1
        layer_meta = ds.metadata["layers"][0]
        assert layer_meta["name"] == "points"
        assert layer_meta["feature_count"] == 3

    def test_dataset_multi_layers(self, tmp_gpkg_multi):
        ds = dataset_from_gpkg(tmp_gpkg_multi)
        assert ds.metadata["layer_count"] == 2
        layer_names = {lm["name"] for lm in ds.metadata["layers"]}
        assert layer_names == {"points", "polygons"}
