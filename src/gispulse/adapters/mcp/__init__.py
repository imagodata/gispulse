"""GISPulse MCP facade — exposes GISPulse tools and resources via FastMCP."""

from __future__ import annotations

try:
    from gispulse.adapters.mcp.server import mcp

    __all__ = ["mcp"]
except ImportError:
    # fastmcp not installed — MCP facade unavailable
    __all__ = []
