"""
Unit tests for the Triggers router — CRUD, toggle, evaluate, edge cases.
"""

from __future__ import annotations

import os
from uuid import uuid4

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


TRIGGER_PAYLOAD = {
    "name": "on_insert_parcels",
    "description": "Fire on parcel inserts",
    "event": "manual",
    "trigger_type": "api",
    "category": "data",
    "severity": "info",
    "conditions": {"table": "parcels"},
    "enabled": True,
    "auto_eval": False,
}


class TestCreateTrigger:
    def test_create_returns_201(self, client: TestClient) -> None:
        r = client.post("/triggers", json=TRIGGER_PAYLOAD)
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "on_insert_parcels"
        assert "id" in body
        assert body["enabled"] is True

    def test_create_minimal(self, client: TestClient) -> None:
        r = client.post("/triggers", json={"name": "minimal"})
        assert r.status_code == 201
        body = r.json()
        assert body["trigger_type"] == "api"
        assert body["event"] == "manual"

    def test_create_missing_name_returns_422(self, client: TestClient) -> None:
        r = client.post("/triggers", json={"description": "no name"})
        assert r.status_code == 422


class TestListTriggers:
    def test_list_empty(self, client: TestClient) -> None:
        r = client.get("/triggers")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_after_create(self, client: TestClient) -> None:
        client.post("/triggers", json=TRIGGER_PAYLOAD)
        r = client.get("/triggers")
        body = r.json()
        assert body["total"] == 1

    def test_list_pagination(self, client: TestClient) -> None:
        for i in range(5):
            client.post("/triggers", json={**TRIGGER_PAYLOAD, "name": f"trigger_{i}"})
        r = client.get("/triggers?limit=2&offset=0")
        body = r.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5


class TestGetTrigger:
    def test_get_existing(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        r = client.get(f"/triggers/{created['id']}")
        assert r.status_code == 200
        assert r.json()["name"] == "on_insert_parcels"

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.get(f"/triggers/{uuid4()}")
        assert r.status_code == 404


class TestUpdateTrigger:
    def test_update_trigger(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        updated = {**TRIGGER_PAYLOAD, "name": "renamed_trigger", "severity": "warning"}
        r = client.put(f"/triggers/{created['id']}", json=updated)
        assert r.status_code == 200
        assert r.json()["name"] == "renamed_trigger"

    def test_update_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.put(f"/triggers/{uuid4()}", json=TRIGGER_PAYLOAD)
        assert r.status_code == 404


class TestDeleteTrigger:
    def test_delete_trigger(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        r = client.delete(f"/triggers/{created['id']}")
        assert r.status_code == 204

        r2 = client.get(f"/triggers/{created['id']}")
        assert r2.status_code == 404

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.delete(f"/triggers/{uuid4()}")
        assert r.status_code == 404


class TestToggleTrigger:
    def test_toggle_disable(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        r = client.post(f"/triggers/{created['id']}/toggle")
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_toggle_reenable(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        client.post(f"/triggers/{created['id']}/toggle")  # disable
        r = client.post(f"/triggers/{created['id']}/toggle")  # re-enable
        assert r.status_code == 200
        assert r.json()["enabled"] is True

    def test_toggle_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.post(f"/triggers/{uuid4()}/toggle")
        assert r.status_code == 404


class TestEvaluateTrigger:
    def test_evaluate_trigger(self, client: TestClient) -> None:
        created = client.post("/triggers", json=TRIGGER_PAYLOAD).json()
        r = client.post(
            f"/triggers/{created['id']}/evaluate",
            json={
                "records": [
                    {
                        "table_name": "parcels",
                        "operation": "INSERT",
                        "new_values": {"area": 100},
                    }
                ]
            },
        )
        assert r.status_code == 200

    def test_evaluate_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.post(
            f"/triggers/{uuid4()}/evaluate",
            json={"records": [{"table_name": "t", "operation": "INSERT"}]},
        )
        assert r.status_code == 404


class TestTriggerCreateStrictValidation:
    def test_extra_field_rejected(self, client: TestClient) -> None:
        payload = {**TRIGGER_PAYLOAD, "type": "attribute_threshold", "scope": "layer"}
        r = client.post("/triggers", json=payload)
        assert r.status_code == 422
        body = r.text
        assert "type" in body or "scope" in body


# ---------------------------------------------------------------------------
# Issue #94 — POST /triggers/import (CLI ↔ Portal parity P0-5)
# ---------------------------------------------------------------------------


_VALID_YAML = """
gpkg: /tmp/<unbound>
triggers:
  - name: alerte_ppri
    table: permis
    when: [INSERT]
    actions:
      - type: webhook
        url: https://example.com/hook
  - name: log_changes
    table: parcels
    when: [INSERT, UPDATE, DELETE]
    actions:
      - type: log_event
"""

_INVALID_YAML = """
gpkg: /tmp/<unbound>
triggers:
  - name: bad
    table: parcels
    when: []   # empty when list — pydantic must reject
    actions:
      - type: log_event
"""

_MALFORMED_YAML = "gpkg: ['unterminated"


class TestImportTriggers:
    """B-94 / #94 — POST /triggers/import drives the portal's
    "Import YAML" button. Same validator as the CLI's
    ``gispulse triggers validate``."""

    def test_dry_run_raw_body_returns_preview(self, client: TestClient) -> None:
        r = client.post(
            "/triggers/import",
            content=_VALID_YAML,
            headers={"content-type": "application/x-yaml"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is True
        assert body["summary"]["triggers"] == 2
        assert body["summary"]["actions"] == 2
        assert len(body["preview"]) == 2
        names = {p["name"] for p in body["preview"]}
        assert names == {"alerte_ppri", "log_changes"}
        # Dry-run does NOT persist.
        listed = client.get("/triggers").json()
        assert all(t["name"] not in names for t in listed.get("items", []))

    def test_dry_run_multipart_upload(self, client: TestClient) -> None:
        r = client.post(
            "/triggers/import",
            files={"file": ("triggers.yaml", _VALID_YAML, "application/x-yaml")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is True
        assert body["summary"]["triggers"] == 2

    def test_invalid_schema_returns_422(self, client: TestClient) -> None:
        r = client.post(
            "/triggers/import",
            content=_INVALID_YAML,
            headers={"content-type": "application/x-yaml"},
        )
        assert r.status_code == 422
        body = r.json()
        # The endpoint emits a structured envelope directly (bypasses the
        # global error wrapper) so the portal can render per-field
        # diagnostics.
        assert body.get("valid") is False
        errors = body.get("errors", [])
        assert len(errors) >= 1
        assert any(
            "when" in (e.get("message") or "").lower() for e in errors
        )

    def test_malformed_yaml_returns_422(self, client: TestClient) -> None:
        r = client.post(
            "/triggers/import",
            content=_MALFORMED_YAML,
            headers={"content-type": "application/x-yaml"},
        )
        assert r.status_code == 422
        body = r.json()
        assert body.get("valid") is False

    def test_empty_body_returns_400(self, client: TestClient) -> None:
        r = client.post(
            "/triggers/import",
            content="",
            headers={"content-type": "application/x-yaml"},
        )
        assert r.status_code == 400

    def test_oversize_body_returns_413(self, client: TestClient) -> None:
        # 2 MiB > 1 MiB cap.
        big = "# " + ("x" * (2 * 1024 * 1024))
        r = client.post(
            "/triggers/import",
            content=big,
            headers={"content-type": "application/x-yaml"},
        )
        assert r.status_code == 413

    def test_commit_persists_triggers(self, client: TestClient) -> None:
        r = client.post(
            "/triggers/import?commit=true",
            content=_VALID_YAML,
            headers={"content-type": "application/x-yaml"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is True
        assert "committed" in body
        assert len(body["committed"]) == 2
        # GET /triggers must now include both names.
        listed = client.get("/triggers").json()
        names = {t["name"] for t in listed.get("items", [])}
        assert "alerte_ppri" in names
        assert "log_changes" in names

    def test_commit_is_idempotent_on_existing_name(
        self, client: TestClient
    ) -> None:
        """Importing the same YAML twice must not duplicate triggers —
        existing names are skipped."""
        client.post(
            "/triggers/import?commit=true",
            content=_VALID_YAML,
            headers={"content-type": "application/x-yaml"},
        )
        r2 = client.post(
            "/triggers/import?commit=true",
            content=_VALID_YAML,
            headers={"content-type": "application/x-yaml"},
        )
        assert r2.status_code == 200
        body = r2.json()
        # Second pass: 0 newly committed, both names listed under skipped.
        assert body["committed"] == []
        assert set(body["skipped"]) == {"alerte_ppri", "log_changes"}
        # Repository must still hold a single copy of each.
        listed = client.get("/triggers").json()
        names = [t["name"] for t in listed.get("items", [])]
        assert names.count("alerte_ppri") == 1
        assert names.count("log_changes") == 1
