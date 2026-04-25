"""Tests for the jobs endpoint."""

from __future__ import annotations


from gispulse_sdk.models import JobCreate, JobResponse


JOB_JSON = {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "name": "batch_buffer",
    "status": "completed",
    "dataset_id": None,
    "parameters": {},
    "created_at": "2026-01-01T00:00:00",
    "started_at": "2026-01-01T00:00:01",
    "completed_at": "2026-01-01T00:00:02",
    "result_path": None,
    "error_message": None,
    "duration_seconds": 1.0,
}


class TestJobsCRUD:
    def test_create(self, client, mock_api):
        mock_api.post("/jobs").respond(202, json=JOB_JSON)
        job = client.jobs.create(JobCreate(name="batch_buffer"))
        assert isinstance(job, JobResponse)
        assert job.status == "completed"

    def test_list(self, client, mock_api):
        mock_api.get("/jobs").respond(200, json=[JOB_JSON])
        jobs = client.jobs.list()
        assert len(jobs) == 1

    def test_get(self, client, mock_api):
        mock_api.get("/jobs/3fa85f64-5717-4562-b3fc-2c963f66afa6").respond(200, json=JOB_JSON)
        job = client.jobs.get("3fa85f64-5717-4562-b3fc-2c963f66afa6")
        assert job.duration_seconds == 1.0

    def test_cancel(self, client, mock_api):
        mock_api.post("/jobs/3fa85f64-5717-4562-b3fc-2c963f66afa6/cancel").respond(
            200, json={"cancelled": True}
        )
        resp = client.jobs.cancel("3fa85f64-5717-4562-b3fc-2c963f66afa6")
        assert resp["cancelled"] is True
