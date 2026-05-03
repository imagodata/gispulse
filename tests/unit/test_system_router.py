"""Tests for /system/doctor — closes P0-4 of the parity audit (issue #91).

Validates:
1. The pure ``run_checks()`` function returns a stable schema for selected names
2. CLI ``gispulse doctor --json`` and HTTP ``POST /system/doctor`` produce the
   same response shape (parity)
3. The HTTP endpoint rejects unknown check names with 422
4. In portal mode (auth disabled), the endpoint is reachable
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from gispulse.diagnostics import KNOWN_CHECKS, run_checks
from gispulse.diagnostics.system import CheckResult, DoctorResult


# ---------------------------------------------------------------------------
# Pure function
# ---------------------------------------------------------------------------


def test_run_checks_all_returns_doctor_result() -> None:
    result = run_checks()
    assert isinstance(result, DoctorResult)
    assert len(result.checks) == len(KNOWN_CHECKS)
    # Summary covers every status that appears
    for check in result.checks:
        assert check.status in {"ok", "warning", "error", "skipped"}
        assert result.summary[check.status] >= 1


def test_run_checks_subset_runs_only_requested() -> None:
    result = run_checks(["python", "disk"])
    names = [c.name for c in result.checks]
    assert names == ["python", "disk"]


def test_run_checks_unknown_name_silently_skipped() -> None:
    result = run_checks(["python", "does-not-exist", "disk"])
    names = [c.name for c in result.checks]
    assert names == ["python", "disk"]


def test_run_checks_python_always_ok_on_supported_runtime() -> None:
    result = run_checks(["python"])
    py = result.checks[0]
    assert py.name == "python"
    if sys.version_info[:2] >= (3, 10):
        assert py.status == "ok"
    else:
        assert py.status == "error"


def test_check_result_to_dict_is_jsonable() -> None:
    r = CheckResult("foo", "ok", "bar")
    json.dumps(r.to_dict())  # must not raise


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def portal_client(monkeypatch) -> TestClient:
    """Portal mode = auth disabled, endpoint reachable without admin role."""
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    from gispulse.adapters.http.rate_limit import limiter
    limiter.enabled = False
    app = create_app(mode="portal")
    return TestClient(app)


def test_post_doctor_default_runs_all(portal_client: TestClient) -> None:
    resp = portal_client.post("/system/doctor")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"summary", "checks", "ran_at", "has_critical"}
    assert len(body["checks"]) == len(KNOWN_CHECKS)


def test_post_doctor_subset(portal_client: TestClient) -> None:
    resp = portal_client.post("/system/doctor", json={"checks": ["python", "disk"]})
    assert resp.status_code == 200
    body = resp.json()
    names = [c["name"] for c in body["checks"]]
    assert names == ["python", "disk"]


def test_post_doctor_rejects_unknown_check_with_422(portal_client: TestClient) -> None:
    resp = portal_client.post("/system/doctor", json={"checks": ["not-a-real-check"]})
    assert resp.status_code == 422
    body = resp.json()
    # The app's error handler wraps HTTPException as {"error": {"message": ...}}
    message = body.get("error", {}).get("message") or body.get("detail", "")
    assert "not-a-real-check" in message


def test_post_doctor_response_shape_matches_cli_json() -> None:
    """CLI ``gispulse doctor --json`` and HTTP /system/doctor must agree on schema.

    We compare the *keys* of the response, not the values, since the runtime
    snapshot can change between calls (timestamps, versions).
    """
    cli_proc = subprocess.run(
        [sys.executable, "-m", "gispulse.cli", "doctor", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cli_proc.returncode == 0, cli_proc.stderr
    cli_body = json.loads(cli_proc.stdout)

    http_result = run_checks()  # same code path as the endpoint
    http_body = http_result.to_dict()

    # Top-level shape parity
    assert set(cli_body.keys()) == set(http_body.keys())
    # Per-check shape parity
    cli_checks = {c["name"]: set(c.keys()) for c in cli_body["checks"]}
    http_checks = {c["name"]: set(c.keys()) for c in http_body["checks"]}
    assert cli_checks.keys() == http_checks.keys()
    for name, keys in cli_checks.items():
        assert keys == http_checks[name], f"keys differ for check {name!r}"
