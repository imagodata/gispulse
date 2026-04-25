"""
Tests for the portal datasets router (adapters/http/routers/portal_datasets_router.py).

Covers:
- GET /api/portal/datasets — list datasets (empty store)
- GET /api/portal/capabilities — list available capabilities
- GET /api/portal/datasets/{id}/styles — 404 for unknown dataset
- DELETE /api/portal/datasets/{id} — 404 for unknown dataset
- PATCH /api/portal/datasets/{id} — 404 for unknown dataset
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.delenv("GISPULSE_API_KEYS", raising=False)
    from gispulse.adapters.http.rate_limit import limiter
    limiter.enabled = False


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /api/portal/datasets
# ---------------------------------------------------------------------------


class TestListDatasets:
    def test_returns_200(self, client):
        resp = client.get("/api/portal/datasets")
        assert resp.status_code == 200

    def test_returns_list(self, client):
        data = client.get("/api/portal/datasets").json()
        assert isinstance(data, list)

    def test_empty_store_returns_empty_list(self, client):
        data = client.get("/api/portal/datasets").json()
        assert data == []


# ---------------------------------------------------------------------------
# GET /api/portal/capabilities
# ---------------------------------------------------------------------------


class TestListCapabilities:
    def test_returns_200(self, client):
        resp = client.get("/api/portal/capabilities")
        assert resp.status_code == 200

    def test_returns_list(self, client):
        data = client.get("/api/portal/capabilities").json()
        assert isinstance(data, list)

    def test_capabilities_not_empty(self, client):
        """The registry should have at least one capability registered."""
        data = client.get("/api/portal/capabilities").json()
        assert len(data) > 0

    def test_each_capability_has_name(self, client):
        data = client.get("/api/portal/capabilities").json()
        for cap in data:
            assert "name" in cap
            assert isinstance(cap["name"], str)

    def test_each_capability_has_description(self, client):
        data = client.get("/api/portal/capabilities").json()
        for cap in data:
            assert "description" in cap


# ---------------------------------------------------------------------------
# GET /api/portal/datasets/{id}/styles — unknown dataset
# ---------------------------------------------------------------------------


class TestGetDatasetStyles:
    def test_unknown_dataset_returns_404(self, client):
        fake_id = str(uuid4())
        resp = client.get(f"/api/portal/datasets/{fake_id}/styles")
        assert resp.status_code == 404

    def test_404_uses_error_envelope(self, client):
        fake_id = str(uuid4())
        body = client.get(f"/api/portal/datasets/{fake_id}/styles").json()
        assert "error" in body
        assert body["error"]["code"] == "NOT_FOUND"

    def test_invalid_uuid_returns_500(self, client):
        """Invalid UUID in path triggers unhandled ValueError -> 500."""
        resp = client.get("/api/portal/datasets/not-a-uuid/styles")
        assert resp.status_code == 500
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"


# ---------------------------------------------------------------------------
# DELETE /api/portal/datasets/{id} — unknown dataset
# ---------------------------------------------------------------------------


class TestDeleteDataset:
    def test_unknown_dataset_returns_404(self, client):
        fake_id = str(uuid4())
        resp = client.delete(f"/api/portal/datasets/{fake_id}")
        assert resp.status_code == 404

    def test_404_uses_error_envelope(self, client):
        fake_id = str(uuid4())
        body = client.delete(f"/api/portal/datasets/{fake_id}").json()
        assert "error" in body
        assert body["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# PATCH /api/portal/datasets/{id} — unknown dataset
# ---------------------------------------------------------------------------


class TestRenameDataset:
    def test_unknown_dataset_returns_404(self, client):
        fake_id = str(uuid4())
        resp = client.patch(
            f"/api/portal/datasets/{fake_id}",
            json={"name": "new_name"},
        )
        assert resp.status_code == 404

    def test_404_uses_error_envelope(self, client):
        fake_id = str(uuid4())
        body = client.patch(
            f"/api/portal/datasets/{fake_id}",
            json={"name": "new_name"},
        ).json()
        assert "error" in body
        assert body["error"]["code"] == "NOT_FOUND"

    def test_missing_name_returns_422(self, client):
        fake_id = str(uuid4())
        resp = client.patch(f"/api/portal/datasets/{fake_id}", json={})
        assert resp.status_code == 422
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# GET /api/portal/datasets/{id}/layers/{layer}/features — unknown dataset
# ---------------------------------------------------------------------------


class TestGetLayerFeatures:
    def test_unknown_dataset_returns_404(self, client):
        fake_id = str(uuid4())
        resp = client.get(f"/api/portal/datasets/{fake_id}/layers/foo/features")
        assert resp.status_code == 404

    def test_404_uses_error_envelope(self, client):
        fake_id = str(uuid4())
        body = client.get(f"/api/portal/datasets/{fake_id}/layers/foo/features").json()
        assert "error" in body
        assert body["error"]["code"] == "NOT_FOUND"
