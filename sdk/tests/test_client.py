"""Tests for the GISPulseClient core."""

from __future__ import annotations

import pytest

from gispulse_sdk import GISPulseClient, AuthError, NotFoundError, ServerError
from gispulse_sdk.models import HealthResponse


BASE_URL = "https://gispulse.test"


class TestHealth:
    def test_health_ok(self, client, mock_api):
        mock_api.get("/health").respond(200, json={"status": "ok", "version": "0.1.0"})
        h = client.health()
        assert isinstance(h, HealthResponse)
        assert h.status == "ok"
        assert h.version == "0.1.0"

    def test_health_server_error(self, client, mock_api):
        mock_api.get("/health").respond(500, json={"detail": "Internal error"})
        with pytest.raises(ServerError):
            client.health()


class TestAuth:
    def test_api_key_header_sent(self, mock_api):
        route = mock_api.get("/health").respond(200, json={"status": "ok", "version": "0.1.0"})
        c = GISPulseClient(BASE_URL, api_key="my-key")
        c.health()
        c.close()
        assert route.calls[0].request.headers["X-API-Key"] == "my-key"

    def test_auth_error_raised(self, client, mock_api):
        mock_api.get("/capabilities").respond(401, json={"detail": "Invalid or missing API key"})
        with pytest.raises(AuthError):
            client.capabilities()

    def test_not_found_raised(self, client, mock_api):
        mock_api.get("/rules/nonexistent").respond(404, json={"detail": "Not found"})
        with pytest.raises(NotFoundError):
            client.rules.get("nonexistent")


class TestContextManager:
    def test_client_context_manager(self, mock_api):
        mock_api.get("/health").respond(200, json={"status": "ok", "version": "0.1.0"})
        with GISPulseClient(BASE_URL, api_key="k") as c:
            h = c.health()
            assert h.status == "ok"


class TestRepr:
    def test_repr(self):
        c = GISPulseClient("https://example.com")
        assert "example.com" in repr(c)
        c.close()
