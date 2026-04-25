"""
Tests for the `gispulse jobs` CLI sub-group (issue #260).

Uses httpx.MockTransport to avoid real network calls.
Tests: list (empty + populated), status (found + not found), cancel (ok + 409).
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from typer.testing import CliRunner

from gispulse.cli import app

runner = CliRunner()

_JOB_ID = str(uuid4())
_SAMPLE_JOB = {
    "id": _JOB_ID,
    "name": "test_job",
    "status": "pending",
    "dataset_id": None,
    "parameters": {},
    "created_at": "2026-04-04T12:00:00",
    "started_at": None,
    "completed_at": None,
    "result_path": None,
    "error_message": None,
    "duration_seconds": None,
    "attempts": 0,
}


def _mock_transport(routes: dict[str, tuple[int, object]]) -> httpx.MockTransport:
    """Build an httpx.MockTransport responding to GET/POST paths."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        if key in routes:
            status, body = routes[key]
            content = json.dumps(body).encode()
            return httpx.Response(status, content=content, headers={"content-type": "application/json"})
        return httpx.Response(404, content=b'{"detail": "not found"}')

    return httpx.MockTransport(handler)


def _patch_client(monkeypatch, transport: httpx.MockTransport) -> None:
    """Patch httpx.Client so it uses the given mock transport."""
    _real = httpx.Client

    def fake_client(**kw):
        kw.pop("transport", None)
        return _real(transport=transport, **kw)

    monkeypatch.setattr(httpx, "Client", fake_client)


# ---------------------------------------------------------------------------
# jobs list
# ---------------------------------------------------------------------------


class TestJobsList:
    def test_list_empty(self, monkeypatch):
        transport = _mock_transport({
            "GET /jobs": (200, {"items": [], "total": 0, "limit": 50, "offset": 0}),
        })
        _patch_client(monkeypatch, transport)
        result = runner.invoke(app, ["jobs", "list", "--host", "http://fake:8001"])
        assert result.exit_code == 0
        assert "No jobs found" in result.output

    def test_list_shows_job(self, monkeypatch):
        transport = _mock_transport({
            "GET /jobs": (200, {"items": [_SAMPLE_JOB], "total": 1, "limit": 50, "offset": 0}),
        })
        _patch_client(monkeypatch, transport)
        result = runner.invoke(app, ["jobs", "list", "--host", "http://fake:8001"])
        assert result.exit_code == 0
        assert _JOB_ID in result.output
        assert "pending" in result.output

    def test_list_shows_attempts_column(self, monkeypatch):
        transport = _mock_transport({
            "GET /jobs": (200, {"items": [_SAMPLE_JOB], "total": 1}),
        })
        _patch_client(monkeypatch, transport)
        result = runner.invoke(app, ["jobs", "list", "--host", "http://fake:8001"])
        assert "ATTEMPTS" in result.output


# ---------------------------------------------------------------------------
# jobs status
# ---------------------------------------------------------------------------


class TestJobsStatus:
    def test_status_found(self, monkeypatch):
        transport = _mock_transport({
            f"GET /jobs/{_JOB_ID}": (200, _SAMPLE_JOB),
        })
        _patch_client(monkeypatch, transport)
        result = runner.invoke(app, ["jobs", "status", _JOB_ID, "--host", "http://fake:8001"])
        assert result.exit_code == 0
        assert _JOB_ID in result.output
        assert "pending" in result.output
        assert "Attempts" in result.output

    def test_status_not_found(self, monkeypatch):
        bad_id = str(uuid4())
        transport = _mock_transport({})  # all 404
        _patch_client(monkeypatch, transport)
        result = runner.invoke(app, ["jobs", "status", bad_id, "--host", "http://fake:8001"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# jobs cancel
# ---------------------------------------------------------------------------


class TestJobsCancel:
    def test_cancel_success(self, monkeypatch):
        cancelled = {**_SAMPLE_JOB, "status": "failed", "error_message": "Cancelled by user"}
        transport = _mock_transport({
            f"POST /jobs/{_JOB_ID}/cancel": (200, cancelled),
        })
        _patch_client(monkeypatch, transport)
        result = runner.invoke(app, ["jobs", "cancel", _JOB_ID, "--host", "http://fake:8001"])
        assert result.exit_code == 0
        assert "cancelled" in result.output.lower()

    def test_cancel_409_already_done(self, monkeypatch):
        transport = _mock_transport({
            f"POST /jobs/{_JOB_ID}/cancel": (
                409, {"detail": f"Job '{_JOB_ID}' is already completed. Cannot cancel."}
            ),
        })
        _patch_client(monkeypatch, transport)
        result = runner.invoke(app, ["jobs", "cancel", _JOB_ID, "--host", "http://fake:8001"])
        assert result.exit_code == 1

    def test_cancel_not_found(self, monkeypatch):
        bad_id = str(uuid4())
        transport = _mock_transport({})  # all 404
        _patch_client(monkeypatch, transport)
        result = runner.invoke(app, ["jobs", "cancel", bad_id, "--host", "http://fake:8001"])
        assert result.exit_code == 1
