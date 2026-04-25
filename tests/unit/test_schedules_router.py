"""
Unit tests for the Schedules router — CRUD, 404, 422, tier gating.

The scheduler dependency returns 503 when no scheduler is attached.
We mock app.state.scheduler to bypass that and test CRUD logic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


# ---------------------------------------------------------------------------
# Fake scheduler for in-memory testing
# ---------------------------------------------------------------------------

@dataclass
class _FakeScheduledPipeline:
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    cron_expression: str = ""
    pipeline_config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None
    created_by: str | None = None


class _FakeScheduler:
    """Minimal in-memory scheduler that satisfies the router contract."""

    def __init__(self) -> None:
        self._store: dict[str, _FakeScheduledPipeline] = {}

    async def add(self, sp: Any) -> _FakeScheduledPipeline:
        fake = _FakeScheduledPipeline(
            name=sp.name,
            cron_expression=sp.cron_expression,
            pipeline_config=sp.pipeline_config,
            enabled=sp.enabled,
            created_by=getattr(sp, "created_by", None),
        )
        self._store[str(fake.id)] = fake
        return fake

    async def list_schedules(self) -> list[_FakeScheduledPipeline]:
        return list(self._store.values())

    def get(self, schedule_id: str) -> _FakeScheduledPipeline | None:
        return self._store.get(schedule_id)

    async def update(self, schedule_id: str, **kwargs: Any) -> _FakeScheduledPipeline | None:
        sp = self._store.get(schedule_id)
        if sp is None:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(sp, key):
                setattr(sp, key, value)
        return sp

    async def remove(self, schedule_id: str) -> bool:
        return self._store.pop(schedule_id, None) is not None

    async def run_now(self, schedule_id: str) -> str | None:
        if schedule_id not in self._store:
            return None
        return str(uuid4())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.setenv("GISPULSE_TIER", "pro")
    from persistence.tier import make_test_license_key
    monkeypatch.setenv("GISPULSE_LICENSE_KEY", make_test_license_key("pro"))
    monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "1")
    from gispulse.adapters.http.rate_limit import limiter
    limiter.enabled = False


@pytest.fixture()
def client() -> TestClient:
    from persistence.tier import make_test_license_key
    os.environ["GISPULSE_STORAGE"] = "memory"
    os.environ["GISPULSE_TIER"] = "pro"
    os.environ["GISPULSE_LICENSE_KEY"] = make_test_license_key("pro")
    os.environ["GISPULSE_LICENCE_SKIP_VERIFY"] = "1"
    app = create_app()
    app.state.scheduler = _FakeScheduler()
    return TestClient(app)


SCHEDULE_PAYLOAD = {
    "name": "hourly",
    "cron_expression": "0 * * * *",
    "pipeline_config": {},
    "enabled": True,
}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class TestCreateSchedule:
    def test_create_returns_201(self, client: TestClient) -> None:
        r = client.post("/schedules", json=SCHEDULE_PAYLOAD)
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "hourly"
        assert body["cron_expression"] == "0 * * * *"
        assert body["enabled"] is True
        assert "id" in body

    def test_create_missing_name_returns_422(self, client: TestClient) -> None:
        payload = {
            "cron_expression": "0 * * * *",
            "pipeline_config": {},
        }
        # name is required — omitting it triggers a validation error
        r = client.post("/schedules", json={k: v for k, v in payload.items()})
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_create_empty_name_returns_422(self, client: TestClient) -> None:
        payload = {**SCHEDULE_PAYLOAD, "name": ""}
        r = client.post("/schedules", json=payload)
        assert r.status_code == 422

    def test_create_empty_cron_returns_422(self, client: TestClient) -> None:
        payload = {**SCHEDULE_PAYLOAD, "cron_expression": ""}
        r = client.post("/schedules", json=payload)
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

class TestListSchedules:
    def test_list_empty(self, client: TestClient) -> None:
        r = client.get("/schedules")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_after_create(self, client: TestClient) -> None:
        client.post("/schedules", json=SCHEDULE_PAYLOAD)
        r = client.get("/schedules")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["name"] == "hourly"


# ---------------------------------------------------------------------------
# Get by ID
# ---------------------------------------------------------------------------

class TestGetSchedule:
    def test_get_existing(self, client: TestClient) -> None:
        cr = client.post("/schedules", json=SCHEDULE_PAYLOAD)
        sid = cr.json()["id"]
        r = client.get(f"/schedules/{sid}")
        assert r.status_code == 200
        assert r.json()["id"] == sid

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid4())
        r = client.get(f"/schedules/{fake_id}")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDeleteSchedule:
    def test_delete_existing(self, client: TestClient) -> None:
        cr = client.post("/schedules", json=SCHEDULE_PAYLOAD)
        sid = cr.json()["id"]
        r = client.delete(f"/schedules/{sid}")
        assert r.status_code == 204

        # Confirm gone
        r2 = client.get(f"/schedules/{sid}")
        assert r2.status_code == 404

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        fake_id = str(uuid4())
        r = client.delete(f"/schedules/{fake_id}")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# No scheduler → 503
# ---------------------------------------------------------------------------

class TestSchedulerUnavailable:
    def test_no_scheduler_returns_503(self, client: TestClient) -> None:
        # Force scheduler to None after app startup to simulate unavailable scheduler
        client.app.state.scheduler = None
        r = client.get("/schedules")
        assert r.status_code == 503
        body = r.json()
        assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
