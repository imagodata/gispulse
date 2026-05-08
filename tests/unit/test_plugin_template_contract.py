"""Contract checks for the external plugin template."""

from __future__ import annotations

import inspect
import importlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core.plugin_contracts import PluginHostContext
from core import plugin_hub


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "examples" / "plugin-template"


@dataclass
class _TemplateEntryPoint:
    name: str
    value: str

    def load(self) -> Any:
        module_name, object_name = self.value.split(":", 1)
        module = importlib.import_module(module_name)
        return getattr(module, object_name)


def test_plugin_template_capability_uses_current_execute_signature() -> None:
    from capabilities.registry import REGISTRY

    source = (TEMPLATE / "gispulse_cap_example" / "capabilities.py").read_text(
        encoding="utf-8"
    )

    assert "from gispulse.plugins.api import Capability, register_capability" in source
    assert "from capabilities." not in source

    REGISTRY._items.pop("centroid", None)
    sys.path.insert(0, str(TEMPLATE))
    try:
        from gispulse_cap_example.capabilities import CentroidCapability
    finally:
        sys.path.remove(str(TEMPLATE))

    signature = inspect.signature(CentroidCapability.execute)

    assert "config" not in signature.parameters
    assert any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def test_plugin_template_declares_mountable_router_entry_point() -> None:
    pyproject = (TEMPLATE / "pyproject.toml").read_text(encoding="utf-8")

    assert '[project.entry-points."gispulse.routers"]' in pyproject
    assert 'example = "gispulse_cap_example.routers:ExampleRouterFactory"' in pyproject

    sys.path.insert(0, str(TEMPLATE))
    try:
        from gispulse_cap_example.routers import ExampleRouterFactory
    finally:
        sys.path.remove(str(TEMPLATE))

    app = FastAPI()
    ctx = PluginHostContext(
        app=app,
        settings=None,
        logger=logging.getLogger("test.plugin_template"),
        plugin_hub=None,
    )
    app.include_router(ExampleRouterFactory().create(ctx))

    response = TestClient(app).get("/plugins/example/health")

    assert response.status_code == 200
    assert response.json() == {"plugin": "example", "status": "ok"}


def test_plugin_template_router_is_discoverable_through_plugin_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_entry_points(group: str | None = None, **_kwargs: object) -> list[_TemplateEntryPoint]:
        if group == "gispulse.routers":
            return [
                _TemplateEntryPoint(
                    "example",
                    "gispulse_cap_example.routers:ExampleRouterFactory",
                )
            ]
        return []

    monkeypatch.setattr(plugin_hub, "entry_points", fake_entry_points)
    plugin_hub.PluginHub.reset()
    sys.path.insert(0, str(TEMPLATE))
    try:
        hub = plugin_hub.PluginHub.get()
        app = FastAPI()
        ctx = PluginHostContext(
            app=app,
            settings=None,
            logger=logging.getLogger("test.plugin_template"),
            plugin_hub=hub,
        )
        app.include_router(hub.routers["example"].create(ctx))
    finally:
        sys.path.remove(str(TEMPLATE))
        plugin_hub.PluginHub.reset()

    response = TestClient(app).get("/plugins/example/health")

    assert response.status_code == 200
    assert response.json() == {"plugin": "example", "status": "ok"}


def test_plugin_contract_document_exists_for_current_host_surface() -> None:
    doc = ROOT / "docs" / "PLUGIN_CONTRACT.md"

    content = doc.read_text(encoding="utf-8")

    assert "https://github.com/imagodata/gispulse/issues/68" in content
    assert "Refs #68" in content
    assert "gispulse.routers" in content
    assert "gispulse.mcp_tools" in content
    assert "observed in the Permis Check integration" in content
    assert "does not prove or stabilize it" in content
    assert "RouterFactory.create(app)" in content
    assert "gispulse.plugins.api" in content
    assert "PluginHostContext" in content
    assert "PR Plan" in content
    assert "not yet the full stable SDK" in content
    assert "transitional" in content
    assert "New plugins should not treat `app.state` as a\nstable SDK" in content
    assert "temporary escape hatch" in content
    assert "`core.*`, `catalog.*`, `orchestration.*`" in content
