"""
SDK end-to-end integration tests against a TestClient FastAPI app (issue #258).

These tests verify that GISPulseClient communicates correctly with the HTTP API
by injecting a Starlette TestClient as the transport layer.  No real network
calls are made.

Lifecycle flows tested:
- health()
- datasets: list (empty)
- jobs: create → get → cancel
- rules: create → list → get → delete
"""

from __future__ import annotations

import sys
import os

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app

# SDK may not be installed as a package; add sdk/ to sys.path
_SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sdk")
if _SDK_PATH not in sys.path:
    sys.path.insert(0, _SDK_PATH)

from gispulse_sdk.client import GISPulseClient  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: inject TestClient into the SDK client
# ---------------------------------------------------------------------------


@pytest.fixture()
def sdk(monkeypatch) -> GISPulseClient:
    """GISPulseClient backed by a fresh in-memory FastAPI TestClient."""
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.delenv("GISPULSE_API_KEYS", raising=False)
    app = create_app()
    # Disable rate limiting so the shared limiter state doesn't bleed across tests.
    import gispulse.adapters.http.rate_limit as _rl
    _rl.limiter.enabled = False
    tc = TestClient(app, raise_server_exceptions=True)
    client = GISPulseClient("http://testserver")
    # Inject TestClient as the HTTP transport (same interface as httpx.Client)
    client._http = tc
    yield client
    _rl.limiter.enabled = True


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestSDKHealth:
    def test_health_returns_ok(self, sdk):
        resp = sdk.health()
        assert resp.status == "ok"

    def test_health_has_version(self, sdk):
        resp = sdk.health()
        assert resp.version


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


class TestSDKDatasets:
    def test_list_datasets_empty(self, sdk):
        datasets = sdk.datasets.list()
        assert isinstance(datasets, list)
        assert len(datasets) == 0

    def test_list_datasets_returns_list_type(self, sdk):
        result = sdk.datasets.list()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class TestSDKJobs:
    def test_create_job_returns_response(self, sdk):
        from gispulse_sdk.models import JobCreate
        job = sdk.jobs.create(JobCreate(name="sdk_test_job", parameters={}))
        assert job.id is not None
        assert job.name == "sdk_test_job"
        assert job.status in ("pending", "running", "completed", "failed")

    def test_create_job_has_attempts_field(self, sdk):
        from gispulse_sdk.models import JobCreate
        job = sdk.jobs.create(JobCreate(name="sdk_test_job", parameters={}))
        assert hasattr(job, "attempts")

    def test_get_job_after_create(self, sdk):
        from gispulse_sdk.models import JobCreate
        created = sdk.jobs.create(JobCreate(name="test_get", parameters={}))
        fetched = sdk.jobs.get(created.id)
        assert fetched.id == created.id
        assert fetched.name == "test_get"

    def test_list_jobs_after_create(self, sdk):
        from gispulse_sdk.models import JobCreate
        sdk.jobs.create(JobCreate(name="j1", parameters={}))
        sdk.jobs.create(JobCreate(name="j2", parameters={}))
        jobs = sdk.jobs.list()
        assert len(jobs) >= 2

    def test_cancel_pending_job(self, sdk):
        from gispulse_sdk.models import JobCreate
        job = sdk.jobs.create(JobCreate(name="to_cancel", parameters={}))
        # Only cancel if still pending (might have completed immediately)
        fetched = sdk.jobs.get(job.id)
        if fetched.status == "pending":
            result = sdk.jobs.cancel(job.id)
            assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class TestSDKRules:
    def test_create_rule(self, sdk):
        from gispulse_sdk.models import RuleCreate
        rule = sdk.rules.create(RuleCreate(
            name="sdk_test_rule",
            capability="buffer",
            config={"distance": 10},
        ))
        assert rule.id is not None
        assert rule.name == "sdk_test_rule"
        assert rule.capability == "buffer"

    def test_list_rules_after_create(self, sdk):
        from gispulse_sdk.models import RuleCreate
        sdk.rules.create(RuleCreate(name="r1", capability="buffer", config={}))
        sdk.rules.create(RuleCreate(name="r2", capability="buffer", config={}))
        rules = sdk.rules.list()
        assert len(rules) >= 2

    def test_get_rule_by_id(self, sdk):
        from gispulse_sdk.models import RuleCreate
        created = sdk.rules.create(RuleCreate(name="get_me", capability="buffer", config={}))
        fetched = sdk.rules.get(created.id)
        assert fetched.id == created.id
        assert fetched.name == "get_me"

    def test_delete_rule(self, sdk):
        from gispulse_sdk.models import RuleCreate
        rule = sdk.rules.create(RuleCreate(name="delete_me", capability="buffer", config={}))
        sdk.rules.delete(rule.id)
        # After deletion, list should not contain the rule
        remaining_ids = [r.id for r in sdk.rules.list()]
        assert rule.id not in remaining_ids

    def test_rule_lifecycle_create_get_delete(self, sdk):
        """Full lifecycle: create → get → verify exists → delete → verify gone."""
        from gispulse_sdk.models import RuleCreate
        rule = sdk.rules.create(RuleCreate(
            name="lifecycle_rule", capability="filter", config={"predicate": "area > 100"},
        ))
        assert rule.id is not None

        fetched = sdk.rules.get(rule.id)
        assert fetched.name == "lifecycle_rule"

        sdk.rules.delete(rule.id)
        ids_after = [r.id for r in sdk.rules.list()]
        assert rule.id not in ids_after
