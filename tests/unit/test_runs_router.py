"""Tests for GET /runs and GET /runs/{id} endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from gispulse.core.models import JobStatus
from gispulse.core.run_models import PipelineRun


@pytest.fixture
def client(tmp_path):
    import warnings
    warnings.filterwarnings("ignore")
    app = create_app()
    with TestClient(app) as c:
        # Override run_repo AFTER lifespan runs (lifespan would overwrite a pre-set value)
        from gispulse.persistence.run_repository import RunRepository
        c.app.state.run_repo = RunRepository(db_path=tmp_path / "test_runs.db")
        yield c


@pytest.fixture
def seeded_run(client) -> PipelineRun:
    run = PipelineRun(source="job", spec_ref="my_pipe", scope="63")
    run.status = JobStatus.COMPLETED
    run.ended_at = datetime.now(timezone.utc)
    client.app.state.run_repo.save(run)
    return run


class TestRunsRouter:
    def test_list_runs_empty(self, client):
        response = client.get("/runs")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_runs_returns_persisted(self, client, seeded_run):
        response = client.get("/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["run_id"] == str(seeded_run.run_id)
        assert item["source"] == "job"
        assert item["spec_ref"] == "my_pipe"
        assert item["status"] == "completed"

    def test_get_run_by_id(self, client, seeded_run):
        response = client.get(f"/runs/{seeded_run.run_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == str(seeded_run.run_id)
        assert data["scope"] == "63"

    def test_get_run_not_found(self, client):
        response = client.get(f"/runs/{uuid4()}")
        assert response.status_code == 404

    def test_list_runs_invalid_limit_returns_422(self, client):
        """P2b: limit=0 must be rejected (ge=1)."""
        response = client.get("/runs?limit=0")
        assert response.status_code == 422

    def test_list_runs_invalid_limit_too_large_returns_422(self, client):
        """P2b: limit=201 must be rejected (le=200)."""
        response = client.get("/runs?limit=201")
        assert response.status_code == 422

    def test_list_runs_invalid_offset_negative_returns_422(self, client):
        """P2b: offset=-1 must be rejected (ge=0)."""
        response = client.get("/runs?offset=-1")
        assert response.status_code == 422
