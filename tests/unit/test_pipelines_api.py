"""Tests for the pipelines API router (#403) and trigger operations (#404)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# #403 — Pipeline v2 API
# ---------------------------------------------------------------------------


class TestPipelineValidate:
    """POST /pipelines/validate."""

    def test_validate_valid_pipeline(self, client: TestClient):
        resp = client.post("/pipelines/validate", json={
            "steps": [
                {"id": "s1", "type": "capability", "capability": "filter",
                 "params": {"expression": "value > 10"}},
                {"id": "s2", "type": "capability", "capability": "buffer",
                 "params": {"distance": 50}, "input": "s1"},
            ],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["issues"] == []

    def test_validate_unknown_capability(self, client: TestClient):
        resp = client.post("/pipelines/validate", json={
            "steps": [
                {"id": "s1", "type": "capability", "capability": "nonexistent_cap",
                 "params": {}},
            ],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert any("nonexistent_cap" in i["message"] for i in body["issues"])

    def test_validate_duplicate_ids(self, client: TestClient):
        resp = client.post("/pipelines/validate", json={
            "steps": [
                {"id": "dup", "type": "capability", "capability": "filter", "params": {}},
                {"id": "dup", "type": "capability", "capability": "buffer", "params": {"distance": 1}},
            ],
        })
        body = resp.json()
        assert body["valid"] is False
        assert any("Duplicate" in i["message"] for i in body["issues"])

    def test_validate_empty_steps_rejected(self, client: TestClient):
        resp = client.post("/pipelines/validate", json={"steps": []})
        assert resp.status_code == 422


class TestPipelineExamples:
    """GET /pipelines/examples."""

    def test_list_examples(self, client: TestClient):
        resp = client.get("/pipelines/examples")
        assert resp.status_code == 200
        examples = resp.json()
        assert isinstance(examples, list)
        assert len(examples) >= 2
        for ex in examples:
            assert "name" in ex
            assert "spec" in ex
            assert ex["spec"]["version"] == 2

    def test_examples_have_valid_steps(self, client: TestClient):
        resp = client.get("/pipelines/examples")
        for ex in resp.json():
            assert len(ex["spec"]["steps"]) > 0


class TestPipelineExecute:
    """POST /pipelines/execute — requires dataset_id or input_path."""

    def test_execute_no_input_returns_422(self, client: TestClient):
        resp = client.post("/pipelines/execute", json={
            "steps": [
                {"id": "s1", "type": "capability", "capability": "filter",
                 "params": {"expression": "value > 10"}},
            ],
        })
        assert resp.status_code == 422

    def test_execute_unknown_dataset_returns_404(self, client: TestClient):
        resp = client.post("/pipelines/execute", json={
            "dataset_id": "00000000-0000-0000-0000-000000000000",
            "steps": [
                {"id": "s1", "type": "capability", "capability": "filter",
                 "params": {"expression": "value > 10"}},
            ],
        })
        assert resp.status_code == 404

    def test_execute_nonexistent_file_returns_400(self, client: TestClient):
        resp = client.post("/pipelines/execute", json={
            "input_path": "/nonexistent.gpkg",
            "steps": [
                {"id": "s1", "type": "capability", "capability": "filter",
                 "params": {"expression": "value > 10"}},
            ],
        })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# #404 — Trigger Operations CRUD
# ---------------------------------------------------------------------------


class TestTriggerOperationsCRUD:
    """CRUD on /triggers/{id}/operations."""

    @pytest.fixture
    def trigger_id(self, client: TestClient) -> str:
        """Create a trigger and return its UUID."""
        resp = client.post("/triggers", json={
            "name": "test_ops_trigger",
            "event": "manual",
            "trigger_type": "dml",
            "conditions": {"table": "parcels", "events": ["INSERT"], "operations": []},
        })
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_list_empty_operations(self, client: TestClient, trigger_id: str):
        resp = client.get(f"/triggers/{trigger_id}/operations")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_add_operation(self, client: TestClient, trigger_id: str):
        resp = client.post(f"/triggers/{trigger_id}/operations", json={
            "phase": "before",
            "operation": "st_within",
            "field": "zone_id",
            "distant_table": "zones",
            "distant_field": "id",
            "order": 1,
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["op_id"] == 0
        assert body["phase"] == "before"
        assert body["operation"] == "st_within"

    def test_list_after_add(self, client: TestClient, trigger_id: str):
        # Add one
        client.post(f"/triggers/{trigger_id}/operations", json={
            "phase": "after",
            "operation": "count_st_contains",
            "field": "parcel_count",
            "distant_table": "buildings",
            "distant_field": "id",
        })
        resp = client.get(f"/triggers/{trigger_id}/operations")
        assert resp.status_code == 200
        ops = resp.json()
        assert len(ops) == 1
        assert ops[0]["operation"] == "count_st_contains"

    def test_update_operation(self, client: TestClient, trigger_id: str):
        # Add
        client.post(f"/triggers/{trigger_id}/operations", json={
            "phase": "before",
            "operation": "st_within",
            "field": "zone_id",
            "distant_table": "zones",
            "distant_field": "id",
        })
        # Update
        resp = client.put(f"/triggers/{trigger_id}/operations/0", json={
            "phase": "before",
            "operation": "st_nearest",
            "field": "nearest_zone",
            "distant_table": "zones",
            "distant_field": "name",
            "order": 5,
        })
        assert resp.status_code == 200
        assert resp.json()["operation"] == "st_nearest"
        assert resp.json()["order"] == 5

    def test_delete_operation(self, client: TestClient, trigger_id: str):
        # Add
        client.post(f"/triggers/{trigger_id}/operations", json={
            "phase": "before",
            "operation": "st_within",
            "field": "zone_id",
            "distant_table": "zones",
            "distant_field": "id",
        })
        # Delete
        resp = client.delete(f"/triggers/{trigger_id}/operations/0")
        assert resp.status_code == 204
        # Verify empty
        resp = client.get(f"/triggers/{trigger_id}/operations")
        assert resp.json() == []

    def test_delete_nonexistent_operation(self, client: TestClient, trigger_id: str):
        resp = client.delete(f"/triggers/{trigger_id}/operations/99")
        assert resp.status_code == 404

    def test_operations_on_nonexistent_trigger(self, client: TestClient):
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = client.get(f"/triggers/{fake_id}/operations")
        assert resp.status_code == 404
