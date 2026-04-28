"""
Unit tests for the Rules router — CRUD operations, validation, edge cases.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


@pytest.fixture(autouse=True)
def _use_memory_storage(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    # Disable rate limiter for tests to avoid 429 on burst operations
    from gispulse.adapters.http.rate_limit import limiter
    limiter.enabled = False


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    app = create_app()
    return TestClient(app)


RULE_PAYLOAD = {
    "name": "buffer_50m",
    "description": "Apply a 50m buffer",
    "scope": "global",
    "capability": "buffer",
    "config": {"distance": 50},
    "enabled": True,
}


class TestCreateRule:
    def test_create_rule_returns_201(self, client: TestClient) -> None:
        r = client.post("/rules", json=RULE_PAYLOAD)
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "buffer_50m"
        assert body["capability"] == "buffer"
        assert "id" in body

    def test_create_rule_missing_name_returns_422(self, client: TestClient) -> None:
        payload = {**RULE_PAYLOAD}
        del payload["name"]
        r = client.post("/rules", json=payload)
        assert r.status_code == 422

    def test_create_rule_missing_capability_returns_422(self, client: TestClient) -> None:
        payload = {**RULE_PAYLOAD}
        del payload["capability"]
        r = client.post("/rules", json=payload)
        assert r.status_code == 422

    def test_create_rule_defaults(self, client: TestClient) -> None:
        r = client.post("/rules", json={"name": "minimal", "capability": "filter"})
        assert r.status_code == 201
        body = r.json()
        assert body["enabled"] is True
        assert body["scope"] == "global"
        assert body["description"] == ""


class TestListRules:
    def test_list_empty(self, client: TestClient) -> None:
        r = client.get("/rules")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_after_create(self, client: TestClient) -> None:
        client.post("/rules", json=RULE_PAYLOAD)
        r = client.get("/rules")
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "buffer_50m"

    def test_list_pagination(self, client: TestClient) -> None:
        for i in range(5):
            client.post("/rules", json={**RULE_PAYLOAD, "name": f"rule_{i}", "capability": "buffer"})
        r = client.get("/rules?limit=2&offset=0")
        body = r.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5

        r2 = client.get("/rules?limit=2&offset=4")
        body2 = r2.json()
        assert len(body2["items"]) == 1


class TestGetRule:
    def test_get_existing_rule(self, client: TestClient) -> None:
        created = client.post("/rules", json=RULE_PAYLOAD).json()
        r = client.get(f"/rules/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_get_nonexistent_rule_returns_404(self, client: TestClient) -> None:
        r = client.get(f"/rules/{uuid4()}")
        assert r.status_code == 404


class TestUpdateRule:
    def test_update_rule(self, client: TestClient) -> None:
        created = client.post("/rules", json=RULE_PAYLOAD).json()
        updated_payload = {**RULE_PAYLOAD, "name": "buffer_100m", "config": {"distance": 100}}
        r = client.put(f"/rules/{created['id']}", json=updated_payload)
        assert r.status_code == 200
        assert r.json()["name"] == "buffer_100m"
        assert r.json()["config"]["distance"] == 100

    def test_update_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.put(f"/rules/{uuid4()}", json=RULE_PAYLOAD)
        assert r.status_code == 404


class TestDeleteRule:
    def test_delete_rule(self, client: TestClient) -> None:
        created = client.post("/rules", json=RULE_PAYLOAD).json()
        r = client.delete(f"/rules/{created['id']}")
        assert r.status_code == 204

        # Verify it's gone
        r2 = client.get(f"/rules/{created['id']}")
        assert r2.status_code == 404

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.delete(f"/rules/{uuid4()}")
        assert r.status_code == 404


class TestValidateRule:
    def test_validate_valid_rule(self, client: TestClient) -> None:
        created = client.post("/rules", json=RULE_PAYLOAD).json()
        r = client.post(f"/rules/{created['id']}/validate")
        assert r.status_code == 200
        body = r.json()
        assert "valid" in body
        assert "errors" in body

    def test_validate_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.post(f"/rules/{uuid4()}/validate")
        assert r.status_code == 404


class TestRuleCreateStrictValidation:
    def test_unknown_capability_rejected(self, client: TestClient) -> None:
        payload = {**RULE_PAYLOAD, "capability": "does_not_exist_cap"}
        r = client.post("/rules", json=payload)
        assert r.status_code == 422
        assert "Unknown capability" in r.text

    def test_extra_field_rejected(self, client: TestClient) -> None:
        payload = {**RULE_PAYLOAD, "layer": "cities"}
        r = client.post("/rules", json=payload)
        assert r.status_code == 422
        assert "layer" in r.text
