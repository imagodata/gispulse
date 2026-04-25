"""
Tests for the /metrics HTTP endpoint (issue #256).

Covers:
- Endpoint returns 200 with correct Content-Type
- Prometheus text format validated
- GISPULSE_METRICS_TOKEN protection when set
- Endpoint accessible without token when env var not set
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from core.observability import MetricsCollector


@pytest.fixture(autouse=True)
def reset_metrics():
    MetricsCollector.get().reset()
    yield
    MetricsCollector.get().reset()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GISPULSE_METRICS_TOKEN", raising=False)
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    app = create_app()
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def client_with_token(monkeypatch):
    monkeypatch.setenv("GISPULSE_METRICS_TOKEN", "test-secret-token")
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    app = create_app()
    return TestClient(app, raise_server_exceptions=True)


class TestMetricsEndpointNoToken:
    def test_returns_200(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_content_type_prometheus(self, client):
        resp = client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"]
        assert "0.0.4" in resp.headers["content-type"]

    def test_empty_metrics_valid(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        # Empty or just a newline is valid
        assert isinstance(resp.text, str)

    def test_counter_appears_in_output(self, client):
        MetricsCollector.get().inc("test_requests_total", 5)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "test_requests_total" in resp.text
        assert "5" in resp.text

    def test_gauge_appears_in_output(self, client):
        MetricsCollector.get().gauge("active_sessions", 3.0)
        resp = client.get("/metrics")
        assert "active_sessions" in resp.text
        assert "3.0" in resp.text

    def test_prometheus_type_line_present(self, client):
        MetricsCollector.get().inc("jobs_total", 1)
        resp = client.get("/metrics")
        assert "# TYPE jobs_total counter" in resp.text

    def test_prometheus_help_line_present(self, client):
        MetricsCollector.get().inc("jobs_total", 1)
        resp = client.get("/metrics")
        assert "# HELP jobs_total" in resp.text


class TestMetricsEndpointWithToken:
    def test_returns_401_without_token(self, client_with_token):
        resp = client_with_token.get("/metrics")
        assert resp.status_code == 401

    def test_returns_401_wrong_token(self, client_with_token):
        resp = client_with_token.get(
            "/metrics", headers={"Authorization": "Bearer wrong-token"}
        )
        assert resp.status_code == 401

    def test_returns_200_with_correct_token(self, client_with_token):
        resp = client_with_token.get(
            "/metrics", headers={"Authorization": "Bearer test-secret-token"}
        )
        assert resp.status_code == 200

    def test_content_type_prometheus_with_token(self, client_with_token):
        resp = client_with_token.get(
            "/metrics", headers={"Authorization": "Bearer test-secret-token"}
        )
        assert "text/plain" in resp.headers["content-type"]
        assert "0.0.4" in resp.headers["content-type"]

    def test_bearer_prefix_required(self, client_with_token):
        """Raw token without 'Bearer ' prefix must be rejected."""
        resp = client_with_token.get(
            "/metrics", headers={"Authorization": "test-secret-token"}
        )
        assert resp.status_code == 401
