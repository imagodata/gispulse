"""
Unit tests for the unified error response standardization (#158).

Verifies that all FastAPI applications (engine + portal) return errors in
the canonical envelope:

    {"error": {"code": str, "message": str, "detail": any}}

Covers: 400, 404, 422, 500.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gispulse.adapters.http.error_handlers import register_error_handlers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_app() -> FastAPI:
    """Create a minimal FastAPI app with error handlers registered."""
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/ok")
    def ok():
        return {"status": "ok"}

    @app.get("/boom")
    def boom():
        raise RuntimeError("simulated crash")

    @app.get("/items/{item_id}")
    def get_item(item_id: int):
        if item_id == 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Item not found")
        return {"id": item_id}

    @app.post("/validate")
    def validate_body(body: dict):
        return body

    return app


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Shared test client for the minimal test app."""
    return TestClient(_make_test_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helper assertions
# ---------------------------------------------------------------------------

def assert_error_envelope(data: dict) -> dict:
    """Assert the response has the standard error envelope and return the inner error dict."""
    assert "error" in data, f"Expected 'error' key in response, got: {data}"
    err = data["error"]
    assert "code" in err, f"Missing 'code' in error: {err}"
    assert "message" in err, f"Missing 'message' in error: {err}"
    assert "detail" in err, f"Missing 'detail' in error: {err}"
    return err


# ---------------------------------------------------------------------------
# Tests — engine app (create_app)
# ---------------------------------------------------------------------------

class TestEngineAppErrorHandlers:
    """Test error handlers on the full engine FastAPI app."""

    @pytest.fixture(autouse=True)
    def _memory_storage(self, monkeypatch):
        monkeypatch.setenv("GISPULSE_STORAGE", "memory")

    @pytest.fixture()
    def engine_client(self) -> TestClient:
        os.environ["GISPULSE_STORAGE"] = "memory"
        from gispulse.adapters.http.app import create_app
        app = create_app()
        return TestClient(app, raise_server_exceptions=False)

    def test_404_route_not_found(self, engine_client: TestClient) -> None:
        """A request to an unknown path must return a structured 404."""
        resp = engine_client.get("/does-not-exist-xyz")
        assert resp.status_code == 404
        err = assert_error_envelope(resp.json())
        assert err["code"] == "NOT_FOUND"
        assert isinstance(err["message"], str)

    def test_422_validation_error(self, engine_client: TestClient) -> None:
        """Posting a malformed body to a typed endpoint should return 422."""
        resp = engine_client.post(
            "/rules",
            json={"this_field_is_completely_wrong": True},
        )
        assert resp.status_code == 422
        err = assert_error_envelope(resp.json())
        assert err["code"] == "VALIDATION_ERROR"
        assert isinstance(err["detail"], list)


# ---------------------------------------------------------------------------
# Tests — minimal app (isolated handlers)
# ---------------------------------------------------------------------------

class TestErrorEnvelopeFormat:
    """Verify the exact JSON structure produced by each handler."""

    def test_health_endpoint_unaffected(self, client: TestClient) -> None:
        """Normal successful responses must NOT be wrapped in an error envelope."""
        resp = client.get("/ok")
        assert resp.status_code == 200
        assert "error" not in resp.json()

    def test_404_http_exception(self, client: TestClient) -> None:
        resp = client.get("/items/0")
        assert resp.status_code == 404
        err = assert_error_envelope(resp.json())
        assert err["code"] == "NOT_FOUND"
        assert "not found" in err["message"].lower()

    def test_500_unhandled_exception(self, client: TestClient) -> None:
        """Unhandled exceptions must be caught and wrapped as 500."""
        resp = client.get("/boom")
        assert resp.status_code == 500
        err = assert_error_envelope(resp.json())
        assert err["code"] == "INTERNAL_SERVER_ERROR"
        assert isinstance(err["message"], str)

    def test_error_envelope_keys_always_present(self, client: TestClient) -> None:
        """All three keys (code, message, detail) must always be present."""
        resp = client.get("/items/0")
        err = resp.json()["error"]
        for key in ("code", "message", "detail"):
            assert key in err, f"Key '{key}' missing from error envelope"


# ---------------------------------------------------------------------------
# Tests — portal app
# ---------------------------------------------------------------------------

class TestPortalAppErrorHandlers:
    """Smoke-test error handlers on the portal FastAPI app."""

    @pytest.fixture(autouse=True)
    def _memory_storage(self, monkeypatch):
        monkeypatch.setenv("GISPULSE_STORAGE", "memory")

    @pytest.fixture()
    def portal_client(self) -> TestClient:
        os.environ["GISPULSE_STORAGE"] = "memory"
        from gispulse.adapters.http.portal_app import create_portal_app
        app = create_portal_app()
        return TestClient(app, raise_server_exceptions=False)

    def test_404_unknown_api_route(self, portal_client: TestClient) -> None:
        """Requesting an unknown API path on the portal app must yield a structured 404."""
        resp = portal_client.get("/api/does-not-exist-xyz")
        assert resp.status_code == 404
        err = assert_error_envelope(resp.json())
        assert err["code"] == "NOT_FOUND"
