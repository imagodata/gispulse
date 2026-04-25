"""
Unit tests for the Triggers router — CRUD, toggle, evaluate, edge cases.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


@pytest.fixture(autouse=True)
def _use_memory_storage(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


TRIGGER_PAYLOAD = {
    "name": "on_insert_parcels",
    "description": "Fire on parcel inserts",
    "event": "manual",
    "trigger_type": "api",
    "category": "data",
    "severity": "info",
    "conditions": {"table": "parcels"},
    "enabled": True,
    "auto_eval": False,
}


class TestCreateTrigger:
    def test_create_returns_201(self, client: TestClient) -> None:
        r = client.post("/triggers", json=TRIGGER_PAYLOAD)
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "on_insert_parcels"
        assert "id" in body
        assert body["enabled"] is True

    def test_create_minimal(self, client: TestClient) -> None:
        r = client.post("/triggers", json={"name": "minimal"})
        assert r.status_code == 201
        body = r.json()
        assert body["trigger_type"] == "api"
        assert body["event"] == "manual"

    def test_create_missing_name_returns_422(self, client: TestClient) -> None:
        r = client.post("/triggers", json={"description": "no name"})
        assert r.status_code == 422


class TestListTriggers:
    def test_list_empty(self, client: TestClient) -> None:
        r = client.get("/triggers")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_after_create(self, client: TestClient) -> None:
        client.post("/triggers", json=TRIGGER_PAYLOAD)
        r = client.get("/triggers")
        body = r.json()
        assert body["total"] == 1

    def test_list_pagination(self, client: TestClient) -> None:
        for i in range(5):
            client.post("/triggers", json={**TRIGGER_PAYLOAD, "name": f"trigger_{i}"})
        r = client.get("/triggers?limit=2&offset=0")
        body = r.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5


class TestGetTrigger:
    def test_get_existing(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        r = client.get(f"/triggers/{created['id']}")
        assert r.status_code == 200
        assert r.json()["name"] == "on_insert_parcels"

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.get(f"/triggers/{uuid4()}")
        assert r.status_code == 404


class TestUpdateTrigger:
    def test_update_trigger(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        updated = {**TRIGGER_PAYLOAD, "name": "renamed_trigger", "severity": "warning"}
        r = client.put(f"/triggers/{created['id']}", json=updated)
        assert r.status_code == 200
        assert r.json()["name"] == "renamed_trigger"

    def test_update_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.put(f"/triggers/{uuid4()}", json=TRIGGER_PAYLOAD)
        assert r.status_code == 404


class TestDeleteTrigger:
    def test_delete_trigger(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        r = client.delete(f"/triggers/{created['id']}")
        assert r.status_code == 204

        r2 = client.get(f"/triggers/{created['id']}")
        assert r2.status_code == 404

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.delete(f"/triggers/{uuid4()}")
        assert r.status_code == 404


class TestToggleTrigger:
    def test_toggle_disable(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        r = client.post(f"/triggers/{created['id']}/toggle")
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_toggle_reenable(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        client.post(f"/triggers/{created['id']}/toggle")  # disable
        r = client.post(f"/triggers/{created['id']}/toggle")  # re-enable
        assert r.status_code == 200
        assert r.json()["enabled"] is True

    def test_toggle_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.post(f"/triggers/{uuid4()}/toggle")
        assert r.status_code == 404


class TestEvaluateTrigger:
    def test_evaluate_trigger(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        r = client.post(
            f"/triggers/{created['id']}/evaluate",
            json={
                "records": [
                    {
                        "table_name": "parcels",
                        "operation": "INSERT",
                        "new_values": {"area": 100},
                    }
                ]
            },
        )
        assert r.status_code == 200

    def test_evaluate_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.post(
            f"/triggers/{uuid4()}/evaluate",
            json={"records": [{"table_name": "t", "operation": "INSERT"}]},
        )
        assert r.status_code == 404


class TestTriggerCreateStrictValidation:
    def test_extra_field_rejected(self, client: TestClient) -> None:
        payload = {**TRIGGER_PAYLOAD, "type": "attribute_threshold", "scope": "layer"}
        r = client.post("/triggers", json=payload)
        assert r.status_code == 422
        body = r.text
        assert "type" in body or "scope" in body
