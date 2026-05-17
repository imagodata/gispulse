"""Tests for the GISPulse FastMCP facade (adapters/mcp/server.py).

Since v1.8.0 the server is a thin, stateless adapter over
:class:`gispulse.app.GISPulseApp` — every tool is a one-liner onto a
GISPulseApp use-case. FastMCP is an optional dependency; all tests are
skipped when it is not installed.
"""

from __future__ import annotations

import json

import pytest

fastmcp = pytest.importorskip("fastmcp")

# Only import after the skip guard so the module-level ImportError in
# server.py is not triggered in environments without fastmcp.
from gispulse.adapters.mcp import server as mcp_server  # noqa: E402
from gispulse.core import plugin_hub  # noqa: E402


# ---------------------------------------------------------------------------
# Server creation & statelessness
# ---------------------------------------------------------------------------


def test_mcp_server_created():
    """FastMCP server instance exists and has the correct name."""
    assert mcp_server.mcp is not None
    assert mcp_server.mcp.name == "GISPulse"


def test_no_inmemory_repository():
    """Regression guard: the pre-v1.8.0 in-memory repos must stay gone."""
    for attr in ("_rule_repo", "_dataset_repo", "_job_repo"):
        assert not hasattr(mcp_server, attr), (
            f"{attr} re-introduced — the MCP adapter must stay stateless "
            "and delegate to GISPulseApp."
        )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_tools_registered():
    """All expected tools are registered on the FastMCP server."""
    import asyncio

    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool_names = {t.name for t in tools}

    expected = {
        "list_capabilities",
        "get_capability_info",
        "browse_catalog",
        "get_catalog_entry",
        "list_templates",
        "get_template",
        "inspect_dataset",
        "validate_pipeline",
        "list_plugins",
    }
    missing = expected - tool_names
    assert not missing, f"Missing tools: {missing}"


def test_plugin_tool_registered(monkeypatch):
    """MCP tool entry-points can extend a freshly-created server."""
    import asyncio

    class FakeEntryPoint:
        name = "fake-mcp-tool"

        def load(self):
            class FakeToolFactory:
                name = "fake-mcp-tool"

                def register(self, mcp):
                    @mcp.tool()
                    def plugin_ping() -> str:
                        return "pong"

            return FakeToolFactory

    def fake_entry_points(group: str | None = None, **_kwargs):
        if group == "gispulse.mcp_tools":
            return [FakeEntryPoint()]
        return []

    monkeypatch.setattr(plugin_hub, "entry_points", fake_entry_points)
    plugin_hub.PluginHub.reset()

    server = mcp_server.create_mcp_server()
    tools = asyncio.run(server.list_tools())
    tool_names = {t.name for t in tools}

    assert "plugin_ping" in tool_names

    plugin_hub.PluginHub.reset()


# ---------------------------------------------------------------------------
# Capability tools
# ---------------------------------------------------------------------------


def test_list_capabilities_returns_list():
    """list_capabilities() returns a non-empty list of capability dicts."""
    caps = mcp_server.list_capabilities()

    assert isinstance(caps, list)
    assert len(caps) > 0
    for cap in caps:
        assert {"name", "description", "schema"} <= set(cap)


def test_list_capabilities_includes_buffer():
    """list_capabilities() includes the 'buffer' capability."""
    names = {c["name"] for c in mcp_server.list_capabilities()}
    assert "buffer" in names


def test_get_capability_info_known():
    """get_capability_info() returns correct metadata for 'buffer'."""
    info = mcp_server.get_capability_info("buffer")

    assert "error" not in info
    assert info["name"] == "buffer"
    assert "description" in info
    assert "schema" in info


def test_get_capability_info_unknown():
    """get_capability_info() returns an error dict for unknown capabilities."""
    info = mcp_server.get_capability_info("nonexistent_capability_xyz")

    assert "error" in info
    assert "available" in info


# ---------------------------------------------------------------------------
# Catalog tools
# ---------------------------------------------------------------------------


def test_browse_catalog_returns_list():
    """browse_catalog() returns a list of catalog entry dicts."""
    entries = mcp_server.browse_catalog(limit=5)
    assert isinstance(entries, list)


def test_browse_catalog_invalid_domain():
    """browse_catalog() rejects an unknown domain with an error entry."""
    result = mcp_server.browse_catalog(domain="not-a-domain")
    assert result and "error" in result[0]
    assert "available" in result[0]


def test_get_catalog_entry_unknown():
    """get_catalog_entry() returns an error dict for an unknown id."""
    result = mcp_server.get_catalog_entry("__no_such_entry__")
    assert "error" in result


# ---------------------------------------------------------------------------
# Template tools
# ---------------------------------------------------------------------------


def test_list_templates_finds_builtins():
    """list_templates() exposes the bundled pipeline templates."""
    templates = mcp_server.list_templates()
    assert isinstance(templates, list)
    assert templates, "expected built-in templates"
    assert {"name", "title", "description"} <= set(templates[0])


def test_get_template_known():
    """get_template() returns the raw JSON of a known template."""
    name = mcp_server.list_templates()[0]["name"]
    tpl = mcp_server.get_template(name)
    assert "error" not in tpl


def test_get_template_unknown():
    """get_template() returns an error dict for an unknown template."""
    result = mcp_server.get_template("__no_such_template__")
    assert "error" in result


# ---------------------------------------------------------------------------
# Dataset / pipeline tools
# ---------------------------------------------------------------------------


def test_inspect_dataset_missing_file():
    """inspect_dataset() returns an error for a non-existent path."""
    result = mcp_server.inspect_dataset("/tmp/does_not_exist_gispulse_test.gpkg")
    assert "error" in result


def test_validate_pipeline_missing_file():
    """validate_pipeline() reports a missing file as invalid."""
    result = mcp_server.validate_pipeline("/tmp/__no_pipeline__.json")
    assert result["valid"] is False
    assert result["errors"]


def test_validate_pipeline_valid(tmp_path):
    """validate_pipeline() accepts a well-formed v2 pipeline file."""
    spec = {
        "version": 2,
        "name": "smoke",
        "steps": [
            {
                "id": "s1",
                "type": "capability",
                "capability": "buffer",
                "params": {"distance": 5},
            }
        ],
    }
    pipeline_file = tmp_path / "pipeline.json"
    pipeline_file.write_text(json.dumps(spec), encoding="utf-8")

    result = mcp_server.validate_pipeline(str(pipeline_file))
    assert result["valid"] is True
    assert result["steps"] == 1


# ---------------------------------------------------------------------------
# Plugin tools
# ---------------------------------------------------------------------------


def test_list_plugins_returns_list():
    """list_plugins() returns JSON-safe plugin record dicts."""
    plugins = mcp_server.list_plugins()
    assert isinstance(plugins, list)
    # Must be JSON-serialisable (PluginRecord.as_dict omits entry_point/obj).
    json.dumps(plugins)


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------


def test_resource_capabilities_is_valid_json():
    """gispulse://capabilities resource returns valid JSON with capabilities."""
    data = json.loads(mcp_server.resource_capabilities())
    assert isinstance(data, list)
    assert len(data) > 0


def test_resource_templates_is_valid_json():
    """gispulse://templates resource returns valid JSON with templates."""
    data = json.loads(mcp_server.resource_templates())
    assert isinstance(data, list)
    assert len(data) > 0
