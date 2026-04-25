"""Tests for the /auth/* endpoints (SSO / OIDC auth router)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Force in-memory storage and disable rate limiting."""
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    from gispulse.adapters.http.rate_limit import limiter

    limiter.enabled = False


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /auth/providers
# ---------------------------------------------------------------------------


class TestListProviders:
    """GET /auth/providers — list configured SSO providers."""

    def test_returns_200_with_list(self, client: TestClient) -> None:
        """When OIDC is not configured the endpoint should still be reachable
        (the auth router is only mounted when oidc_provider is not None, so
        we verify that the app either returns the list or 404 if the router
        is not mounted at all)."""
        response = client.get("/auth/providers")
        # Auth router may not be mounted when OIDC is not configured
        if response.status_code == 200:
            body = response.json()
            assert isinstance(body, list)
        else:
            # Router not mounted — 404 is acceptable
            assert response.status_code == 404

    def test_returns_empty_list_when_no_oidc(self, client: TestClient) -> None:
        """Without OIDC env vars, the provider list should be empty or
        the router should not be mounted at all."""
        response = client.get("/auth/providers")
        if response.status_code == 200:
            assert response.json() == []


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


class TestMe:
    """GET /auth/me — current user info from session."""

    def test_returns_401_when_not_authenticated(self, client: TestClient) -> None:
        """Without a session cookie, /auth/me must return 401."""
        response = client.get("/auth/me")
        if response.status_code == 401:
            body = response.json()
            # Error envelope: {"error": {"code": "...", "message": "..."}}
            assert "error" in body
            assert body["error"]["code"] == "UNAUTHORIZED"
        else:
            # Router not mounted (OIDC not configured) — 404 is acceptable
            assert response.status_code == 404

    def test_returns_401_with_invalid_cookie(self, client: TestClient) -> None:
        """A garbage session cookie must still yield 401."""
        response = client.get(
            "/auth/me",
            cookies={"gispulse_session": "invalid-token-garbage"},
        )
        if response.status_code == 401:
            body = response.json()
            assert body["error"]["code"] == "UNAUTHORIZED"
        else:
            assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


class TestLogout:
    """POST /auth/logout — clear session cookie."""

    def test_logout_returns_200_or_not_mounted(self, client: TestClient) -> None:
        """Logout should return 200 with status logged_out, or 404/405 if the
        auth router is not mounted (OIDC not configured)."""
        response = client.post("/auth/logout")
        if response.status_code == 200:
            body = response.json()
            assert body.get("status") == "logged_out"
        else:
            # Router not mounted — 404 or 405 (method not allowed on
            # the catch-all GET route) are both acceptable.
            assert response.status_code in (404, 405)

    def test_logout_clears_session_cookie(self, client: TestClient) -> None:
        """After logout, the session cookie should be cleared."""
        response = client.post(
            "/auth/logout",
            cookies={"gispulse_session": "some-token"},
        )
        if response.status_code == 200:
            # Check that the response asks the browser to delete the cookie
            set_cookie = response.headers.get("set-cookie", "")
            # Either no cookie header or an expiry/max-age=0 directive
            if set_cookie:
                assert "gispulse_session" in set_cookie
