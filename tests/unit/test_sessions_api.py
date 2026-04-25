"""Tests pour les endpoints /sessions (P-6 #90)."""
from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


@pytest.fixture(autouse=True)
def _use_memory_storage(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    return TestClient(app)


class TestCreateSession:
    def test_create_session_returns_201(self, client):
        resp = client.post("/sessions", json={"source_client": "portal", "ttl_hours": 8})
        assert resp.status_code == 201

    def test_create_session_body_fields(self, client):
        resp = client.post("/sessions", json={"source_client": "qgis", "ttl_hours": 4})
        body = resp.json()
        assert "id" in body
        assert body["schema_name"].startswith("sess_")
        assert body["pg_role"] == body["schema_name"]
        assert len(body["pg_password"]) > 16
        assert body["pg_notify_channel"].startswith("gispulse_sess_")
        assert body["source_client"] == "qgis"
        assert body["ttl_hours"] == 4
        assert "expires_at" in body

    def test_create_session_default_ttl(self, client):
        resp = client.post("/sessions", json={})
        assert resp.status_code == 201
        body = resp.json()
        assert body["ttl_hours"] == 8

    def test_create_session_unique_ids(self, client):
        r1 = client.post("/sessions", json={})
        r2 = client.post("/sessions", json={})
        assert r1.json()["id"] != r2.json()["id"]


class TestGetSession:
    def test_get_session_returns_200(self, client):
        created = client.post("/sessions", json={}).json()
        resp = client.get(f"/sessions/{created['id']}")
        assert resp.status_code == 200

    def test_get_session_matches_created(self, client):
        created = client.post("/sessions", json={"source_client": "cli"}).json()
        body = client.get(f"/sessions/{created['id']}").json()
        assert body["id"] == created["id"]
        assert body["schema_name"] == created["schema_name"]
        assert body["source_client"] == "cli"

    def test_get_unknown_session_returns_404(self, client):
        resp = client.get("/sessions/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestListSessions:
    def test_list_sessions_empty(self, client):
        resp = client.get("/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sessions_after_create(self, client):
        client.post("/sessions", json={"source_client": "portal"})
        resp = client.get("/sessions")
        # Sessions en état PROVISIONING ne sont pas dans list_active()
        # (list_active filtre sur ACTIVE)
        assert resp.status_code == 200


class TestDeleteSession:
    def test_delete_session_returns_204(self, client):
        created = client.post("/sessions", json={}).json()
        resp = client.delete(f"/sessions/{created['id']}")
        assert resp.status_code == 204

    def test_delete_marks_torn_down(self, client):
        created = client.post("/sessions", json={}).json()
        client.delete(f"/sessions/{created['id']}")
        body = client.get(f"/sessions/{created['id']}").json()
        assert body["status"] == "torn_down"

    def test_delete_unknown_returns_404(self, client):
        resp = client.delete("/sessions/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
