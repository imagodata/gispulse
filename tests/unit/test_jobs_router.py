"""
Unit tests for the Jobs router — create, list, get, cancel, edge cases.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from core.models import Dataset


@pytest.fixture(autouse=True)
def _use_memory_storage(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    from gispulse.adapters.http.rate_limit import limiter
    limiter.enabled = False


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


@pytest.fixture()
def client_with_dataset() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    ds = Dataset(name="parcels", source_path="/data/parcels.gpkg")
    app.state.dataset_repo.save(ds)
    return TestClient(app)


JOB_PAYLOAD = {
    "name": "batch_buffer",
    "parameters": {"rule_ids": []},
}


class TestCreateJob:
    def test_create_job_returns_202(self, client: TestClient) -> None:
        r = client.post("/jobs", json=JOB_PAYLOAD)
        assert r.status_code == 202
        body = r.json()
        assert body["name"] == "batch_buffer"
        assert body["status"] == "pending"
        assert "id" in body

    def test_create_job_missing_name_returns_422(self, client: TestClient) -> None:
        r = client.post("/jobs", json={"parameters": {}})
        assert r.status_code == 422

    def test_create_job_with_dataset_id(self, client_with_dataset: TestClient) -> None:
        # Get the dataset first
        datasets = client_with_dataset.get("/datasets").json()
        if datasets.get("items"):
            ds_id = datasets["items"][0]["id"]
            r = client_with_dataset.post("/jobs", json={
                "name": "with_ds",
                "dataset_id": ds_id,
                "parameters": {},
            })
            assert r.status_code == 202


class TestListJobs:
    def test_list_empty(self, client: TestClient) -> None:
        r = client.get("/jobs")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_after_create(self, client: TestClient) -> None:
        client.post("/jobs", json=JOB_PAYLOAD)
        r = client.get("/jobs")
        body = r.json()
        assert body["total"] >= 1

    def test_list_pagination(self, client: TestClient) -> None:
        for i in range(5):
            client.post("/jobs", json={**JOB_PAYLOAD, "name": f"job_{i}"})
        r = client.get("/jobs?limit=2&offset=0")
        body = r.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5


class TestGetJob:
    def test_get_existing_job(self, client: TestClient) -> None:
        created = client.post("/jobs", json=JOB_PAYLOAD).json()
        r = client.get(f"/jobs/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.get(f"/jobs/{uuid4()}")
        assert r.status_code == 404


class TestCancelJob:
    def test_cancel_pending_job(self, client: TestClient) -> None:
        created = client.post("/jobs", json=JOB_PAYLOAD).json()
        r = client.post(f"/jobs/{created['id']}/cancel")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "failed"

    def test_cancel_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.post(f"/jobs/{uuid4()}/cancel")
        assert r.status_code == 404
