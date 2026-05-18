"""Unit tests for vertex/segment capabilities added in the R1 QGIS port."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon

from gispulse.capabilities.vector import (
    DensifyVerticesCapability,
    ExtractSegmentsCapability,
    ExtractVerticesCapability,
)


@pytest.fixture
def square() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1],
            "name": ["S1"],
            "geometry": [Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])],
        },
        crs="EPSG:2154",
    )


@pytest.fixture
def line() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "id": [1],
            "geometry": [LineString([(0, 0), (100, 0), (100, 100)])],
        },
        crs="EPSG:2154",
    )


class TestExtractVertices:

    def test_extracts_all_polygon_vertices(self, square):
        result = ExtractVerticesCapability().execute(square)
        # Polygon exterior has 5 coords (closed ring)
        assert len(result) == 5
        assert "vertex_index" in result.columns
        assert "global_index" in result.columns
        assert all(g.geom_type == "Point" for g in result.geometry)

    def test_extracts_all_line_vertices(self, line):
        result = ExtractVerticesCapability().execute(line)
        assert len(result) == 3

    def test_keep_attrs_copies_columns(self, square):
        result = ExtractVerticesCapability().execute(square, keep_attrs=True)
        assert "name" in result.columns
        assert all(result["name"] == "S1")

    def test_empty_gdf(self):
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:2154")
        result = ExtractVerticesCapability().execute(empty)
        assert len(result) == 0


class TestExtractSegments:

    def test_splits_line_into_segments(self, line):
        result = ExtractSegmentsCapability().execute(line, crs_meters="EPSG:2154")
        # 3-vertex line → 2 segments
        assert len(result) == 2
        # First segment is 100 m
        assert pytest.approx(result["length"].iloc[0]) == 100.0

    def test_splits_polygon_boundary(self, square):
        result = ExtractSegmentsCapability().execute(square, crs_meters="EPSG:2154")
        # Square: 5 coords (closed) → 4 segments
        assert len(result) == 4
        # Each side 10 m
        assert all(abs(l - 10.0) < 1e-6 for l in result["length"])


class TestDensifyVertices:

    def test_max_distance_inserts_vertices(self, line):
        result = DensifyVerticesCapability().execute(
            line, max_distance=10.0, crs_meters="EPSG:2154"
        )
        new_line = result.geometry.iloc[0]
        coords = list(new_line.coords)
        # Original was 3 coords, max_distance=10 on 200 m total → lots more
        assert len(coords) > 20

    def test_n_vertices_per_segment(self, line):
        result = DensifyVerticesCapability().execute(
            line, n_vertices_per_segment=4, crs_meters="EPSG:2154"
        )
        coords = list(result.geometry.iloc[0].coords)
        # Original had 3 coords; N=4 per segment → 2 segments split
        assert len(coords) >= 5

    def test_requires_exactly_one_mode(self, line):
        with pytest.raises(ValueError, match="max_distance"):
            DensifyVerticesCapability().execute(line)  # neither mode set

        with pytest.raises(ValueError, match="max_distance"):
            DensifyVerticesCapability().execute(
                line, max_distance=5.0, n_vertices_per_segment=3
            )

    def test_invalid_max_distance(self, line):
        with pytest.raises(ValueError, match="max_distance"):
            DensifyVerticesCapability().execute(line, max_distance=0)

    def test_invalid_n_vertices(self, line):
        with pytest.raises(ValueError, match="n_vertices_per_segment"):
            DensifyVerticesCapability().execute(line, n_vertices_per_segment=0)
