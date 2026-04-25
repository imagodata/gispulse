"""
Unit tests for the Relations router — CRUD, detect, 404, 422.
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
    from gispulse.adapters.http.rate_limit import limiter
    limiter.enabled = False


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


RELATION_PAYLOAD = {
    "source_layer_name": "parcels",
    "target_layer_name": "owners",
    "relation_type": "fk",
    "source_field": "owner_id",
    "target_field": "id",
    "label": "parcels_to_owners",
}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class TestCreateRelation:
    def test_create_returns_201(self, client: TestClient) -> None:
        r = client.post("/relations", json=RELATION_PAYLOAD)
        assert r.status_code == 201
        body = r.json()
        assert body["source_layer_name"] == "parcels"
        assert body["target_layer_name"] == "owners"
        assert body["relation_type"] == "fk"
        assert body["source_field"] == "owner_id"
        assert body["target_field"] == "id"
        assert body["label"] == "parcels_to_owners"
        assert "id" in body
        assert body["confirmed"] is False

    def test_create_spatial_relation(self, client: TestClient) -> None:
        payload = {
            "source_layer_name": "batiments",
            "target_layer_name": "parcelles",
            "relation_type": "spatial",
            "spatial_op": "intersects",
            "label": "batiments intersecte parcelles",
        }
        r = client.post("/relations", json=payload)
        assert r.status_code == 201
        body = r.json()
        assert body["relation_type"] == "spatial"
        assert body["spatial_op"] == "intersects"

    def test_create_minimal(self, client: TestClient) -> None:
        # Only defaults — relation_type defaults to "spatial"
        r = client.post("/relations", json={})
        assert r.status_code == 201
        body = r.json()
        assert body["relation_type"] == "spatial"

    def test_create_with_confidence(self, client: TestClient) -> None:
        payload = {**RELATION_PAYLOAD, "confidence": 0.85}
        r = client.post("/relations", json=payload)
        assert r.status_code == 201
        assert r.json()["confidence"] == 0.85

    def test_create_invalid_confidence_returns_422(self, client: TestClient) -> None:
        payload = {**RELATION_PAYLOAD, "confidence": 2.0}
        r = client.post("/relations", json=payload)
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

class TestListRelations:
    def test_list_empty(self, client: TestClient) -> None:
        r = client.get("/relations")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_after_create(self, client: TestClient) -> None:
        client.post("/relations", json=RELATION_PAYLOAD)
        r = client.get("/relations")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["source_layer_name"] == "parcels"

    def test_list_filter_by_relation_type(self, client: TestClient) -> None:
        client.post("/relations", json=RELATION_PAYLOAD)
        client.post("/relations", json={
            "source_layer_name": "a",
            "target_layer_name": "b",
            "relation_type": "spatial",
            "spatial_op": "within",
        })
        r = client.get("/relations?relation_type=fk")
        body = r.json()
        assert len(body) == 1
        assert body[0]["relation_type"] == "fk"

    def test_list_filter_by_confirmed(self, client: TestClient) -> None:
        client.post("/relations", json=RELATION_PAYLOAD)
        r = client.get("/relations?confirmed=false")
        assert r.status_code == 200
        assert len(r.json()) == 1

        r2 = client.get("/relations?confirmed=true")
        assert r2.status_code == 200
        assert len(r2.json()) == 0


# ---------------------------------------------------------------------------
# Get by ID
# ---------------------------------------------------------------------------

class TestGetRelation:
    def test_get_existing(self, client: TestClient) -> None:
        cr = client.post("/relations", json=RELATION_PAYLOAD)
        rid = cr.json()["id"]
        r = client.get(f"/relations/{rid}")
        assert r.status_code == 200
        assert r.json()["id"] == rid

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid4())
        r = client.get(f"/relations/{fake_id}")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "NOT_FOUND"
        assert "not found" in body["error"]["message"].lower()

    def test_get_invalid_uuid_returns_422(self, client: TestClient) -> None:
        r = client.get("/relations/not-a-uuid")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDeleteRelation:
    def test_delete_existing(self, client: TestClient) -> None:
        cr = client.post("/relations", json=RELATION_PAYLOAD)
        rid = cr.json()["id"]
        r = client.delete(f"/relations/{rid}")
        assert r.status_code == 204

        # Confirm gone
        r2 = client.get(f"/relations/{rid}")
        assert r2.status_code == 404

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid4())
        r = client.delete(f"/relations/{fake_id}")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "NOT_FOUND"

    def test_delete_invalid_uuid_returns_422(self, client: TestClient) -> None:
        r = client.delete("/relations/not-a-uuid")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Detect (POST /relations/detect)
# ---------------------------------------------------------------------------

class TestDetectRelations:
    def test_detect_empty_layer_cache(self, client: TestClient) -> None:
        """With no loaded layers, detect should return an empty list."""
        r = client.post("/relations/detect")
        assert r.status_code == 200
        assert r.json() == []


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

class TestConfirmRelation:
    def test_confirm_existing(self, client: TestClient) -> None:
        cr = client.post("/relations", json=RELATION_PAYLOAD)
        rid = cr.json()["id"]
        assert cr.json()["confirmed"] is False

        r = client.post(f"/relations/{rid}/confirm")
        assert r.status_code == 200
        assert r.json()["confirmed"] is True

    def test_confirm_nonexistent_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid4())
        r = client.post(f"/relations/{fake_id}/confirm")
        assert r.status_code == 404
