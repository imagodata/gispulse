"""Pilot ``gispulse.mcp_tools`` plugin — reference for MCP-tool authors (#205).

This module is the *worked example* the MCP-tool authoring guide
(``docs-site/plugins/mcp-tools.md``) walks through. It is a first-party
plugin that contributes one extra tool to the GISPulse MCP server.

Contract
--------
A ``gispulse.mcp_tools`` entry-point must resolve to an object satisfying
:class:`gispulse.core.plugin_contracts.McpToolFactory`:

* a ``name`` attribute (``str``) — used in the hub inventory and logs;
* a ``register(self, mcp)`` method — called once per server with the live
  FastMCP server, where the plugin attaches its tools via ``@mcp.tool()``.

The :class:`~gispulse.core.plugin_hub.ExtensionHub` discovers the
entry-point, instantiates the class (zero-arg constructor), and
:func:`gispulse.adapters.mcp.server.register_plugin_mcp_surface` calls
``register`` on the running server. A plugin that raises during
``register`` is logged and skipped — it never takes the server down.

Keep the factory import-light: the module is imported during hub
discovery, so heavy imports (geopandas, requests, …) belong inside the
tool body, not at module scope.
"""

from __future__ import annotations

from typing import Any


class PilotMcpTools:
    """Reference :class:`McpToolFactory` — contributes ``gispulse_pilot_echo``.

    A real plugin would register domain tools here (e.g. a connector that
    exposes ``query_ftth_coverage``). This pilot keeps the body trivial so
    the *wiring* is the lesson, not the tool logic.
    """

    name = "gispulse-mcp-pilot"

    def register(self, mcp: Any) -> None:
        """Attach this plugin's tools to the FastMCP ``mcp`` server."""

        @mcp.tool()
        def gispulse_pilot_echo(message: str) -> dict[str, str]:
            """Echo a message back — proves the MCP-tool plugin wiring works.

            Args:
                message: Any string the caller wants echoed.

            Returns:
                ``{"plugin": ..., "echo": message}``.
            """
            return {"plugin": PilotMcpTools.name, "echo": message}
