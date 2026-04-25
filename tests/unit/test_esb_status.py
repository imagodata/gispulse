"""
Tests for the GET /esb/status endpoint (issue #259).

Covers:
- Endpoint exists and returns 200
- Response JSON has expected keys
- Values have correct types
- Circuit breaker state appears when registered
- Endpoint protected by API key (when keys configured)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.delenv("GISPULSE_API_KEYS", raising=False)
    app = create_app()
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def auth_client(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.setenv("GISPULSE_API_KEYS", "test-key")
    app = create_app()
    return TestClient(app, raise_server_exceptions=True)


class TestEsbStatusStructure:
    def test_returns_200(self, client):
        resp = client.get("/esb/status")
        assert resp.status_code == 200

    def test_response_is_json(self, client):
        resp = client.get("/esb/status")
        data = resp.json()
        assert isinstance(data, dict)

    def test_has_circuit_breakers_key(self, client):
        resp = client.get("/esb/status")
        assert "circuit_breakers" in resp.json()

    def test_has_dlq_size_key(self, client):
        resp = client.get("/esb/status")
        assert "dlq_size" in resp.json()

    def test_has_pg_notify_connected_key(self, client):
        resp = client.get("/esb/status")
        assert "pg_notify_connected" in resp.json()

    def test_has_workers_key(self, client):
        resp = client.get("/esb/status")
        assert "workers" in resp.json()

    def test_circuit_breakers_is_dict(self, client):
        data = client.get("/esb/status").json()
        assert isinstance(data["circuit_breakers"], dict)

    def test_dlq_size_is_int(self, client):
        data = client.get("/esb/status").json()
        assert isinstance(data["dlq_size"], int)

    def test_pg_notify_connected_is_bool(self, client):
        data = client.get("/esb/status").json()
        assert isinstance(data["pg_notify_connected"], bool)

    def test_circuit_breaker_state_after_registration(self, client):
        """If a circuit breaker is registered, its state appears in /esb/status."""
        from gispulse.adapters.esb.circuit_breaker import get_circuit_breaker, _circuit_breakers
        _circuit_breakers.clear()
        get_circuit_breaker("test_service")
        data = client.get("/esb/status").json()
        assert "test_service" in data["circuit_breakers"]
        _circuit_breakers.clear()


class TestEsbStatusAuth:
    def test_returns_401_without_key(self, auth_client):
        resp = auth_client.get("/esb/status")
        assert resp.status_code == 401

    def test_returns_200_with_valid_key(self, auth_client):
        resp = auth_client.get(
            "/esb/status", headers={"X-API-Key": "test-key"}
        )
        assert resp.status_code == 200
