"""
Unit tests for Sprint R-3 project endpoints:
  - GET /projects/{id}/stats
  - GET /projects/{id}/activity

Issues #150 and #151.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from gispulse.core.models import Dataset, Project


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_memory_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


@pytest.fixture()
def client_with_project() -> tuple[TestClient, Project]:
    """Return a TestClient with one Project pre-saved."""
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    project = Project(name="Test Project", description="For unit tests")
    app.state.project_repo.save(project)
    return TestClient(app), project


@pytest.fixture()
def client_with_project_and_dataset() -> tuple[TestClient, Project, Dataset]:
    """Return a TestClient with one Project and one Dataset pre-saved."""
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    ds = Dataset(name="parcels", source_path="/data/parcels.gpkg")
    app.state.dataset_repo.save(ds)
    project = Project(name="Geo Project", description="With data")
    project.datasets.append(ds.id)
    app.state.project_repo.save(project)
    return TestClient(app), project, ds


# ---------------------------------------------------------------------------
# GET /projects/{id}/stats — issue #150
# ---------------------------------------------------------------------------


class TestProjectStats:
    def test_stats_404_for_unknown_project(self, client: TestClient) -> None:
        """Returns 404 when the project does not exist."""
        fake_id = str(uuid.uuid4())
        res = client.get(f"/projects/{fake_id}/stats")
        assert res.status_code == 404

    def test_stats_returns_schema(self, client_with_project: tuple[TestClient, Project]) -> None:
        """Returns a valid stats object for an existing project."""
        client, project = client_with_project
        res = client.get(f"/projects/{project.id}/stats")
        assert res.status_code == 200
        body = res.json()
        assert body["project_id"] == str(project.id)
        assert "dataset_count" in body
        assert "layer_count" in body
        assert "rule_count" in body
        assert "trigger_count" in body
        assert "scenario_count" in body
        assert "total_feature_count" in body

    def test_stats_dataset_count_matches(
        self, client_with_project_and_dataset: tuple[TestClient, Project, Dataset]
    ) -> None:
        """dataset_count reflects the number of datasets associated to the project."""
        client, project, _ds = client_with_project_and_dataset
        res = client.get(f"/projects/{project.id}/stats")
        assert res.status_code == 200
        body = res.json()
        assert body["dataset_count"] == 1

    def test_stats_empty_project(self, client_with_project: tuple[TestClient, Project]) -> None:
        """An empty project has zeroed stats."""
        client, project = client_with_project
        res = client.get(f"/projects/{project.id}/stats")
        body = res.json()
        assert body["dataset_count"] == 0
        assert body["rule_count"] == 0
        assert body["trigger_count"] == 0

    def test_stats_last_activity_is_string(
        self, client_with_project: tuple[TestClient, Project]
    ) -> None:
        """last_activity is a non-empty ISO string."""
        client, project = client_with_project
        res = client.get(f"/projects/{project.id}/stats")
        body = res.json()
        assert isinstance(body["last_activity"], str)
        assert len(body["last_activity"]) > 0


# ---------------------------------------------------------------------------
# GET /projects/{id}/activity — issue #151
# ---------------------------------------------------------------------------


class TestProjectActivity:
    def test_activity_404_for_unknown_project(self, client: TestClient) -> None:
        """Returns 404 when the project does not exist."""
        fake_id = str(uuid.uuid4())
        res = client.get(f"/projects/{fake_id}/activity")
        assert res.status_code == 404

    def test_activity_returns_schema(
        self, client_with_project: tuple[TestClient, Project]
    ) -> None:
        """Returns a valid activity response with items list."""
        client, project = client_with_project
        res = client.get(f"/projects/{project.id}/activity")
        assert res.status_code == 200
        body = res.json()
        assert body["project_id"] == str(project.id)
        assert isinstance(body["items"], list)
        assert isinstance(body["total"], int)

    def test_activity_contains_project_created_event(
        self, client_with_project: tuple[TestClient, Project]
    ) -> None:
        """A project_created event is always present."""
        client, project = client_with_project
        res = client.get(f"/projects/{project.id}/activity")
        body = res.json()
        types = [item["event_type"] for item in body["items"]]
        assert "project_created" in types

    def test_activity_event_schema(
        self, client_with_project: tuple[TestClient, Project]
    ) -> None:
        """Each activity item has required fields."""
        client, project = client_with_project
        res = client.get(f"/projects/{project.id}/activity")
        body = res.json()
        assert len(body["items"]) > 0
        for item in body["items"]:
            assert "id" in item
            assert "event_type" in item
            assert "title" in item
            assert "status" in item
            assert "timestamp" in item
            assert "metadata" in item

    def test_activity_limit_param(
        self, client_with_project: tuple[TestClient, Project]
    ) -> None:
        """limit parameter controls how many items are returned."""
        client, project = client_with_project
        res = client.get(f"/projects/{project.id}/activity?limit=1")
        body = res.json()
        assert len(body["items"]) <= 1

    def test_activity_dataset_import_event_present(
        self, client_with_project_and_dataset: tuple[TestClient, Project, Dataset]
    ) -> None:
        """When a project has datasets, dataset_import events appear in the timeline."""
        client, project, _ds = client_with_project_and_dataset
        res = client.get(f"/projects/{project.id}/activity")
        body = res.json()
        types = [item["event_type"] for item in body["items"]]
        assert "dataset_import" in types

    def test_activity_sorted_newest_first(
        self, client_with_project: tuple[TestClient, Project]
    ) -> None:
        """Items are ordered newest timestamp first."""
        client, project = client_with_project
        res = client.get(f"/projects/{project.id}/activity")
        body = res.json()
        timestamps = [item["timestamp"] for item in body["items"]]
        assert timestamps == sorted(timestamps, reverse=True)
