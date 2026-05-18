"""Unit tests for the Templates router (Chantier C of v1.8.0 Foundations).

The router is a thin adapter over :class:`gispulse.app.GISPulseApp`; these
tests check the HTTP surface — listing, fetching and the 404 path.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from gispulse.adapters.http.app import create_app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    from gispulse.adapters.http.rate_limit import limiter

    limiter.enabled = False


@pytest.fixture()
def client() -> TestClient:
    os.environ["GISPULSE_STORAGE"] = "memory"
    return TestClient(create_app())


def test_list_templates_returns_200(client: TestClient) -> None:
    resp = client.get("/templates")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body, "expected built-in templates"
    assert {"name", "title", "description"} <= set(body[0])


def test_get_template_returns_raw_json(client: TestClient) -> None:
    name = client.get("/templates").json()[0]["name"]
    resp = client.get(f"/templates/{name}")
    assert resp.status_code == 200
    assert isinstance(resp.json(), (dict, list))


def test_get_unknown_template_returns_404(client: TestClient) -> None:
    resp = client.get("/templates/__no_such_template__")
    assert resp.status_code == 404


def test_templates_router_available_in_portal_mode() -> None:
    portal_client = TestClient(create_app(mode="portal"))
    resp = portal_client.get("/templates")
    assert resp.status_code == 200
