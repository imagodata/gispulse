"""Tests for the /maps/* endpoints — saved web maps CRUD (#405)."""

from __future__ import annotations

import os
from uuid import uuid4

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
    from unittest.mock import MagicMock

    app.state.spatial_engine = MagicMock()
    return TestClient(app)


def _payload(name: str = "Ma carte", **overrides) -> dict:
    payload = {
        "name": name,
        "description": "carte de test",
        "layers": [
            {"dataset_id": "abc", "layer": "parcelles", "visible": True, "opacity": 0.8},
        ],
        "styles": {"parcelles": {"renderer": "single", "color": "#ff0000"}},
        "view": {"center": [2.35, 48.85], "zoom": 12},
        "filters": {"parcelles": "surface > 100"},
        "basemap": "osm",
        "metadata": {},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# POST /maps
# ---------------------------------------------------------------------------


class TestCreateMap:
    def test_create_returns_201_with_full_composition(self, client: TestClient) -> None:
        resp = client.post("/maps", json=_payload())
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Ma carte"
        assert "id" in body
        assert body["layers"][0]["opacity"] == 0.8
        assert body["styles"]["parcelles"]["renderer"] == "single"
        assert body["view"]["zoom"] == 12
        assert body["filters"]["parcelles"] == "surface > 100"
        assert body["basemap"] == "osm"
        assert body["created_at"] and body["updated_at"]

    def test_create_with_project_id(self, client: TestClient) -> None:
        pid = str(uuid4())
        resp = client.post("/maps", json=_payload(project_id=pid))
        assert resp.status_code == 201
        assert resp.json()["project_id"] == pid

    def test_create_defaults_empty_composition(self, client: TestClient) -> None:
        resp = client.post("/maps", json={"name": "vide"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["layers"] == []
        assert body["styles"] == {}
        assert body["project_id"] is None

    def test_create_422_on_missing_name(self, client: TestClient) -> None:
        resp = client.post("/maps", json={"view": {}})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_create_422_on_invalid_project_id(self, client: TestClient) -> None:
        resp = client.post("/maps", json=_payload(project_id="not-a-uuid"))
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /maps
# ---------------------------------------------------------------------------


class TestListMaps:
    def test_list_empty(self, client: TestClient) -> None:
        resp = client.get("/maps")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_after_create(self, client: TestClient) -> None:
        client.post("/maps", json=_payload(name="a"))
        client.post("/maps", json=_payload(name="b"))
        body = client.get("/maps").json()
        assert body["total"] == 2
        assert {m["name"] for m in body["items"]} == {"a", "b"}

    def test_list_pagination(self, client: TestClient) -> None:
        for i in range(5):
            client.post("/maps", json=_payload(name=f"m{i}"))
        body = client.get("/maps?limit=2&offset=1").json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 1

    def test_list_filter_by_project(self, client: TestClient) -> None:
        pid = str(uuid4())
        client.post("/maps", json=_payload(name="in", project_id=pid))
        client.post("/maps", json=_payload(name="out", project_id=str(uuid4())))
        body = client.get(f"/maps?project_id={pid}").json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "in"


# ---------------------------------------------------------------------------
# GET /maps/{id}
# ---------------------------------------------------------------------------


class TestGetMap:
    def test_get_existing(self, client: TestClient) -> None:
        created = client.post("/maps", json=_payload()).json()
        resp = client.get(f"/maps/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_404(self, client: TestClient) -> None:
        resp = client.get(f"/maps/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    def test_get_422_invalid_uuid(self, client: TestClient) -> None:
        resp = client.get("/maps/not-a-uuid")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /maps/{id}
# ---------------------------------------------------------------------------


class TestUpdateMap:
    def test_update_replaces_composition(self, client: TestClient) -> None:
        created = client.post("/maps", json=_payload()).json()
        new = _payload(
            name="Renommée",
            view={"center": [0, 0], "zoom": 3},
            layers=[],
        )
        resp = client.put(f"/maps/{created['id']}", json=new)
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Renommée"
        assert body["view"]["zoom"] == 3
        assert body["layers"] == []
        assert body["id"] == created["id"]

    def test_update_404(self, client: TestClient) -> None:
        resp = client.put(f"/maps/{uuid4()}", json=_payload())
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /maps/{id}
# ---------------------------------------------------------------------------


class TestDeleteMap:
    def test_delete_204(self, client: TestClient) -> None:
        created = client.post("/maps", json=_payload()).json()
        resp = client.delete(f"/maps/{created['id']}")
        assert resp.status_code == 204
        assert client.get(f"/maps/{created['id']}").status_code == 404

    def test_delete_404(self, client: TestClient) -> None:
        resp = client.delete(f"/maps/{uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Publication (#406)
# ---------------------------------------------------------------------------


class TestPublishMap:
    def test_publish_returns_token(self, client: TestClient) -> None:
        mid = client.post("/maps", json=_payload()).json()["id"]
        resp = client.post(f"/maps/{mid}/publish")
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"].startswith("pub_")
        assert body["url"] == f"/public/maps/{body['token']}"
        assert body["layer_count"] == 1
        # the map now reports as published
        got = client.get(f"/maps/{mid}").json()
        assert got["is_published"] is True
        assert got["public_token"] == body["token"]

    def test_publish_404(self, client: TestClient) -> None:
        assert client.post(f"/maps/{uuid4()}/publish").status_code == 404

    def test_republish_keeps_token(self, client: TestClient) -> None:
        mid = client.post("/maps", json=_payload()).json()["id"]
        t1 = client.post(f"/maps/{mid}/publish").json()["token"]
        t2 = client.post(f"/maps/{mid}/publish").json()["token"]
        assert t1 == t2

    def test_unpublish_invalidates(self, client: TestClient) -> None:
        mid = client.post("/maps", json=_payload()).json()["id"]
        token = client.post(f"/maps/{mid}/publish").json()["token"]
        assert client.get(f"/public/maps/{token}").status_code == 200
        assert client.delete(f"/maps/{mid}/publish").status_code == 204
        # token invalid immediately
        assert client.get(f"/public/maps/{token}").status_code == 404
        assert client.get(f"/maps/{mid}").json()["is_published"] is False

    def test_unpublish_404(self, client: TestClient) -> None:
        assert client.delete(f"/maps/{uuid4()}/publish").status_code == 404

    def test_community_quota_enforced(self, client: TestClient, monkeypatch) -> None:
        # The shared test suite defaults to Pro (unlimited); force community
        # so the published-map quota (3) applies. get_current_tier() reads the
        # env live, so this takes effect for the publish requests below.
        monkeypatch.setenv("GISPULSE_TIER", "community")
        ids = [client.post("/maps", json=_payload(name=f"m{i}")).json()["id"] for i in range(4)]
        for mid in ids[:3]:
            assert client.post(f"/maps/{mid}/publish").status_code == 200
        # 4th distinct publish exceeds the quota
        resp = client.post(f"/maps/{ids[3]}/publish")
        assert resp.status_code == 402
        # re-publishing an already-published one is still allowed
        assert client.post(f"/maps/{ids[0]}/publish").status_code == 200
        # freeing a slot lets the 4th publish
        assert client.delete(f"/maps/{ids[0]}/publish").status_code == 204
        assert client.post(f"/maps/{ids[3]}/publish").status_code == 200
