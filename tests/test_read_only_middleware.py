"""Tests for the read-only public-demo middleware."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gispulse.adapters.http.middleware.read_only import ReadOnlyMiddleware


def _make_app(admin_keys: set[str] | None = None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ReadOnlyMiddleware, admin_keys=admin_keys or set())

    @app.get("/datasets")
    def list_datasets():
        return {"items": []}

    @app.post("/rules")
    def create_rule():
        return {"id": "x"}

    @app.delete("/rules/{rid}")
    def delete_rule(rid: str):
        return {"ok": True}

    @app.put("/rules/{rid}")
    def update_rule(rid: str):
        return {"ok": True}

    @app.post("/capabilities/sql-preview")
    def sql_preview():
        return {"rows": []}

    @app.post("/rules/{rid}/validate")
    def rule_validate(rid: str):
        return {"valid": True}

    @app.post("/filter/apply")
    def filter_apply():
        return {"ok": True}

    @app.post("/datasets/upload")
    def upload():
        return {"id": "x"}

    return app


def test_safe_methods_pass_through():
    client = TestClient(_make_app())
    assert client.get("/datasets").status_code == 200
    assert client.options("/datasets").status_code in (200, 405)


def test_post_to_persistence_route_blocked():
    client = TestClient(_make_app())
    r = client.post("/rules", json={"name": "x"})
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "READ_ONLY_DEMO"


def test_put_blocked():
    client = TestClient(_make_app())
    r = client.put("/rules/abc", json={})
    assert r.status_code == 403


def test_delete_blocked():
    client = TestClient(_make_app())
    r = client.delete("/rules/abc")
    assert r.status_code == 403


def test_compute_only_post_allowed():
    client = TestClient(_make_app())
    assert client.post("/capabilities/sql-preview", json={"sql": "SELECT 1"}).status_code == 200
    assert client.post("/rules/abc/validate", json={}).status_code == 200
    assert client.post("/filter/apply", json={}).status_code == 200


def test_upload_post_blocked_because_persistent():
    """POST /datasets/upload writes a file — must be blocked."""
    client = TestClient(_make_app())
    r = client.post("/datasets/upload")
    assert r.status_code == 403


def test_admin_key_bypasses_block():
    admin = "secret-admin-key"
    client = TestClient(_make_app(admin_keys={admin}))
    r = client.post("/rules", json={"name": "x"}, headers={"X-API-Key": admin})
    assert r.status_code == 200


def test_admin_key_via_bearer_bypasses_block():
    admin = "bearer-admin-key"
    client = TestClient(_make_app(admin_keys={admin}))
    r = client.post(
        "/rules",
        json={"name": "x"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200


def test_wrong_admin_key_still_blocked():
    client = TestClient(_make_app(admin_keys={"good-key"}))
    r = client.post("/rules", json={}, headers={"X-API-Key": "bad-key"})
    assert r.status_code == 403


def test_no_admin_keys_configured_blocks_everything_anonymous():
    client = TestClient(_make_app(admin_keys=set()))
    r = client.post("/rules", json={}, headers={"X-API-Key": "anything"})
    assert r.status_code == 403
