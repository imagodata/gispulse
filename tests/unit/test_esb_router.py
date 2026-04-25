"""
Tests for the ESB router (adapters/http/routers/esb_router.py).

Covers:
- GET /esb/status — returns ESB operational status
- Response structure and types
- Auth protection when API keys are configured
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.delenv("GISPULSE_API_KEYS", raising=False)
    from gispulse.adapters.http.rate_limit import limiter
    limiter.enabled = False


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


@pytest.fixture()
def auth_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.setenv("GISPULSE_API_KEYS", "test-key-esb")
    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /esb/status — structure
# ---------------------------------------------------------------------------


class TestEsbStatusResponse:
    def test_returns_200(self, client):
        resp = client.get("/esb/status")
        assert resp.status_code == 200

    def test_response_is_dict(self, client):
        data = client.get("/esb/status").json()
        assert isinstance(data, dict)

    def test_has_workers_key(self, client):
        data = client.get("/esb/status").json()
        assert "workers" in data

    def test_has_circuit_breakers_key(self, client):
        data = client.get("/esb/status").json()
        assert "circuit_breakers" in data

    def test_has_dlq_size_key(self, client):
        data = client.get("/esb/status").json()
        assert "dlq_size" in data

    def test_has_pg_notify_connected_key(self, client):
        data = client.get("/esb/status").json()
        assert "pg_notify_connected" in data


# ---------------------------------------------------------------------------
# GET /esb/status — types
# ---------------------------------------------------------------------------


class TestEsbStatusTypes:
    def test_circuit_breakers_is_dict(self, client):
        data = client.get("/esb/status").json()
        assert isinstance(data["circuit_breakers"], dict)

    def test_dlq_size_is_int(self, client):
        data = client.get("/esb/status").json()
        assert isinstance(data["dlq_size"], int)

    def test_pg_notify_connected_is_bool(self, client):
        data = client.get("/esb/status").json()
        assert isinstance(data["pg_notify_connected"], bool)

    def test_workers_is_none_when_no_pool(self, client):
        """Without a running WorkerPool, workers should be None."""
        data = client.get("/esb/status").json()
        assert data["workers"] is None


# ---------------------------------------------------------------------------
# GET /esb/status — default values
# ---------------------------------------------------------------------------


class TestEsbStatusDefaults:
    def test_dlq_size_defaults_to_zero(self, client):
        data = client.get("/esb/status").json()
        assert data["dlq_size"] == 0

    def test_pg_notify_defaults_to_false(self, client):
        data = client.get("/esb/status").json()
        assert data["pg_notify_connected"] is False

    def test_circuit_breakers_empty_by_default(self, client):
        from gispulse.adapters.esb.circuit_breaker import _circuit_breakers
        _circuit_breakers.clear()
        data = client.get("/esb/status").json()
        assert data["circuit_breakers"] == {}


# ---------------------------------------------------------------------------
# GET /esb/status — auth
# ---------------------------------------------------------------------------


class TestEsbStatusAuth:
    def test_returns_401_without_key(self, auth_client):
        resp = auth_client.get("/esb/status")
        assert resp.status_code == 401

    def test_401_uses_error_envelope(self, auth_client):
        body = auth_client.get("/esb/status").json()
        assert "error" in body
        assert body["error"]["code"] == "UNAUTHORIZED"

    def test_returns_200_with_valid_key(self, auth_client):
        resp = auth_client.get(
            "/esb/status", headers={"X-API-Key": "test-key-esb"}
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Non-existent ESB endpoints
# ---------------------------------------------------------------------------


class TestEsbNotFound:
    def test_unknown_esb_path_returns_404(self, client):
        resp = client.get("/esb/nonexistent")
        assert resp.status_code == 404
