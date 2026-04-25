"""Tests for the async GISPulseAsyncClient."""

from __future__ import annotations

import pytest
import respx

from gispulse_sdk.async_client import GISPulseAsyncClient
from gispulse_sdk.models import HealthResponse

BASE_URL = "https://gispulse.test"


@pytest.fixture
def mock_api():
    with respx.mock(base_url=BASE_URL) as router:
        yield router


@pytest.mark.asyncio
async def test_health(mock_api):
    mock_api.get("/health").respond(200, json={"status": "ok", "version": "0.1.0"})
    async with GISPulseAsyncClient(BASE_URL, api_key="k") as c:
        h = await c.health()
        assert isinstance(h, HealthResponse)
        assert h.status == "ok"


@pytest.mark.asyncio
async def test_datasets_list(mock_api):
    mock_api.get("/datasets").respond(200, json=[{
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "name": "test",
        "data_category": "vector",
        "crs": "EPSG:4326",
        "metadata": {},
        "created_at": "2026-01-01T00:00:00",
    }])
    async with GISPulseAsyncClient(BASE_URL, api_key="k") as c:
        ds = await c.datasets.list()
        assert len(ds) == 1
        assert ds[0].name == "test"


@pytest.mark.asyncio
async def test_rules_list(mock_api):
    mock_api.get("/rules").respond(200, json=[{
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "name": "buf",
        "scope": "global",
        "capability": "buffer",
        "config": {},
        "enabled": True,
    }])
    async with GISPulseAsyncClient(BASE_URL, api_key="k") as c:
        rules = await c.rules.list()
        assert len(rules) == 1


@pytest.mark.asyncio
async def test_jobs_create(mock_api):
    mock_api.post("/jobs").respond(202, json={
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "name": "test_job",
        "status": "pending",
        "parameters": {},
        "created_at": "2026-01-01T00:00:00",
    })
    async with GISPulseAsyncClient(BASE_URL, api_key="k") as c:
        from gispulse_sdk.models import JobCreate
        job = await c.jobs.create(JobCreate(name="test_job"))
        assert job.status == "pending"


@pytest.mark.asyncio
async def test_scenarios_run(mock_api):
    mock_api.post("/scenarios/abc/run").respond(200, json={"status": "success"})
    async with GISPulseAsyncClient(BASE_URL, api_key="k") as c:
        result = await c.scenarios.run("abc")
        assert result["status"] == "success"


@pytest.mark.asyncio
async def test_ogc_collections(mock_api):
    mock_api.get("/ogc/collections").respond(200, json={"collections": [{"id": "parcels"}]})
    async with GISPulseAsyncClient(BASE_URL, api_key="k") as c:
        cols = await c.ogc.collections()
        assert len(cols) == 1


@pytest.mark.asyncio
async def test_sessions_create(mock_api):
    mock_api.post("/sessions").respond(200, json={
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "schema_name": "sess_abc",
        "pg_role": "sess_abc",
        "pg_password": "pass",
        "pg_notify_channel": "gispulse_sess_abc",
        "status": "active",
        "ttl_hours": 8,
        "created_at": "2026-01-01T00:00:00",
    })
    async with GISPulseAsyncClient(BASE_URL, api_key="k") as c:
        s = await c.sessions.create()
        assert s.status == "active"
        assert s.schema_name == "sess_abc"
