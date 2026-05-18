"""
Unit tests for the GISPulse FastAPI HTTP facade.

Uses starlette's TestClient (requires httpx as a dev dependency).
Each test operates against a fresh app instance to guarantee isolation.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from gispulse.core.models import Dataset


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_memory_storage(monkeypatch):
    """Force in-memory storage for all HTTP API tests."""
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")


@pytest.fixture()
def client() -> TestClient:
    """Return a TestClient backed by a fresh GISPulse app instance."""
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


@pytest.fixture()
def client_with_dataset() -> TestClient:
    """Return a TestClient with one Dataset pre-loaded in the repository."""
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    ds = Dataset(name="parcels", source_path="/data/parcels.gpkg")
    app.state.dataset_repo.save(ds)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_list_capabilities_returns_list(self, client: TestClient) -> None:
        """GET /capabilities must return a non-empty JSON array."""
        response = client.get("/capabilities")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) > 0

    def test_list_capabilities_items_have_required_fields(
        self, client: TestClient
    ) -> None:
        """Each capability entry must expose name, description and json_schema."""
        response = client.get("/capabilities")
        assert response.status_code == 200
        for item in response.json():
            assert "name" in item
            assert "description" in item
            assert "json_schema" in item

    def test_get_existing_capability(self, client: TestClient) -> None:
        """GET /capabilities/buffer must return details for the buffer capability."""
        response = client.get("/capabilities/buffer")
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "buffer"
        assert "json_schema" in body

    def test_get_nonexistent_capability_returns_404(
        self, client: TestClient
    ) -> None:
        """GET /capabilities/nonexistent must return 404 with standard error envelope."""
        response = client.get("/capabilities/nonexistent")
        assert response.status_code == 404
        body = response.json()
        # Standardized error envelope (Sprint R-8 #158)
        assert "error" in body
        assert body["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class TestRules:
    def test_create_rule_returns_201(self, client: TestClient) -> None:
        """POST /rules with valid payload must return 201 and the created rule."""
        payload = {
            "name": "buffer_50m",
            "description": "Apply a 50 m buffer",
            "capability": "buffer",
            "config": {"distance": 50},
        }
        response = client.post("/rules", json=payload)
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "buffer_50m"
        assert body["capability"] == "buffer"
        assert "id" in body

    def test_list_rules_returns_created_rule(self, client: TestClient) -> None:
        """After POST /rules, GET /rules must include the new rule."""
        payload = {
            "name": "dissolve_rule",
            "capability": "dissolve",
            "config": {"by": "district"},
        }
        post_resp = client.post("/rules", json=payload)
        assert post_resp.status_code == 201
        rule_id = post_resp.json()["id"]

        list_resp = client.get("/rules")
        assert list_resp.status_code == 200
        data = list_resp.json()
        ids = [r["id"] for r in data["items"]]
        assert rule_id in ids
        assert "total" in data

    def test_get_rule_by_id(self, client: TestClient) -> None:
        """GET /rules/{id} must return the exact rule that was created."""
        payload = {
            "name": "reproject_rule",
            "capability": "reproject",
            "config": {"target_crs": "EPSG:2154"},
        }
        post_resp = client.post("/rules", json=payload)
        rule_id = post_resp.json()["id"]

        get_resp = client.get(f"/rules/{rule_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == rule_id

    def test_get_nonexistent_rule_returns_404(self, client: TestClient) -> None:
        """GET /rules/{unknown-id} must return 404."""
        from uuid import uuid4

        response = client.get(f"/rules/{uuid4()}")
        assert response.status_code == 404

    def test_delete_rule(self, client: TestClient) -> None:
        """DELETE /rules/{id} must return 204 and the rule must no longer exist."""
        payload = {
            "name": "to_delete",
            "capability": "buffer",
            "config": {"distance": 10},
        }
        rule_id = client.post("/rules", json=payload).json()["id"]

        del_resp = client.delete(f"/rules/{rule_id}")
        assert del_resp.status_code == 204

        get_resp = client.get(f"/rules/{rule_id}")
        assert get_resp.status_code == 404

    def test_validate_valid_rule(self, client: TestClient) -> None:
        """POST /rules/{id}/validate must return valid=True for a well-formed rule."""
        payload = {
            "name": "buffer_valid",
            "capability": "buffer",
            "config": {"distance": 100},
        }
        rule_id = client.post("/rules", json=payload).json()["id"]

        val_resp = client.post(f"/rules/{rule_id}/validate")
        assert val_resp.status_code == 200
        body = val_resp.json()
        assert body["valid"] is True
        assert body["errors"] == []

    def test_validate_invalid_rule_returns_errors(
        self, client: TestClient
    ) -> None:
        """POST /rules/{id}/validate must return valid=False for a misconfigured rule.

        POST /rules now rejects invalid payloads at creation time, so we insert
        the broken rule straight into the repo to simulate the realistic case:
        a rule that became invalid after a capability schema migration.
        """
        from gispulse.core.models import Rule

        broken_rule = Rule(
            name="reproject_broken",
            capability="reproject",
            config={},  # missing required target_crs
        )
        client.app.state.rule_repo.save(broken_rule)

        val_resp = client.post(f"/rules/{broken_rule.id}/validate")
        assert val_resp.status_code == 200
        body = val_resp.json()
        assert body["valid"] is False
        assert len(body["errors"]) > 0


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class TestJobs:
    def test_create_job_returns_202(self, client: TestClient) -> None:
        """POST /jobs must accept job asynchronously and return 202."""
        payload = {"name": "empty_job", "parameters": {}}
        response = client.post("/jobs", json=payload)
        assert response.status_code == 202
        body = response.json()
        assert body["name"] == "empty_job"
        assert "id" in body
        # Async job starts as pending (background task may complete during test)
        assert body["status"] in ("pending", "completed")

    def test_create_job_with_rule_ids(self, client: TestClient) -> None:
        """POST /jobs with rule_ids referencing existing rules should complete."""
        # First create a rule
        rule_payload = {
            "name": "buf_job_rule",
            "capability": "buffer",
            "config": {"distance": 10},
        }
        rule_id = client.post("/rules", json=rule_payload).json()["id"]

        job_payload = {
            "name": "job_with_rule",
            "parameters": {"rule_ids": [rule_id]},
        }
        response = client.post("/jobs", json=job_payload)
        assert response.status_code == 202
        body = response.json()
        # Async job — status may be pending or already completed in test
        assert body["status"] in ("pending", "completed", "failed")

    def test_list_jobs_after_creation(self, client: TestClient) -> None:
        """GET /jobs must include jobs that were previously created."""
        job_id = client.post("/jobs", json={"name": "listed_job"}).json()["id"]

        response = client.get("/jobs")
        assert response.status_code == 200
        data = response.json()
        ids = [j["id"] for j in data["items"]]
        assert job_id in ids
        assert "total" in data

    def test_get_job_by_id(self, client: TestClient) -> None:
        """GET /jobs/{id} must return the exact job."""
        job_id = client.post("/jobs", json={"name": "fetch_job"}).json()["id"]

        response = client.get(f"/jobs/{job_id}")
        assert response.status_code == 200
        assert response.json()["id"] == job_id

    def test_get_nonexistent_job_returns_404(self, client: TestClient) -> None:
        """GET /jobs/{unknown-id} must return 404."""
        from uuid import uuid4

        response = client.get(f"/jobs/{uuid4()}")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


class TestDatasets:
    def test_list_datasets_empty_by_default(self, client: TestClient) -> None:
        """GET /datasets on a fresh app must return an empty paginated response."""
        response = client.get("/datasets")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_datasets_with_preloaded_data(
        self, client_with_dataset: TestClient
    ) -> None:
        """GET /datasets must return pre-loaded datasets."""
        response = client_with_dataset.get("/datasets")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "parcels"

    def test_get_dataset_by_id(
        self, client_with_dataset: TestClient
    ) -> None:
        """GET /datasets/{id} must return the dataset matching the id."""
        datasets = client_with_dataset.get("/datasets").json()["items"]
        ds_id = datasets[0]["id"]

        response = client_with_dataset.get(f"/datasets/{ds_id}")
        assert response.status_code == 200
        assert response.json()["id"] == ds_id

    def test_get_nonexistent_dataset_returns_404(
        self, client: TestClient
    ) -> None:
        """GET /datasets/{unknown-id} must return 404."""
        from uuid import uuid4

        response = client.get(f"/datasets/{uuid4()}")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Issue #11 — OpenAPI schema generation
# ---------------------------------------------------------------------------


class TestOpenAPISchema:
    def test_openapi_json_returns_200(self, client: TestClient) -> None:
        """GET /openapi.json must return a valid OpenAPI schema document."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

    def test_openapi_json_has_required_top_level_keys(
        self, client: TestClient
    ) -> None:
        """The OpenAPI schema must contain openapi, info and paths keys."""
        schema = client.get("/openapi.json").json()
        assert "openapi" in schema
        assert "info" in schema
        assert "paths" in schema

    def test_openapi_json_info_matches_app_metadata(
        self, client: TestClient
    ) -> None:
        """The OpenAPI info block must reflect the GISPulse title and version."""
        info = client.get("/openapi.json").json()["info"]
        assert info["title"] == "GISPulse"
        assert info["version"]  # version is set

    def test_openapi_json_contains_expected_paths(
        self, client: TestClient
    ) -> None:
        """Core API paths must be present in the OpenAPI schema."""
        paths = client.get("/openapi.json").json()["paths"]
        expected = ["/capabilities", "/rules", "/jobs", "/datasets", "/health"]
        for path in expected:
            assert path in paths, f"Missing path in OpenAPI schema: {path}"

    def test_docs_endpoint_returns_200(self, client: TestClient) -> None:
        """GET /docs (Swagger UI) must return an HTML page."""
        response = client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_schema_capabilities_returns_list(self, client: TestClient) -> None:
        """GET /schema/capabilities must return a non-empty list of capability schemas."""
        response = client.get("/schema/capabilities")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) > 0

    def test_schema_capabilities_items_have_required_fields(
        self, client: TestClient
    ) -> None:
        """Each entry in /schema/capabilities must have name, description and json_schema."""
        response = client.get("/schema/capabilities")
        assert response.status_code == 200
        for item in response.json():
            assert "name" in item
            assert "description" in item
            assert "json_schema" in item


# ---------------------------------------------------------------------------
# Issue #12 — Authentication middleware
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_ok(self, client: TestClient) -> None:
        """GET /health must return status=ok without authentication."""
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "checks" in body


class TestAuthDisabled:
    """When GISPULSE_API_KEYS is not set, all endpoints must be accessible."""

    def test_capabilities_accessible_without_key(self) -> None:
        """Without env var, /capabilities must return 200."""
        os.environ.pop("GISPULSE_API_KEYS", None)
        client = TestClient(create_app())
        response = client.get("/capabilities")
        assert response.status_code == 200

    def test_health_accessible_without_key(self) -> None:
        """Without env var, /health must return 200."""
        os.environ.pop("GISPULSE_API_KEYS", None)
        client = TestClient(create_app())
        response = client.get("/health")
        assert response.status_code == 200

    def test_docs_accessible_without_key(self) -> None:
        """Without env var, /docs must return 200."""
        os.environ.pop("GISPULSE_API_KEYS", None)
        client = TestClient(create_app())
        response = client.get("/docs")
        assert response.status_code == 200


class TestAuthEnabled:
    """When GISPULSE_API_KEYS is set, protected endpoints require a valid key."""

    VALID_KEY = "secret-test-key"

    @pytest.fixture(autouse=True)
    def set_api_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set GISPULSE_API_KEYS before each test in this class."""
        monkeypatch.setenv("GISPULSE_API_KEYS", self.VALID_KEY)

    @pytest.fixture()
    def auth_client(self) -> TestClient:
        """Return a TestClient built after the env var is set."""
        return TestClient(create_app())

    def test_protected_endpoint_without_key_returns_401(
        self, auth_client: TestClient
    ) -> None:
        """GET /capabilities without a key must return 401 when auth is enabled."""
        response = auth_client.get("/capabilities")
        assert response.status_code == 401

    def test_protected_endpoint_with_wrong_key_returns_401(
        self, auth_client: TestClient
    ) -> None:
        """GET /capabilities with a wrong key must return 401."""
        response = auth_client.get(
            "/capabilities", headers={"X-API-Key": "wrong-key"}
        )
        assert response.status_code == 401

    def test_protected_endpoint_with_valid_key_returns_200(
        self, auth_client: TestClient
    ) -> None:
        """GET /capabilities with the correct key must return 200."""
        response = auth_client.get(
            "/capabilities", headers={"X-API-Key": self.VALID_KEY}
        )
        assert response.status_code == 200

    def test_health_always_accessible_with_auth_enabled(
        self, auth_client: TestClient
    ) -> None:
        """GET /health must return 200 even when auth is enabled (no key supplied)."""
        response = auth_client.get("/health")
        assert response.status_code == 200

    def test_docs_always_accessible_with_auth_enabled(
        self, auth_client: TestClient
    ) -> None:
        """GET /docs must return 200 even when auth is enabled (no key supplied)."""
        response = auth_client.get("/docs")
        assert response.status_code == 200

    def test_openapi_json_always_accessible_with_auth_enabled(
        self, auth_client: TestClient
    ) -> None:
        """GET /openapi.json must return 200 even when auth is enabled."""
        response = auth_client.get("/openapi.json")
        assert response.status_code == 200

    def test_schema_capabilities_requires_auth(
        self, auth_client: TestClient
    ) -> None:
        """GET /schema/capabilities must return 401 without a key."""
        response = auth_client.get("/schema/capabilities")
        assert response.status_code == 401

    def test_schema_capabilities_accessible_with_valid_key(
        self, auth_client: TestClient
    ) -> None:
        """GET /schema/capabilities must return 200 with the correct key."""
        response = auth_client.get(
            "/schema/capabilities", headers={"X-API-Key": self.VALID_KEY}
        )
        assert response.status_code == 200

    def test_multiple_valid_keys_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When multiple keys are configured, any of them grants access."""
        monkeypatch.setenv("GISPULSE_API_KEYS", "key-one,key-two,key-three")
        client = TestClient(create_app())
        for key in ("key-one", "key-two", "key-three"):
            response = client.get("/capabilities", headers={"X-API-Key": key})
            assert response.status_code == 200, f"Key '{key}' was rejected unexpectedly"
