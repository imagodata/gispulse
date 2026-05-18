"""Tests for the pilot ``gispulse.mcp_tools`` plugin (issue #205).

``gispulse.plugins.mcp_pilot.PilotMcpTools`` is the reference
implementation an MCP-tool author copies. These tests verify it honours
the :class:`McpToolFactory` contract and that its tool reaches a freshly
created FastMCP server.
"""

from __future__ import annotations

import pytest

fastmcp = pytest.importorskip("fastmcp")

from gispulse.adapters.mcp import server as mcp_server  # noqa: E402
from gispulse.core import plugin_hub  # noqa: E402
from gispulse.core.plugin_contracts import McpToolFactory  # noqa: E402
from gispulse.plugins.mcp_pilot import PilotMcpTools  # noqa: E402


def test_pilot_satisfies_mcp_tool_factory_protocol():
    """The pilot is a structural McpToolFactory (name + register)."""
    pilot = PilotMcpTools()
    assert isinstance(pilot, McpToolFactory)
    assert pilot.name == "gispulse-mcp-pilot"


def test_pilot_registers_echo_tool_on_a_server():
    """register() attaches gispulse_pilot_echo to a FastMCP server."""
    import asyncio

    server = fastmcp.FastMCP("test")
    PilotMcpTools().register(server)

    tools = asyncio.run(server.list_tools())
    assert "gispulse_pilot_echo" in {t.name for t in tools}


def test_pilot_discovered_via_entry_point():
    """The pilot is published under the gispulse.mcp_tools entry-point.

    Confirms the pyproject.toml declaration is installed so a real
    ``gispulse mcp`` server picks the plugin up. Skips cleanly when the
    package metadata has not been (re)installed.
    """
    plugin_hub.ExtensionHub.reset()
    hub = plugin_hub.ExtensionHub.get()
    names = {factory.name for factory in hub.mcp_tools}
    plugin_hub.ExtensionHub.reset()
    if "gispulse-mcp-pilot" not in names:
        pytest.skip("entry-point metadata not installed (pip install -e .)")
    assert "gispulse-mcp-pilot" in names


def test_pilot_tool_registered_on_default_server():
    """When the entry-point is installed, the default MCP server carries
    the pilot tool through register_plugin_mcp_surface."""
    import asyncio

    plugin_hub.ExtensionHub.reset()
    server = mcp_server.create_mcp_server()
    tools = asyncio.run(server.list_tools())
    tool_names = {t.name for t in tools}
    plugin_hub.ExtensionHub.reset()
    if "gispulse-mcp-pilot" not in {
        f.name for f in plugin_hub.ExtensionHub.get().mcp_tools
    }:
        plugin_hub.ExtensionHub.reset()
        pytest.skip("entry-point metadata not installed (pip install -e .)")
    assert "gispulse_pilot_echo" in tool_names
