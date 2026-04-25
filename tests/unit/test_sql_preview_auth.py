"""Security regression: /capabilities/sql-preview must require X-Admin-Key
and reject DDL/DCL — same perimeter as /portal/sql/execute.

Audit deep 2026-04-24 v3 §CRITICAL: prior versions of the route had no
auth dependency and skipped `_validate_sql_readonly`, exposing arbitrary
PostGIS execution to any anonymous caller in portal mode.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """Boot the HTTP app with a known admin key and a non-empty DSN so the
    auth gate is the *first* thing that runs. The DSN is intentionally
    bogus — none of the tests in this module reach the actual DB engine."""
    monkeypatch.setenv("GISPULSE_SQL_ADMIN_KEY", "test_sql_admin_key")
    monkeypatch.setenv("GISPULSE_POSTGIS_DSN", "postgresql://nobody@/nodb")
    # Force config reload — settings is a module-level singleton.
    from core import config as _cfg_mod
    monkeypatch.setattr(_cfg_mod, "settings", _cfg_mod.Settings())

    from gispulse.adapters.http.app import create_app
    return TestClient(create_app())


def test_sql_preview_without_admin_key_is_403(client):
    """No X-Admin-Key header → 403 (auth gate fires before DSN/SQL checks)."""
    resp = client.post(
        "/capabilities/sql-preview",
        json={"sql": "SELECT 1", "params": {}, "limit": 10},
    )
    assert resp.status_code == 403


def test_sql_preview_with_wrong_admin_key_is_403(client):
    resp = client.post(
        "/capabilities/sql-preview",
        json={"sql": "SELECT 1", "params": {}, "limit": 10},
        headers={"X-Admin-Key": "totally_wrong"},
    )
    assert resp.status_code == 403


def test_sql_preview_blocks_drop_table(client):
    """Even with a valid admin key, DDL must be rejected upstream of the engine."""
    resp = client.post(
        "/capabilities/sql-preview",
        json={"sql": "DROP TABLE users", "params": {}, "limit": 10},
        headers={"X-Admin-Key": "test_sql_admin_key"},
    )
    assert resp.status_code == 400


def test_sql_preview_blocks_pg_read_file(client):
    resp = client.post(
        "/capabilities/sql-preview",
        json={
            "sql": "SELECT pg_read_file('/etc/passwd')",
            "params": {},
            "limit": 10,
        },
        headers={"X-Admin-Key": "test_sql_admin_key"},
    )
    assert resp.status_code == 400


def test_sql_preview_blocks_set_role(client):
    resp = client.post(
        "/capabilities/sql-preview",
        json={"sql": "SET ROLE postgres; SELECT 1", "params": {}, "limit": 10},
        headers={"X-Admin-Key": "test_sql_admin_key"},
    )
    assert resp.status_code == 400


def test_sql_preview_with_admin_key_passes_auth_gate(client):
    """Valid key + clean SELECT → does not 401/403/400. The bogus DSN means
    the call will fail at the engine layer, surfacing as a non-empty
    ``error`` field in the response body or a 5xx — but never as the
    auth/blocklist gates we just added.
    """
    resp = client.post(
        "/capabilities/sql-preview",
        json={"sql": "SELECT 1 AS x", "params": {}, "limit": 5},
        headers={"X-Admin-Key": "test_sql_admin_key"},
    )
    assert resp.status_code not in (401, 403)
    if resp.status_code == 200:
        body = resp.json()
        # Engine failure is acceptable (bogus DSN); auth + blocklist passed.
        assert body.get("error") is None or "PostGIS" in body["error"] or "engine" in body["error"].lower() or "connect" in body["error"].lower()
