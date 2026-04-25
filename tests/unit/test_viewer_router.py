"""Tests for the Phase 1.5 viewer API endpoints."""

from __future__ import annotations


import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point, Polygon

from gispulse.adapters.http.serve_app import create_serve_app


@pytest.fixture
def sample_gpkg(tmp_path):
    """Create a GPKG with two layers for testing."""
    # Points layer
    points = gpd.GeoDataFrame(
        {"name": ["A", "B", "C"], "value": [10, 20, 30]},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
        crs="EPSG:4326",
    )
    path = tmp_path / "test.gpkg"
    points.to_file(str(path), layer="points", driver="GPKG")

    # Polygons layer
    polys = gpd.GeoDataFrame(
        {"zone": ["X", "Y"]},
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(2, 2), (3, 2), (3, 3), (2, 3)]),
        ],
        crs="EPSG:4326",
    )
    polys.to_file(str(path), layer="polygons", driver="GPKG")
    return path


@pytest.fixture
def client(sample_gpkg):
    """Create a test client for the serve app."""
    app = create_serve_app(str(sample_gpkg))
    with TestClient(app) as c:
        yield c


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["mode"] == "viewer"


class TestListLayers:
    def test_list_layers(self, client):
        r = client.get("/v1/viewer/layers")
        assert r.status_code == 200
        data = r.json()
        assert len(data["layers"]) == 2
        names = {l["name"] for l in data["layers"]}
        assert "points" in names
        assert "polygons" in names

    def test_layer_metadata(self, client):
        r = client.get("/v1/viewer/layers")
        data = r.json()
        points = next(l for l in data["layers"] if l["name"] == "points")
        assert points["feature_count"] == 3
        assert points["geometry_type"] == "Point"
        assert len(points["bbox"]) == 4
        assert points["crs"] == "EPSG:4326"


class TestLayerDetail:
    def test_get_layer(self, client):
        r = client.get("/v1/viewer/layers/points")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "points"
        assert data["feature_count"] == 3
        field_names = [f["name"] for f in data["fields"]]
        assert "name" in field_names
        assert "value" in field_names

    def test_layer_not_found(self, client):
        r = client.get("/v1/viewer/layers/nonexistent")
        assert r.status_code == 404


class TestFeatures:
    def test_get_all_features(self, client):
        r = client.get("/v1/viewer/layers/points/features")
        assert r.status_code == 200
        geojson = r.json()
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) == 3
        assert geojson["total_count"] == 3

    def test_features_with_limit(self, client):
        r = client.get("/v1/viewer/layers/points/features?limit=2")
        geojson = r.json()
        assert len(geojson["features"]) == 2
        assert geojson["total_count"] == 3

    def test_features_with_offset(self, client):
        r = client.get("/v1/viewer/layers/points/features?limit=2&offset=2")
        geojson = r.json()
        assert len(geojson["features"]) == 1  # only C left

    def test_features_with_bbox(self, client):
        # bbox that only contains Point(0,0) and Point(1,1)
        r = client.get("/v1/viewer/layers/points/features?bbox=-0.5,-0.5,1.5,1.5")
        geojson = r.json()
        assert len(geojson["features"]) == 2

    def test_features_invalid_bbox(self, client):
        r = client.get("/v1/viewer/layers/points/features?bbox=invalid")
        assert r.status_code == 400

    def test_features_with_simplify(self, client):
        r = client.get("/v1/viewer/layers/polygons/features?simplify=0.1")
        assert r.status_code == 200
        geojson = r.json()
        assert geojson["type"] == "FeatureCollection"

    def test_features_geojson_structure(self, client):
        r = client.get("/v1/viewer/layers/points/features")
        geojson = r.json()
        feature = geojson["features"][0]
        assert "type" in feature
        assert feature["type"] == "Feature"
        assert "geometry" in feature
        assert "properties" in feature

    def test_features_not_found(self, client):
        r = client.get("/v1/viewer/layers/nope/features")
        assert r.status_code == 404


class TestBbox:
    def test_get_bbox(self, client):
        r = client.get("/v1/viewer/layers/points/bbox")
        assert r.status_code == 200
        data = r.json()
        assert "bbox" in data
        assert len(data["bbox"]) == 4
        # Points are at (0,0), (1,1), (2,2) so bbox should be [0,0,2,2]
        assert data["bbox"][0] == pytest.approx(0.0)
        assert data["bbox"][2] == pytest.approx(2.0)

    def test_bbox_not_found(self, client):
        r = client.get("/v1/viewer/layers/nope/bbox")
        assert r.status_code == 404
