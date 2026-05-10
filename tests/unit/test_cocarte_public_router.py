"""Integration tests for the Cocarte public viewer router (issue #59)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from gispulse.adapters.http.rate_limit import limiter
from persistence.map_io import MapRepository


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """Per-test SQLite repo bound to tmp_path (settings cache workaround)."""
    monkeypatch.setenv("GISPULSE_STORAGE", "sqlite")
    limiter.enabled = False
    app = create_app()
    app.state.map_repo = MapRepository(db_path=tmp_path / "viewer.db")
    return TestClient(app)


def _create_map(client: TestClient, *, title: str, visibility: str) -> dict:
    """Create then patch visibility (POST defaults to private)."""
    created = client.post("/maps", json={"title": title}).json()
    if visibility != "private":
        client.patch(f"/maps/{created['id']}", json={"visibility": visibility})
        return client.get(f"/maps/{created['id']}").json()
    return created


class TestPublicSlug:
    def test_public_visible(self, client: TestClient) -> None:
        m = _create_map(client, title="Open data", visibility="public")
        r = client.get(f"/c/{m['slug']}")
        assert r.status_code == 200
        body = r.json()
        assert body["slug"] == m["slug"]
        assert body["title"] == "Open data"
        assert body["visibility"] == "public"
        # Sanitised: no owner_id, no share_token
        assert "owner_id" not in body
        assert "share_token" not in body

    def test_private_returns_404(self, client: TestClient) -> None:
        m = _create_map(client, title="Secret", visibility="private")
        r = client.get(f"/c/{m['slug']}")
        assert r.status_code == 404

    def test_unlisted_returns_404_via_slug(self, client: TestClient) -> None:
        m = _create_map(client, title="Hidden", visibility="unlisted")
        r = client.get(f"/c/{m['slug']}")
        assert r.status_code == 404, "unlisted maps must not be reachable by slug"

    def test_unknown_slug_returns_404(self, client: TestClient) -> None:
        r = client.get("/c/does-not-exist")
        assert r.status_code == 404

    def test_trashed_public_returns_404(self, client: TestClient) -> None:
        m = _create_map(client, title="Soon trashed", visibility="public")
        client.delete(f"/maps/{m['id']}")
        r = client.get(f"/c/{m['slug']}")
        assert r.status_code == 404


class TestUnlistedByToken:
    def test_valid_token_returns_200(self, client: TestClient) -> None:
        m = _create_map(client, title="Linked", visibility="unlisted")
        # Fetch the share_token from the owner-side dashboard endpoint.
        owner_view = client.get(f"/maps/{m['id']}").json()
        token = owner_view["share_token"]
        assert token is not None and len(token) >= 40

        r = client.get(f"/c/by-token/{token}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == m["id"]
        assert body["visibility"] == "unlisted"
        assert "share_token" not in body
        assert "owner_id" not in body

    def test_invalid_token_returns_404(self, client: TestClient) -> None:
        r = client.get("/c/by-token/totally-bogus-token-zzzzzzzzz")
        assert r.status_code == 404

    def test_empty_token_returns_404(self, client: TestClient) -> None:
        # FastAPI returns 404 for missing path param, but we also guard for
        # whitespace-only / extremely short tokens at the handler level.
        r = client.get("/c/by-token/x")
        assert r.status_code == 404

    def test_oversized_token_returns_404(self, client: TestClient) -> None:
        r = client.get("/c/by-token/" + "A" * 500)
        assert r.status_code == 404

    def test_private_map_token_lookup_fails(self, client: TestClient) -> None:
        """A private map (no token) cannot be reached via by-token even if
        the caller guesses an empty/random string."""
        _create_map(client, title="Private no token", visibility="private")
        r = client.get("/c/by-token/anything-here-zzzzzzzzzzz")
        assert r.status_code == 404

    def test_token_after_visibility_revert_to_private(
        self, client: TestClient
    ) -> None:
        """Patching back to private clears the token; the old token now 404s."""
        m = _create_map(client, title="Brief unlisted", visibility="unlisted")
        token = client.get(f"/maps/{m['id']}").json()["share_token"]
        # Revert to private — token cleared
        client.patch(f"/maps/{m['id']}", json={"visibility": "private"})
        r = client.get(f"/c/by-token/{token}")
        assert r.status_code == 404


class TestHealth:
    def test_health_returns_200(self, client: TestClient) -> None:
        r = client.get("/c/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
