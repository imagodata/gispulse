"""Tests for orchestration.worker.JobWorker — polling loop, status transitions, error paths."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gispulse.core.models import Dataset, Job, JobStatus
from gispulse.orchestration.job_queue import InMemoryJobQueue
from gispulse.orchestration.worker import JobWorker
from gispulse.persistence.repository import InMemoryRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": [1, 2], "geometry": [Point(0, 0), Point(1, 1)]},
        crs="EPSG:4326",
    )


@pytest.fixture
def results_dir(tmp_path) -> Path:
    d = tmp_path / "results"
    d.mkdir()
    return d


@pytest.fixture
def dataset_repo(tmp_path, sample_gdf) -> InMemoryRepository:
    repo: InMemoryRepository = InMemoryRepository()
    gpkg_path = tmp_path / "input.gpkg"
    sample_gdf.to_file(gpkg_path, driver="GPKG", layer="cities")
    ds = Dataset(id=uuid4(), name="cities", source_path=str(gpkg_path))
    repo.save(ds)
    return repo


@pytest.fixture
def job_repo() -> InMemoryRepository:
    return InMemoryRepository()


def _make_worker(queue, runner, dataset_repo, job_repo, results_dir) -> JobWorker:
    return JobWorker(
        queue=queue,
        runner=runner,
        dataset_repo=dataset_repo,
        job_repo=job_repo,
        results_dir=results_dir,
        poll_interval=0.05,
        max_concurrent=1,
    )


async def _drive(worker: JobWorker, until_idle_iters: int = 3) -> None:
    """Start the worker, let it drain, then stop."""
    task = asyncio.create_task(worker.start())
    # wait for queue to drain
    for _ in range(50):
        await asyncio.sleep(0.05)
        if not worker._active_tasks and worker._queue._queue.empty():  # type: ignore[attr-defined]
            break
    worker.stop()
    await asyncio.wait_for(task, timeout=2.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestJobWorkerLifecycle:
    async def test_start_stop_cleanly_with_empty_queue(self, job_repo, dataset_repo, results_dir):
        queue = InMemoryJobQueue()
        runner = MagicMock()
        worker = _make_worker(queue, runner, dataset_repo, job_repo, results_dir)

        task = asyncio.create_task(worker.start())
        await asyncio.sleep(0.15)
        worker.stop()
        await asyncio.wait_for(task, timeout=2.0)
        # runner should never have been called on an empty queue
        runner.run.assert_not_called()

    async def test_stop_sets_running_false(self, job_repo, dataset_repo, results_dir):
        queue = InMemoryJobQueue()
        runner = MagicMock()
        worker = _make_worker(queue, runner, dataset_repo, job_repo, results_dir)
        worker._running = True
        worker.stop()
        assert worker._running is False


@pytest.mark.asyncio
class TestJobWorkerSuccessPath:
    async def test_processes_job_and_marks_completed(
        self, sample_gdf, dataset_repo, job_repo, results_dir
    ):
        queue = InMemoryJobQueue()
        ds = dataset_repo.list_all()[0]  # type: ignore[attr-defined]
        job = Job(id=uuid4(), name="test", dataset_id=ds.id, parameters={"layer": "cities"})
        job_repo.save(job)
        await queue.enqueue(job)

        # Runner returns the gdf unchanged
        runner = MagicMock()
        runner.run.return_value = (job, sample_gdf)

        worker = _make_worker(queue, runner, dataset_repo, job_repo, results_dir)
        await _drive(worker)

        runner.run.assert_called_once()
        status = await queue.get_status(str(job.id))
        assert status is not None
        assert status["status"] == JobStatus.COMPLETED.value

        saved = job_repo.get(job.id)
        assert saved is not None
        assert saved.status == JobStatus.COMPLETED
        assert saved.completed_at is not None

    async def test_empty_result_does_not_write_file(
        self, dataset_repo, job_repo, results_dir
    ):
        queue = InMemoryJobQueue()
        ds = dataset_repo.list_all()[0]  # type: ignore[attr-defined]
        job = Job(id=uuid4(), name="empty", dataset_id=ds.id, parameters={"layer": "cities"})
        job_repo.save(job)
        await queue.enqueue(job)

        empty_gdf = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
        runner = MagicMock()
        runner.run.return_value = (job, empty_gdf)

        worker = _make_worker(queue, runner, dataset_repo, job_repo, results_dir)
        await _drive(worker)

        status = await queue.get_status(str(job.id))
        assert status["status"] == JobStatus.COMPLETED.value
        # No result file written
        assert list(results_dir.glob("*.gpkg")) == []


@pytest.mark.asyncio
class TestJobWorkerFailurePath:
    async def test_runner_exception_marks_job_failed(
        self, dataset_repo, job_repo, results_dir
    ):
        queue = InMemoryJobQueue()
        ds = dataset_repo.list_all()[0]  # type: ignore[attr-defined]
        job = Job(id=uuid4(), name="explode", dataset_id=ds.id, parameters={"layer": "cities"})
        job_repo.save(job)
        await queue.enqueue(job)

        runner = MagicMock()
        runner.run.side_effect = RuntimeError("boom")

        worker = _make_worker(queue, runner, dataset_repo, job_repo, results_dir)
        await _drive(worker)

        status = await queue.get_status(str(job.id))
        assert status is not None
        assert status["status"] == JobStatus.FAILED.value
        assert "boom" in (status.get("error") or "")
        saved = job_repo.get(job.id)
        assert saved.status == JobStatus.FAILED
        assert saved.error_message == "boom"
        assert saved.completed_at is not None

    async def test_missing_dataset_marks_job_failed(self, job_repo, results_dir, tmp_path):
        """Job references a dataset id absent from the repository → FAILED."""
        queue = InMemoryJobQueue()
        ghost_id = uuid4()
        job = Job(id=uuid4(), name="ghost", dataset_id=ghost_id)
        job_repo.save(job)
        await queue.enqueue(job)

        runner = MagicMock()
        empty_repo: InMemoryRepository = InMemoryRepository()

        worker = _make_worker(queue, runner, empty_repo, job_repo, results_dir)
        await _drive(worker)

        status = await queue.get_status(str(job.id))
        assert status["status"] == JobStatus.FAILED.value
        # Runner never invoked because load_dataset raised first
        runner.run.assert_not_called()


@pytest.mark.asyncio
class TestJobWorkerCancellation:
    async def test_skips_job_already_marked_failed(
        self, dataset_repo, job_repo, results_dir
    ):
        queue = InMemoryJobQueue()
        ds = dataset_repo.list_all()[0]  # type: ignore[attr-defined]
        job = Job(id=uuid4(), name="cancelled", dataset_id=ds.id, parameters={"layer": "cities"})
        job_repo.save(job)
        await queue.enqueue(job)
        # Pre-cancel: mark job as FAILED in queue before worker picks it up
        await queue.update_status(str(job.id), JobStatus.FAILED, error="cancelled")

        runner = MagicMock()
        worker = _make_worker(queue, runner, dataset_repo, job_repo, results_dir)
        await _drive(worker)

        # Runner must not have executed a cancelled job
        runner.run.assert_not_called()
