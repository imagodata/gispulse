"""GISPulse adapters — HTTP facade, MCP facade, ESB event bus."""

from . import http  # noqa: F401

# Side-effect imports — the core transport fetchers self-register in
# core.sources.PROTOCOLS (issue #192), so DeclarativeSource.fetch() always
# has a WFS / OGC API Features / STAC / REST adapter to dispatch to.
from gispulse.adapters import ogc, rest, stac  # noqa: F401

# MCP facade — available only when fastmcp is installed and compatible
try:
    from gispulse.adapters.mcp import mcp as mcp_server  # noqa: F401

    _MCP_AVAILABLE = True
except (ImportError, TypeError):
    _MCP_AVAILABLE = False

