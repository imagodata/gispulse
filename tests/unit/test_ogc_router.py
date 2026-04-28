"""
Tests for OGC API Features router (adapters/http/routers/ogc_features_router.py).

Uses FastAPI TestClient (httpx) against a fresh app instance.
"""

from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from core.models import Dataset


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _memory_storage(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


@pytest.fixture()
def client_with_dataset() -> tuple[TestClient, Dataset]:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    ds = Dataset(name="parcels", source_path="/data/parcels.gpkg", crs="EPSG:4326")
    app.state.dataset_repo.save(ds)
    return TestClient(app), ds


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------


class TestOGCLandingPage:
    def test_landing_page_returns_200(self, client):
        resp = client.get("/ogc/")
        assert resp.status_code == 200

    def test_landing_page_has_links(self, client):
        body = client.get("/ogc/").json()
        assert "links" in body
        assert any(link["rel"] == "self" for link in body["links"])

    def test_landing_page_title(self, client):
        body = client.get("/ogc/").json()
        assert "title" in body
        assert "GISPulse" in body["title"]


# ---------------------------------------------------------------------------
# Conformance
# ---------------------------------------------------------------------------


class TestOGCConformance:
    def test_conformance_returns_200(self, client):
        resp = client.get("/ogc/conformance")
        assert resp.status_code == 200

    def test_conformance_has_classes(self, client):
        body = client.get("/ogc/conformance").json()
        assert "conformsTo" in body
        assert isinstance(body["conformsTo"], list)
        assert len(body["conformsTo"]) > 0

    def test_conformance_includes_core(self, client):
        body = client.get("/ogc/conformance").json()
        assert any("core" in url for url in body["conformsTo"])


# ---------------------------------------------------------------------------
# Collections list
# ---------------------------------------------------------------------------


class TestOGCCollections:
    def test_empty_collections_returns_200(self, client):
        resp = client.get("/ogc/collections")
        assert resp.status_code == 200

    def test_empty_collections_body(self, client):
        body = client.get("/ogc/collections").json()
        assert "collections" in body
        assert body["collections"] == []

    def test_collections_with_one_dataset(self, client_with_dataset):
        client, ds = client_with_dataset
        body = client.get("/ogc/collections").json()
        assert len(body["collections"]) == 1
        assert body["collections"][0]["id"] == str(ds.id)

    def test_collection_links_present(self, client_with_dataset):
        client, ds = client_with_dataset
        body = client.get("/ogc/collections").json()
        coll = body["collections"][0]
        assert "links" in coll
        assert any(link["rel"] == "self" for link in coll["links"])
        assert any(link["rel"] == "items" for link in coll["links"])


# ---------------------------------------------------------------------------
# Single collection
# ---------------------------------------------------------------------------


class TestOGCSingleCollection:
    def test_existing_collection_returns_200(self, client_with_dataset):
        client, ds = client_with_dataset
        resp = client.get(f"/ogc/collections/{ds.id}")
        assert resp.status_code == 200

    def test_collection_id_in_response(self, client_with_dataset):
        client, ds = client_with_dataset
        body = client.get(f"/ogc/collections/{ds.id}").json()
        assert body["id"] == str(ds.id)
        assert body["title"] == ds.name

    def test_nonexistent_collection_returns_404(self, client):
        missing_id = uuid4()
        resp = client.get(f"/ogc/collections/{missing_id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Collection items (features)
# ---------------------------------------------------------------------------


class TestOGCCollectionItems:
    def test_missing_source_path_returns_error(self, client_with_dataset):
        """Dataset with non-existent source_path raises 404."""
        client, ds = client_with_dataset
        # The source_path does not exist on disk — expect a 404 error
        resp = client.get(f"/ogc/collections/{ds.id}/items")
        assert resp.status_code in (404, 500)

    def test_nonexistent_collection_items_404(self, client):
        missing_id = uuid4()
        resp = client.get(f"/ogc/collections/{missing_id}/items")
        assert resp.status_code == 404

    def test_invalid_bbox_returns_400(self, client_with_dataset):
        client, ds = client_with_dataset
        resp = client.get(f"/ogc/collections/{ds.id}/items?bbox=bad_bbox")
        assert resp.status_code == 400

    def test_limit_param_accepted(self, client_with_dataset):
        """Verifies limit param is parsed (even if file load fails)."""
        client, ds = client_with_dataset
        resp = client.get(f"/ogc/collections/{ds.id}/items?limit=10")
        # Error expected because file doesn't exist, but not a 400 (bad param)
        assert resp.status_code != 400

    def test_items_with_mocked_loader(self, client_with_dataset):
        """Test items endpoint with a mocked read_vector."""
        import geopandas as gpd
        from shapely.geometry import Point

        client, ds = client_with_dataset
        gdf = gpd.GeoDataFrame(
            {"name": ["A", "B"]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        )

        with patch("gispulse.adapters.http.routers.ogc_features_router.read_vector", return_value=gdf):
            resp = client.get(f"/ogc/collections/{ds.id}/items")

        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "FeatureCollection"
        assert body["numberReturned"] == 2

    def test_items_pagination(self, client_with_dataset):
        """Test offset/limit pagination."""
        import geopandas as gpd
        from shapely.geometry import Point

        client, ds = client_with_dataset
        gdf = gpd.GeoDataFrame(
            {"value": list(range(10))},
            geometry=[Point(i, i) for i in range(10)],
            crs="EPSG:4326",
        )

        with patch("gispulse.adapters.http.routers.ogc_features_router.read_vector", return_value=gdf):
            resp = client.get(f"/ogc/collections/{ds.id}/items?limit=3&offset=0")

        assert resp.status_code == 200
        body = resp.json()
        assert body["numberReturned"] == 3
        assert body["numberMatched"] == 10

    def test_items_next_link_present(self, client_with_dataset):
        """Test that a 'next' link is included when more items exist."""
        import geopandas as gpd
        from shapely.geometry import Point

        client, ds = client_with_dataset
        gdf = gpd.GeoDataFrame(
            {"v": list(range(5))},
            geometry=[Point(i, i) for i in range(5)],
            crs="EPSG:4326",
        )

        with patch("gispulse.adapters.http.routers.ogc_features_router.read_vector", return_value=gdf):
            resp = client.get(f"/ogc/collections/{ds.id}/items?limit=2")

        body = resp.json()
        assert any(link["rel"] == "next" for link in body.get("links", []))


# ---------------------------------------------------------------------------
# Single feature
# ---------------------------------------------------------------------------


class TestOGCSingleFeature:
    def test_feature_with_mocked_loader(self, client_with_dataset):
        import geopandas as gpd
        from shapely.geometry import Point

        client, ds = client_with_dataset
        gdf = gpd.GeoDataFrame(
            {"name": ["Feature A"]},
            geometry=[Point(2.0, 48.0)],
            crs="EPSG:4326",
        )

        with patch("gispulse.adapters.http.routers.ogc_features_router.read_vector", return_value=gdf):
            resp = client.get(f"/ogc/collections/{ds.id}/items/0")

        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "Feature"
        assert body["id"] == "0"

    def test_feature_out_of_range_returns_404(self, client_with_dataset):
        import geopandas as gpd
        from shapely.geometry import Point

        client, ds = client_with_dataset
        gdf = gpd.GeoDataFrame(
            {"name": ["Feature A"]},
            geometry=[Point(0, 0)],
            crs="EPSG:4326",
        )

        with patch("gispulse.adapters.http.routers.ogc_features_router.read_vector", return_value=gdf):
            resp = client.get(f"/ogc/collections/{ds.id}/items/999")

        assert resp.status_code == 404

    def test_nonexistent_collection_feature_404(self, client):
        missing_id = uuid4()
        resp = client.get(f"/ogc/collections/{missing_id}/items/0")
        assert resp.status_code == 404
