"""Runtime tests for PluginHub surfaces consumed by the HTTP app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from gispulse.core import plugin_hub
from gispulse.adapters.http.app import create_app


@dataclass
class _FakeEntryPoint:
    name: str
    value: Any

    def load(self):  # noqa: D401 - match EntryPoint API
        return self.value


def _patch_eps(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, list[_FakeEntryPoint]]) -> None:
    def fake(group: str | None = None, **_kwargs):
        if group is None:
            return []
        return mapping.get(group, [])

    monkeypatch.setattr(plugin_hub, "entry_points", fake)
    plugin_hub.PluginHub.reset()


@pytest.fixture(autouse=True)
def _memory_storage(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.delenv("GISPULSE_API_KEYS", raising=False)
    plugin_hub.PluginHub.reset()
    yield
    plugin_hub.PluginHub.reset()


def test_create_app_mounts_plugin_router(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRouterFactory:
        name = "fake-router"

        def create(self, app):
            router = APIRouter()

            @router.get("/plugin/ping")
            def ping():
                return {"plugin": self.name}

            return router

    _patch_eps(
        monkeypatch,
        {"gispulse.routers": [_FakeEntryPoint("fake-router", FakeRouterFactory)]},
    )

    client = TestClient(create_app())
    response = client.get("/plugin/ping")

    assert response.status_code == 200
    assert response.json() == {"plugin": "fake-router"}


def test_create_app_runs_plugin_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeLifecycle:
        name = "fake-lifecycle"

        def on_startup(self, app):
            events.append("startup")

        def on_shutdown(self, app):
            events.append("shutdown")

    _patch_eps(
        monkeypatch,
        {"gispulse.lifecycle": [_FakeEntryPoint("fake-lifecycle", FakeLifecycle)]},
    )

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
        assert events == ["startup"]

    assert events == ["startup", "shutdown"]

