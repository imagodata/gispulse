"""``gispulse mcp`` — run the GISPulse MCP server (issue #201).

Exposes the GISPulse tool / resource surface to LLM agents over the
Model Context Protocol. The transport is **stdio** — the shape Claude
Desktop and Claude Code expect for a local server. Wire a client at it
with, e.g. in ``.mcp.json``::

    {
      "mcpServers": {
        "gispulse": { "command": "gispulse", "args": ["mcp"] }
      }
    }

The MCP server module itself (``gispulse.adapters.mcp.server``) was
delivered by PR #162 but had no launcher — this command is that missing
launcher. Re-aligning the tool surface on the current product and an
HTTP transport are tracked in the MCP epic (milestone v1.8.0).
"""

from __future__ import annotations

import typer


def cmd_mcp(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        help="MCP transport. Only 'stdio' is supported in v1.7.0.",
    ),
) -> None:
    """Run the GISPulse MCP server (stdio) for LLM agents."""
    try:
        from gispulse.adapters.mcp.server import create_mcp_server
    except ImportError:
        typer.echo(
            "MCP support requires the 'mcp' extra. "
            "Install it with: pip install 'gispulse[mcp]'",
            err=True,
        )
        raise typer.Exit(1)

    if transport != "stdio":
        typer.echo(
            f"unsupported transport {transport!r} — only 'stdio' is "
            "available in v1.7.0 (HTTP transport is tracked in the MCP "
            "epic, milestone v1.8.0)",
            err=True,
        )
        raise typer.Exit(1)

    server = create_mcp_server()
    typer.echo("GISPulse MCP server — stdio transport, ready.", err=True)
    # FastMCP's stdio transport blocks until the client disconnects.
    server.run()
