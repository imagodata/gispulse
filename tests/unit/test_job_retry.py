"""
Tests for job persistence and retry logic (issue #257).

Covers:
- Job model has `attempts` field
- recover_stale_jobs re-enqueues PENDING/RUNNING jobs
- Max retries (>3) marks job as FAILED with 'max retries exceeded'
- GET /jobs/{id} exposes `attempts` field
- Non-stale jobs (COMPLETED, FAILED) are not touched
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from gispulse.adapters.http.routers.jobs_router import recover_stale_jobs, _MAX_JOB_ATTEMPTS
from core.models import Job, JobStatus
from persistence.repository import InMemoryRepository


# ---------------------------------------------------------------------------
# Unit tests: recover_stale_jobs
# ---------------------------------------------------------------------------


class TestRecoverStaleJobs:
    def _make_repo(self, jobs: list[Job]) -> InMemoryRepository:
        repo = InMemoryRepository()
        for j in jobs:
            repo.save(j)
        return repo

    def test_pending_job_requeued(self, tmp_path):
        job = Job(name="pending_job", status=JobStatus.PENDING)
        repo = self._make_repo([job])
        n = recover_stale_jobs(
            job_repo=repo,
            dataset_repo=InMemoryRepository(),
            runner=None,  # type: ignore
            results_dir=tmp_path,
        )
        assert n == 1
        recovered = repo.get(job.id)
        assert recovered.status == JobStatus.PENDING
        assert recovered.attempts == 1

    def test_running_job_requeued(self, tmp_path):
        job = Job(name="running_job", status=JobStatus.RUNNING)
        repo = self._make_repo([job])
        n = recover_stale_jobs(
            job_repo=repo,
            dataset_repo=InMemoryRepository(),
            runner=None,  # type: ignore
            results_dir=tmp_path,
        )
        assert n == 1
        recovered = repo.get(job.id)
        assert recovered.attempts == 1

    def test_completed_job_not_touched(self, tmp_path):
        job = Job(name="done_job", status=JobStatus.COMPLETED, attempts=0)
        repo = self._make_repo([job])
        n = recover_stale_jobs(
            job_repo=repo,
            dataset_repo=InMemoryRepository(),
            runner=None,  # type: ignore
            results_dir=tmp_path,
        )
        assert n == 0
        intact = repo.get(job.id)
        assert intact.status == JobStatus.COMPLETED
        assert intact.attempts == 0

    def test_failed_job_not_touched(self, tmp_path):
        job = Job(name="failed_job", status=JobStatus.FAILED, attempts=1)
        repo = self._make_repo([job])
        n = recover_stale_jobs(
            job_repo=repo,
            dataset_repo=InMemoryRepository(),
            runner=None,  # type: ignore
            results_dir=tmp_path,
        )
        assert n == 0
        intact = repo.get(job.id)
        assert intact.attempts == 1

    def test_max_retries_exceeded_marks_failed(self, tmp_path):
        job = Job(name="exhausted", status=JobStatus.PENDING, attempts=_MAX_JOB_ATTEMPTS)
        repo = self._make_repo([job])
        n = recover_stale_jobs(
            job_repo=repo,
            dataset_repo=InMemoryRepository(),
            runner=None,  # type: ignore
            results_dir=tmp_path,
        )
        assert n == 0  # not requeued
        exhausted = repo.get(job.id)
        assert exhausted.status == JobStatus.FAILED
        assert exhausted.error_message == "max retries exceeded"
        assert exhausted.completed_at is not None

    def test_attempts_accumulate_across_calls(self, tmp_path):
        job = Job(name="recovering", status=JobStatus.PENDING, attempts=0)
        repo = self._make_repo([job])
        for _ in range(2):
            recover_stale_jobs(
                job_repo=repo,
                dataset_repo=InMemoryRepository(),
                runner=None,  # type: ignore
                results_dir=tmp_path,
            )
        recovered = repo.get(job.id)
        assert recovered.attempts == 2

    def test_multiple_stale_jobs(self, tmp_path):
        jobs = [
            Job(name=f"job_{i}", status=JobStatus.PENDING)
            for i in range(4)
        ]
        repo = self._make_repo(jobs)
        n = recover_stale_jobs(
            job_repo=repo,
            dataset_repo=InMemoryRepository(),
            runner=None,  # type: ignore
            results_dir=tmp_path,
        )
        assert n == 4

    def test_returns_zero_when_no_stale(self, tmp_path):
        repo = InMemoryRepository()
        n = recover_stale_jobs(
            job_repo=repo,
            dataset_repo=InMemoryRepository(),
            runner=None,  # type: ignore
            results_dir=tmp_path,
        )
        assert n == 0


# ---------------------------------------------------------------------------
# Integration tests: Job model field + API
# ---------------------------------------------------------------------------


class TestJobAttemptsField:
    def test_job_has_attempts_default_zero(self):
        job = Job()
        assert job.attempts == 0

    def test_job_attempts_can_be_set(self):
        job = Job(attempts=2)
        assert job.attempts == 2


class TestJobApiAttempts:
    @pytest.fixture()
    def client(self, monkeypatch):
        monkeypatch.setenv("GISPULSE_STORAGE", "memory")
        monkeypatch.delenv("GISPULSE_API_KEYS", raising=False)
        app = create_app()
        return TestClient(app, raise_server_exceptions=True)

    def test_create_job_returns_attempts_zero(self, client):
        resp = client.post("/jobs", json={"name": "test", "parameters": {}})
        assert resp.status_code == 202
        data = resp.json()
        assert "attempts" in data
        assert data["attempts"] == 0

    def test_get_job_exposes_attempts(self, client):
        resp = client.post("/jobs", json={"name": "test", "parameters": {}})
        job_id = resp.json()["id"]
        resp2 = client.get(f"/jobs/{job_id}")
        assert resp2.status_code == 200
        assert "attempts" in resp2.json()
