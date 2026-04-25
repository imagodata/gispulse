"""Tests for the /scenarios/* endpoints (CRUD + execution)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Force in-memory storage and disable rate limiting."""
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    from gispulse.adapters.http.rate_limit import limiter

    limiter.enabled = False


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    # The run endpoint depends on spatial_engine which is set up in the
    # lifespan.  Attach a minimal stub so tests don't crash on attribute
    # lookup.
    from unittest.mock import MagicMock

    app.state.spatial_engine = MagicMock()
    return TestClient(app)


def _create_payload(
    name: str = "test_scenario",
    description: str = "",
    **overrides,
) -> dict:
    """Build a valid ScenarioCreate payload."""
    payload = {
        "name": name,
        "dataset_id": None,
        "jobs": [],
        "rules": [],
        "metadata": {},
    }
    if description:
        payload["metadata"]["description"] = description
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# POST /scenarios — create
# ---------------------------------------------------------------------------


class TestCreateScenario:
    """POST /scenarios — create a new scenario."""

    def test_create_returns_201(self, client: TestClient) -> None:
        response = client.post("/scenarios", json=_create_payload())
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "test_scenario"
        assert "id" in body
        assert body["jobs"] == []
        assert body["rules"] == []
        assert body["version"] == 1

    def test_create_with_metadata(self, client: TestClient) -> None:
        payload = _create_payload(
            name="flood_risk",
            metadata={"region": "Ile-de-France", "priority": "high"},
        )
        response = client.post("/scenarios", json=payload)
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "flood_risk"
        assert body["metadata"]["region"] == "Ile-de-France"

    def test_create_422_on_missing_name(self, client: TestClient) -> None:
        """Missing required 'name' field triggers validation error."""
        response = client.post("/scenarios", json={"jobs": [], "rules": []})
        assert response.status_code == 422
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_create_422_on_empty_body(self, client: TestClient) -> None:
        response = client.post("/scenarios", json={})
        assert response.status_code == 422

    def test_create_422_on_invalid_json(self, client: TestClient) -> None:
        response = client.post(
            "/scenarios",
            content="not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /scenarios — list
# ---------------------------------------------------------------------------


class TestListScenarios:
    """GET /scenarios — paginated scenario list."""

    def test_list_empty(self, client: TestClient) -> None:
        response = client.get("/scenarios")
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_after_create(self, client: TestClient) -> None:
        client.post("/scenarios", json=_create_payload(name="s1"))
        client.post("/scenarios", json=_create_payload(name="s2"))

        response = client.get("/scenarios")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2
        names = {s["name"] for s in body["items"]}
        assert names == {"s1", "s2"}

    def test_list_pagination(self, client: TestClient) -> None:
        for i in range(5):
            client.post("/scenarios", json=_create_payload(name=f"s{i}"))

        response = client.get("/scenarios?limit=2&offset=0")
        body = response.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["offset"] == 0

    def test_list_pagination_offset(self, client: TestClient) -> None:
        for i in range(3):
            client.post("/scenarios", json=_create_payload(name=f"s{i}"))

        response = client.get("/scenarios?limit=2&offset=2")
        body = response.json()
        assert len(body["items"]) == 1
        assert body["total"] == 3


# ---------------------------------------------------------------------------
# GET /scenarios/{id} — get by ID
# ---------------------------------------------------------------------------


class TestGetScenario:
    """GET /scenarios/{id} — retrieve a single scenario."""

    def test_get_existing(self, client: TestClient) -> None:
        create_resp = client.post("/scenarios", json=_create_payload(name="my_scenario"))
        scenario_id = create_resp.json()["id"]

        response = client.get(f"/scenarios/{scenario_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == scenario_id
        assert body["name"] == "my_scenario"

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid4())
        response = client.get(f"/scenarios/{fake_id}")
        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "NOT_FOUND"

    def test_get_invalid_uuid_returns_422(self, client: TestClient) -> None:
        response = client.get("/scenarios/not-a-uuid")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /scenarios/{id}
# ---------------------------------------------------------------------------


class TestDeleteScenario:
    """DELETE /scenarios/{id} — remove a scenario."""

    def test_delete_existing_returns_204(self, client: TestClient) -> None:
        create_resp = client.post("/scenarios", json=_create_payload())
        scenario_id = create_resp.json()["id"]

        response = client.delete(f"/scenarios/{scenario_id}")
        assert response.status_code == 204

        # Verify it is gone
        get_resp = client.get(f"/scenarios/{scenario_id}")
        assert get_resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid4())
        response = client.delete(f"/scenarios/{fake_id}")
        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "NOT_FOUND"

    def test_delete_removes_from_list(self, client: TestClient) -> None:
        create_resp = client.post("/scenarios", json=_create_payload())
        scenario_id = create_resp.json()["id"]

        client.delete(f"/scenarios/{scenario_id}")

        list_resp = client.get("/scenarios")
        assert list_resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# POST /scenarios/{id}/run — execute scenario
# ---------------------------------------------------------------------------


class TestRunScenario:
    """POST /scenarios/{id}/run — execute a scenario."""

    def test_run_nonexistent_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid4())
        response = client.post(f"/scenarios/{fake_id}/run")
        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "NOT_FOUND"

    def test_run_empty_scenario_returns_success(self, client: TestClient) -> None:
        """A scenario with no rules and no graph should run successfully
        (sequential runner with empty rules list)."""
        create_resp = client.post("/scenarios", json=_create_payload())
        scenario_id = create_resp.json()["id"]

        response = client.post(f"/scenarios/{scenario_id}/run")
        assert response.status_code == 200
        body = response.json()
        assert body["scenario_id"] == scenario_id
        assert body["status"] == "success"
        assert isinstance(body["node_results"], list)
        assert isinstance(body["duration_ms"], (int, float))

    def test_run_invalid_uuid_returns_422(self, client: TestClient) -> None:
        response = client.post("/scenarios/not-a-uuid/run")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# PUT /scenarios/{id} — update
# ---------------------------------------------------------------------------


class TestUpdateScenario:
    """PUT /scenarios/{id} — update scenario name/graph/metadata."""

    def test_update_name(self, client: TestClient) -> None:
        create_resp = client.post("/scenarios", json=_create_payload(name="original"))
        scenario_id = create_resp.json()["id"]

        response = client.put(
            f"/scenarios/{scenario_id}",
            json={"name": "renamed"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "renamed"
        assert body["version"] == 2

    def test_update_nonexistent_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid4())
        response = client.put(
            f"/scenarios/{fake_id}",
            json={"name": "nope"},
        )
        assert response.status_code == 404
