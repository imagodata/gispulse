"""
Unit tests for the Catalog router — basemaps, providers, projections, search.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    from gispulse.adapters.http.rate_limit import limiter
    limiter.enabled = False


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Basemaps
# ---------------------------------------------------------------------------

class TestBasemaps:
    def test_list_basemaps_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/catalog/basemaps")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)

    def test_list_basemaps_with_search(self, client: TestClient) -> None:
        r = client.get("/api/catalog/basemaps?search=osm")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_basemaps_with_pagination(self, client: TestClient) -> None:
        r = client.get("/api/catalog/basemaps?limit=5&offset=0")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_basemaps_invalid_limit_returns_422(self, client: TestClient) -> None:
        r = client.get("/api/catalog/basemaps?limit=0")
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_list_basemaps_limit_too_high_returns_422(self, client: TestClient) -> None:
        r = client.get("/api/catalog/basemaps?limit=999")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class TestProviders:
    def test_list_providers_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/catalog/providers")
        assert r.status_code == 200
        body = r.json()
        # Providers can be a list or dict — just check it succeeds
        assert body is not None


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------

class TestProjections:
    def test_list_projections_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/catalog/projections")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)

    def test_list_projections_with_search(self, client: TestClient) -> None:
        r = client.get("/api/catalog/projections?search=lambert")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_projections_with_tags(self, client: TestClient) -> None:
        r = client.get("/api/catalog/projections?tags=france")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# Cross-domain search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/catalog/search?q=osm")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)

    def test_search_with_domain_filter(self, client: TestClient) -> None:
        r = client.get("/api/catalog/search?q=osm&domain=basemap")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_search_missing_q_returns_422(self, client: TestClient) -> None:
        r = client.get("/api/catalog/search")
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_search_with_pagination(self, client: TestClient) -> None:
        r = client.get("/api/catalog/search?q=test&limit=10&offset=0")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# Entry by ID
# ---------------------------------------------------------------------------

class TestGetEntry:
    def test_get_nonexistent_entry_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/catalog/entry/nonexistent_id")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "NOT_FOUND"
