"""Integration tests for the Cocarte Maps router (issue #56)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app
from gispulse.adapters.http.rate_limit import limiter
from persistence.map_io import MapRepository


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """Build a test client with a fresh per-test SQLite ``maps`` repo.

    ``core.config.settings`` is imported once and caches the db_path
    resolved from env at import time. Setting GISPULSE_DB_PATH via
    monkeypatch is too late, so we override ``app.state.map_repo`` with
    a per-test repo bound to ``tmp_path`` instead.
    """
    monkeypatch.setenv("GISPULSE_STORAGE", "sqlite")
    limiter.enabled = False
    app = create_app()
    app.state.map_repo = MapRepository(db_path=tmp_path / "maps.db")
    return TestClient(app)


CREATE_PAYLOAD = {
    "title": "Élections 2026 — résultats par commune",
    "description": "Carte test",
}


class TestCreateMap:
    def test_create_returns_201(self, client: TestClient) -> None:
        r = client.post("/maps", json=CREATE_PAYLOAD)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["title"] == CREATE_PAYLOAD["title"]
        assert body["slug"] == "elections-2026-resultats-par-commune"
        assert body["visibility"] == "private"
        assert body["share_token"] is None
        assert body["deleted_at"] is None

    def test_create_minimal(self, client: TestClient) -> None:
        r = client.post("/maps", json={"title": "Hello"})
        assert r.status_code == 201
        assert r.json()["slug"] == "hello"

    def test_create_empty_title_rejected(self, client: TestClient) -> None:
        r = client.post("/maps", json={"title": ""})
        assert r.status_code == 422

    def test_slug_uniqueness(self, client: TestClient) -> None:
        r1 = client.post("/maps", json={"title": "Same name"})
        r2 = client.post("/maps", json={"title": "Same name"})
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["slug"] != r2.json()["slug"]


class TestListMaps:
    def test_empty_list(self, client: TestClient) -> None:
        r = client.get("/maps")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_after_create(self, client: TestClient) -> None:
        client.post("/maps", json={"title": "M1"})
        client.post("/maps", json={"title": "M2"})
        r = client.get("/maps")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_pagination(self, client: TestClient) -> None:
        for i in range(3):
            client.post("/maps", json={"title": f"M{i}"})
        r = client.get("/maps?limit=2&offset=0")
        assert r.json()["total"] == 3
        assert len(r.json()["items"]) == 2


class TestGetMap:
    def test_get_after_create(self, client: TestClient) -> None:
        created = client.post("/maps", json={"title": "Get me"}).json()
        r = client.get(f"/maps/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_get_missing_returns_404(self, client: TestClient) -> None:
        r = client.get("/maps/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404


class TestUpdateMap:
    def test_patch_description(self, client: TestClient) -> None:
        created = client.post("/maps", json={"title": "Patch me"}).json()
        r = client.patch(f"/maps/{created['id']}", json={"description": "new"})
        assert r.status_code == 200
        assert r.json()["description"] == "new"

    def test_patch_visibility_to_unlisted_generates_token(self, client: TestClient) -> None:
        created = client.post("/maps", json={"title": "Unlist me"}).json()
        r = client.patch(f"/maps/{created['id']}", json={"visibility": "unlisted"})
        assert r.status_code == 200
        body = r.json()
        assert body["visibility"] == "unlisted"
        assert body["share_token"] is not None
        assert len(body["share_token"]) >= 40  # token_urlsafe(32)

    def test_patch_visibility_back_to_private_clears_token(self, client: TestClient) -> None:
        created = client.post("/maps", json={"title": "Privatize"}).json()
        client.patch(f"/maps/{created['id']}", json={"visibility": "unlisted"})
        r = client.patch(f"/maps/{created['id']}", json={"visibility": "private"})
        assert r.json()["share_token"] is None

    def test_patch_view_state(self, client: TestClient) -> None:
        created = client.post("/maps", json={"title": "view"}).json()
        r = client.patch(
            f"/maps/{created['id']}",
            json={"view_state": {"center": [2.35, 48.85], "zoom": 10}},
        )
        assert r.json()["view_state"]["zoom"] == 10


class TestDeleteAndRestore:
    def test_delete_soft_deletes(self, client: TestClient) -> None:
        created = client.post("/maps", json={"title": "Trash me"}).json()
        r = client.delete(f"/maps/{created['id']}")
        assert r.status_code == 204
        # Default GET excludes trashed
        assert client.get(f"/maps/{created['id']}").status_code == 404
        # include_trashed reveals it
        r = client.get(f"/maps/{created['id']}?include_trashed=true")
        assert r.status_code == 200
        assert r.json()["deleted_at"] is not None

    def test_restore_undeletes(self, client: TestClient) -> None:
        created = client.post("/maps", json={"title": "Restore me"}).json()
        client.delete(f"/maps/{created['id']}")
        r = client.post(f"/maps/{created['id']}/restore")
        assert r.status_code == 200
        assert r.json()["deleted_at"] is None
        # Now visible again
        assert client.get(f"/maps/{created['id']}").status_code == 200

    def test_restore_active_returns_409(self, client: TestClient) -> None:
        created = client.post("/maps", json={"title": "Active"}).json()
        r = client.post(f"/maps/{created['id']}/restore")
        assert r.status_code == 409

    def test_list_excludes_trashed_by_default(self, client: TestClient) -> None:
        a = client.post("/maps", json={"title": "Keep"}).json()  # noqa: F841
        b = client.post("/maps", json={"title": "Bin"}).json()
        client.delete(f"/maps/{b['id']}")
        active = client.get("/maps").json()
        full = client.get("/maps?include_trashed=true").json()
        assert active["total"] == 1
        assert full["total"] == 2


class TestRotateShareToken:
    def test_rotate_unlisted_changes_token(self, client: TestClient) -> None:
        created = client.post("/maps", json={"title": "Rotate"}).json()
        client.patch(f"/maps/{created['id']}", json={"visibility": "unlisted"})
        before = client.get(f"/maps/{created['id']}").json()["share_token"]
        r = client.post(f"/maps/{created['id']}/rotate-token")
        assert r.status_code == 200
        assert r.json()["share_token"] != before

    def test_rotate_private_returns_409(self, client: TestClient) -> None:
        created = client.post("/maps", json={"title": "Private"}).json()
        r = client.post(f"/maps/{created['id']}/rotate-token")
        assert r.status_code == 409


class TestTierGating:
    def test_limit_blocks_create_when_count_reached(self, client: TestClient, monkeypatch) -> None:
        # Force a tight limit regardless of tier resolution: makes the test
        # robust to env / cached settings differences.
        from gispulse.adapters.http.routers import maps_router

        monkeypatch.setitem(maps_router._MAP_LIMITS, "community", 3)
        monkeypatch.setitem(maps_router._MAP_LIMITS, "pro", 3)
        monkeypatch.setitem(maps_router._MAP_LIMITS, "team", 3)
        monkeypatch.setitem(maps_router._MAP_LIMITS, "enterprise", 3)

        for i in range(3):
            r = client.post("/maps", json={"title": f"M{i}"})
            assert r.status_code == 201, f"#{i} failed: {r.text}"
        r = client.post("/maps", json={"title": "Fourth"})
        assert r.status_code == 402
        body = r.json()
        msg = body.get("detail") or body.get("error", {}).get("message", "")
        assert "limit" in msg.lower()
