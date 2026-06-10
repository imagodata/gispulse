"""Tests for manifest v3 HTTP execution — vague 3 orchestration (issue #440).

Covers:
- JobRunner._execute_manifest dispatch path
- POST /manifests/run (202, 422 upfront validation)
- POST /manifests/validate (200)
- End-to-end worker integration with PipelineRun persistence
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.core.models import Job, JobStatus
from gispulse.orchestration.runner import JobRunner
from gispulse.persistence.repository import InMemoryRepository
from gispulse.rules.engine import RuleEngine


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_gdf():
    return gpd.GeoDataFrame(
        {"id": [1, 2], "val": [10, 20]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )


def _minimal_manifest_dict(name: str = "test_manifest") -> dict:
    """A valid inline v3 manifest with 1 source + 1 model (filter identity)."""
    return {
        "version": 3,
        "name": name,
        "sources": {"s": {"uri": "memory://s"}},
        "models": {
            "m": {
                "select": "s",
                "transform": [{"filter": {"expression": "val >= 0"}}],
            }
        },
    }


@pytest.fixture
def runner():
    repo = InMemoryRepository()
    engine = RuleEngine(repository=repo)
    return JobRunner(repository=repo, rule_engine=engine)


# ---------------------------------------------------------------------------
# JobRunner._execute_manifest path
# ---------------------------------------------------------------------------


def test_runner_manifest_dispatch_completes(runner):
    """job.parameters['manifest'] → _execute_manifest → COMPLETED."""
    from gispulse.runtime.manifest_runner import ManifestRunResult

    gdf = _make_gdf()
    job = Job(name="test_manifest", parameters={"manifest": _minimal_manifest_dict()})

    def _fake_run_manifest(manifest, *, source_loader=None, materializer=None, event_sink=None, run_id=None, steps_filter=None, resume_markers=None):
        return ManifestRunResult(materialized={}, execution_order=[])

    with patch("gispulse.runtime.manifest_runner.run_manifest", side_effect=_fake_run_manifest):
        updated_job, result_gdf = runner.run(job, gdf)

    assert updated_job.status == JobStatus.COMPLETED
    assert isinstance(result_gdf, gpd.GeoDataFrame)


def test_runner_manifest_takes_priority_over_pipeline_config(runner):
    """manifest wins when both manifest + pipeline_config present; manifest is executed."""
    from gispulse.runtime.manifest_runner import ManifestRunResult

    gdf = _make_gdf()
    job = Job(
        name="conflict_job",
        parameters={
            "manifest": _minimal_manifest_dict(),
            "pipeline_config": {
                "version": 2,
                "steps": [{"id": "s1", "type": "capability", "capability": "filter", "params": {}}],
            },
        },
    )

    executed = []

    def _fake_run_manifest(manifest, *, source_loader=None, materializer=None, event_sink=None, run_id=None, steps_filter=None, resume_markers=None):
        executed.append("manifest")
        return ManifestRunResult(materialized={}, execution_order=[])

    with patch("gispulse.runtime.manifest_runner.run_manifest", side_effect=_fake_run_manifest):
        updated_job, _ = runner.run(job, gdf)

    assert executed == ["manifest"]
    assert updated_job.status == JobStatus.COMPLETED


def test_runner_manifest_invalid_raises_failed(runner):
    """Invalid manifest dict (unresolved select) → job FAILED with descriptive error."""
    gdf = _make_gdf()
    bad_manifest = {
        "version": 3,
        "sources": {"s": {"uri": "memory://s"}},
        "models": {"m": {"select": "ghost"}},  # ghost not declared
    }
    job = Job(name="bad_manifest", parameters={"manifest": bad_manifest})

    with pytest.raises(Exception):
        runner.run(job, gdf)

    assert job.status == JobStatus.FAILED
    assert job.error_message is not None


def test_runner_manifest_incremental_fails_explicitly(runner):
    """materialize: incremental → FAILED with 'incremental' in error message."""
    gdf = _make_gdf()
    manifest = {
        "version": 3,
        "name": "inc_test",
        "sources": {"s": {"uri": "memory://s"}},
        "models": {
            "m": {
                "select": "s",
                "transform": [{"filter": {"expression": "val >= 0"}}],
                "materialize": "incremental",
            }
        },
    }
    job = Job(name="inc_manifest", parameters={"manifest": manifest})

    with pytest.raises(Exception):
        runner.run(job, gdf)

    assert job.status == JobStatus.FAILED
    assert job.error_message is not None
    assert "incremental" in job.error_message.lower() or "not implemented" in job.error_message.lower()


# ---------------------------------------------------------------------------
# P1 — canonical source loader (uri → read_vector, no dataset_id needed)
# ---------------------------------------------------------------------------


def test_runner_manifest_loads_sources_from_uri(runner, tmp_path):
    """Sources with real file URIs must be loaded via loader.load(), not the job GDF.

    The manifest declares 2 sources pointing to distinct GPKG files.
    No dataset_id is set on the job (worker would return empty GDF).
    After execution, the materialized model must contain the correct row
    from the first source (val=42), NOT the rows from the fallback GDF
    (val=10, val=20 from _make_gdf).
    """
    import geopandas as gpd
    from shapely.geometry import Point

    # Write 2 distinct source files
    src1 = gpd.GeoDataFrame(
        {"id": [99], "val": [42]}, geometry=[Point(2, 2)], crs="EPSG:4326"
    )
    src2 = gpd.GeoDataFrame(
        {"id": [88], "val": [7]}, geometry=[Point(3, 3)], crs="EPSG:4326"
    )
    path1 = str(tmp_path / "src1.gpkg")
    path2 = str(tmp_path / "src2.gpkg")
    src1.to_file(path1, driver="GPKG")
    src2.to_file(path2, driver="GPKG")

    manifest = {
        "version": 3,
        "name": "file_source_manifest",
        "sources": {
            "a": {"uri": path1},
            "b": {"uri": path2},
        },
        "models": {
            "out": {
                "select": "a",
                "transform": [{"filter": {"expression": "val >= 0"}}],
            }
        },
    }
    # Empty fallback GDF — if the loader falls back to this, id/val would be wrong
    empty_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "val": [10, 20]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )
    job = Job(
        name="file_source_job",
        parameters={"manifest": manifest},
        # dataset_id intentionally absent
    )
    updated_job, result_gdf = runner.run(job, empty_gdf)

    assert updated_job.status == JobStatus.COMPLETED
    assert not result_gdf.empty, "result must not be empty"
    assert list(result_gdf["id"]) == [99], (
        f"Expected id=[99] from src1, got {list(result_gdf['id'])} — "
        "source_loader likely fell back to the job's GDF instead of reading the file"
    )
    assert list(result_gdf["val"]) == [42]


# ---------------------------------------------------------------------------
# P2b — POST /jobs must reject reserved orchestration keys in parameters
# ---------------------------------------------------------------------------


def _make_jobs_app():
    """Minimal FastAPI app with jobs_router mounted, no auth."""
    from gispulse.adapters.http.routers.jobs_router import router as jobs_router
    from gispulse.orchestration.job_queue import InMemoryJobQueue

    app = FastAPI()
    app.state.job_repo = InMemoryRepository()
    app.state.dataset_repo = InMemoryRepository()
    app.state.job_runner = MagicMock()
    app.state.results_dir = Path("/tmp/test_results_jobs")
    app.state.job_queue = InMemoryJobQueue()
    app.include_router(jobs_router)
    return app


@pytest.mark.parametrize("reserved_key,hint", [
    ("manifest", "POST /manifests/run"),
    ("pipeline_config", "POST /pipelines"),
    ("trigger_depth", None),
])
def test_post_jobs_rejects_reserved_parameter_keys(reserved_key, hint):
    """POST /jobs with reserved orchestration keys → 422 with helpful message."""
    from fastapi.testclient import TestClient

    app = _make_jobs_app()
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/jobs", json={
        "name": "bad_job",
        "parameters": {reserved_key: {"version": 3}},
    })
    assert resp.status_code == 422, (
        f"Expected 422 for reserved key '{reserved_key}', got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    # Error should mention the reserved key
    body_text = str(body).lower()
    assert reserved_key.replace("_", " ") in body_text or reserved_key in body_text, (
        f"Expected '{reserved_key}' in error body, got: {body}"
    )
    if hint:
        assert hint.lower() in body_text or hint.split("/")[1] in body_text, (
            f"Expected hint '{hint}' in error body, got: {body}"
        )


# ---------------------------------------------------------------------------
# P2a — canonical validation: unknown capability rejected at both endpoints
# ---------------------------------------------------------------------------


def test_validate_endpoint_rejects_unknown_capability():
    """POST /manifests/validate rejects a manifest whose compiled steps reference
    an unknown capability — same as /pipelines/validate.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gispulse.orchestration.job_queue import InMemoryJobQueue

    app = FastAPI()
    app.state.job_repo = InMemoryRepository()
    app.state.dataset_repo = InMemoryRepository()
    app.state.job_runner = MagicMock()
    app.state.results_dir = Path("/tmp/test_results_p2a")
    app.state.job_queue = InMemoryJobQueue()
    from gispulse.adapters.http.routers.manifests_router import router as manifests_router
    app.include_router(manifests_router)

    bad_manifest = {
        "version": 3,
        "sources": {"s": {"uri": "memory://s"}},
        "models": {
            "m": {
                "select": "s",
                "transform": [{"__nonexistent_capability__": {}}],
            }
        },
    }
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/manifests/validate", json={"manifest": bad_manifest})
    # Either the transform parse fails with an error (unknown capability key),
    # or validation finds the capability unknown — either way valid=False.
    # The endpoint returns 200 with valid=False, not a 4xx.
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False, f"Expected valid=False for unknown capability, got: {body}"


def test_run_endpoint_rejects_unknown_capability():
    """POST /manifests/run returns 422 for a manifest whose compiled steps reference
    an unknown capability — same gate as /validate.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gispulse.orchestration.job_queue import InMemoryJobQueue

    app = FastAPI()
    app.state.job_repo = InMemoryRepository()
    app.state.dataset_repo = InMemoryRepository()
    app.state.job_runner = MagicMock()
    app.state.results_dir = Path("/tmp/test_results_p2a_run")
    app.state.job_queue = InMemoryJobQueue()
    from gispulse.adapters.http.routers.manifests_router import router as manifests_router
    app.include_router(manifests_router)

    bad_manifest = {
        "version": 3,
        "sources": {"s": {"uri": "memory://s"}},
        "models": {
            "m": {
                "select": "s",
                "transform": [{"__nonexistent_capability__": {}}],
            }
        },
    }
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/manifests/run", json={"manifest": bad_manifest})
    assert resp.status_code == 422, f"Expected 422 for unknown capability, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# HTTP router tests
# ---------------------------------------------------------------------------


from fastapi.testclient import TestClient
from fastapi import FastAPI
from gispulse.orchestration.job_queue import InMemoryJobQueue


def _make_test_app(tmp_path_for_results=None):
    """Minimal FastAPI app with manifests_router mounted, no auth."""
    from gispulse.adapters.http.routers.manifests_router import router as manifests_router

    app = FastAPI()
    app.state.job_repo = InMemoryRepository()
    app.state.dataset_repo = InMemoryRepository()
    app.state.job_runner = MagicMock()
    app.state.results_dir = Path(tmp_path_for_results or "/tmp/test_results_manifests")
    app.state.job_queue = InMemoryJobQueue()
    app.include_router(manifests_router)
    return app


@pytest.fixture
def client(tmp_path):
    app = _make_test_app(tmp_path / "results")
    (tmp_path / "results").mkdir(parents=True, exist_ok=True)
    return TestClient(app)


def test_post_manifests_run_returns_202(client):
    """POST /manifests/run with valid manifest → 202 + job_id."""
    body = {
        "manifest": _minimal_manifest_dict(),
    }
    resp = client.post("/manifests/run", json=body)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "pending"


def test_post_manifests_run_with_scope(client):
    """POST /manifests/run with scope param propagates scope to job parameters."""
    body = {
        "manifest": _minimal_manifest_dict("scoped_run"),
        "scope": "63",
    }
    resp = client.post("/manifests/run", json=body)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    job_repo = client.app.state.job_repo
    job = job_repo.get(UUID(data["job_id"]))
    assert job is not None
    assert job.parameters.get("scope") == "63"


def test_post_manifests_run_invalid_manifest_returns_422(client):
    """POST /manifests/run with invalid manifest (bad version) → 422."""
    body = {
        "manifest": {"version": 2, "steps": []},
    }
    resp = client.post("/manifests/run", json=body)
    assert resp.status_code == 422, resp.text


def test_post_manifests_run_unresolved_ref_returns_422(client):
    """POST /manifests/run with manifest that has unresolved select → 422."""
    body = {
        "manifest": {
            "version": 3,
            "sources": {"s": {"uri": "memory://s"}},
            "models": {"m": {"select": "ghost_source"}},
        }
    }
    resp = client.post("/manifests/run", json=body)
    assert resp.status_code == 422, resp.text


def test_post_manifests_validate_valid(client):
    """POST /manifests/validate with valid manifest → 200 {valid: true}."""
    body = {"manifest": _minimal_manifest_dict()}
    resp = client.post("/manifests/validate", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["valid"] is True
    assert isinstance(data["errors"], list)
    assert data["compiled_steps_count"] >= 1


def test_post_manifests_validate_invalid(client):
    """POST /manifests/validate with invalid manifest → 200 {valid: false, errors: [...]}."""
    body = {
        "manifest": {
            "version": 3,
            "sources": {"s": {"uri": "memory://s"}},
            "models": {"m": {"select": "nonexistent"}},
        }
    }
    resp = client.post("/manifests/validate", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["valid"] is False
    assert len(data["errors"]) > 0


# ---------------------------------------------------------------------------
# End-to-end worker integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_job_worker_end_to_end(tmp_path):
    """POST /manifests/run → job enqueued → worker processes → PipelineRun persisted."""
    from gispulse.orchestration.job_queue import InMemoryJobQueue
    from gispulse.orchestration.runner import JobRunner
    from gispulse.orchestration.worker import JobWorker
    from gispulse.persistence.repository import InMemoryRepository
    from gispulse.persistence.run_repository import RunRepository
    from gispulse.rules.engine import RuleEngine

    queue = InMemoryJobQueue()
    job_repo = InMemoryRepository()
    dataset_repo = InMemoryRepository()
    runner = JobRunner(
        repository=InMemoryRepository(),
        rule_engine=RuleEngine(repository=InMemoryRepository()),
    )
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    run_repo = RunRepository(db_path=tmp_path / "runs.db")

    emitted_events = []

    class _Sink:
        def emit(self, event_type, data):
            emitted_events.append(event_type)

    worker = JobWorker(
        queue=queue,
        runner=runner,
        dataset_repo=dataset_repo,
        job_repo=job_repo,
        run_repo=run_repo,
        event_sink=_Sink(),
        results_dir=results_dir,
        poll_interval=0.05,
    )

    # Write a real GeoDataFrame to disk so the worker can load it as the
    # dataset. This lets _execute_manifest's source_loader receive a proper
    # GDF (with CRS+geometry), which the PipelineExecutor requires even for
    # transform-free models (the CRS auto-injection path reads gdf.crs).
    from gispulse.core.models import Dataset

    gdf_data = _make_gdf()
    gpkg_path = tmp_path / "input.gpkg"
    gdf_data.to_file(str(gpkg_path), driver="GPKG", layer="default")
    dataset = Dataset(name="e2e_dataset", source_path=str(gpkg_path))
    dataset_repo.save(dataset)

    manifest = _minimal_manifest_dict("e2e_manifest")
    job = Job(
        name="e2e_manifest",
        dataset_id=dataset.id,
        parameters={"manifest": manifest, "scope": "test_scope"},
    )
    job_repo.save(job)
    await queue.enqueue(job)

    # Drive the worker until job reaches terminal state
    task = asyncio.create_task(worker.start())
    for _ in range(80):
        await asyncio.sleep(0.1)
        refreshed = job_repo.get(job.id)
        if refreshed and refreshed.status in (JobStatus.COMPLETED, JobStatus.FAILED):
            break
    worker.stop()
    await asyncio.wait_for(task, timeout=10.0)

    refreshed = job_repo.get(job.id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.COMPLETED, f"job failed: {refreshed.error_message}"

    runs = run_repo.list_all()
    assert len(runs) >= 1
    run = runs[0]
    assert run.spec_ref == "e2e_manifest"
    assert run.scope == "test_scope"
    assert run.status == JobStatus.COMPLETED

    assert "run.started" in emitted_events
    assert "run.completed" in emitted_events
