"""GISPulse MCP server — a thin FastMCP adapter over :class:`GISPulseApp`.

Every tool is a one-liner onto a :class:`gispulse.app.GISPulseApp` use-case
(Chantier B of the v1.8.0 "Foundations" refonte). The server holds **no
state of its own** — the pre-v1.8.0 in-memory ``Rule`` / ``Job`` / ``Dataset``
repositories are gone; they modelled a rule grammar that predated the
pipeline-v2 unification and drifted from the rest of the product.

Tools
-----
Capabilities:
  list_capabilities()          -> capability metadata dicts
  get_capability_info(name)    -> single capability detail dict

Catalog:
  browse_catalog(...)          -> matching GIS catalog entries
  get_catalog_entry(entry_id)  -> single catalog entry

Templates:
  list_templates()             -> built-in pipeline templates
  get_template(name)           -> raw template JSON

Datasets / pipelines (read-only — no FS writes, no session state):
  inspect_dataset(path)        -> layer names of a GeoPackage
  validate_pipeline(path)      -> schema-validate a pipeline JSON file

Plugins:
  list_plugins()               -> PluginHub inventory records

Resources
---------
  gispulse://capabilities      -> JSON list of capabilities
  gispulse://templates         -> JSON list of built-in templates

Execution-oriented tools (trigger / changelog / dryrun) and outbound FS
scoping are tracked separately in the MCP epic (#202–#205).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

try:
    from fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "fastmcp is required for the MCP facade. "
        "Install it with: pip install fastmcp"
    ) from exc

from gispulse.app import GISPulseApp
from gispulse.catalog.models import CatalogDomain
from gispulse.core.logging import get_logger
from gispulse.core.plugin_hub import PluginHub

log = get_logger(__name__)

# Stateless façade — shared by every tool. GISPulseApp holds no engine or
# repository handle of its own; it wires each subsystem lazily on demand.
_app = GISPulseApp()


# ---------------------------------------------------------------------------
# FastMCP server helpers
# ---------------------------------------------------------------------------


def create_mcp_server(name: str = "GISPulse", *, include_plugins: bool = True):
    """Create a FastMCP server with built-in and plugin-contributed surfaces."""
    server = FastMCP(name)
    register_builtin_mcp_surface(server)
    if include_plugins:
        register_plugin_mcp_surface(server)
    return server


def register_builtin_mcp_surface(server: Any) -> None:
    """Register the built-in GISPulse MCP tools and resources."""
    for fn in (
        list_capabilities,
        get_capability_info,
        browse_catalog,
        get_catalog_entry,
        list_templates,
        get_template,
        inspect_dataset,
        validate_pipeline,
        list_plugins,
    ):
        server.tool()(fn)
    server.resource("gispulse://capabilities")(resource_capabilities)
    server.resource("gispulse://templates")(resource_templates)


def register_plugin_mcp_surface(server: Any) -> None:
    """Install MCP tools/resources provided through PluginHub entry-points."""
    hub = PluginHub.get()
    for factory in hub.mcp_tools:
        try:
            factory.register(server)
            log.info("plugin_mcp_tools_registered", plugin=factory.name)
        except Exception as exc:
            log.warning("plugin_mcp_tools_failed", plugin=factory.name, error=str(exc))
    for factory in hub.mcp_resources:
        try:
            factory.register(server)
            log.info("plugin_mcp_resources_registered", plugin=factory.name)
        except Exception as exc:
            log.warning(
                "plugin_mcp_resources_failed", plugin=factory.name, error=str(exc)
            )


# ===========================================================================
# Capability tools
# ===========================================================================


def list_capabilities() -> list[dict[str, Any]]:
    """List every registered GISPulse capability with its schema.

    Returns:
        List of dicts with keys ``name``, ``description``, ``schema``.
    """
    return _app.list_capabilities()


def get_capability_info(name: str) -> dict[str, Any]:
    """Return detail for a single capability.

    Args:
        name: Registered capability name (e.g. 'buffer', 'filter').

    Returns:
        Dict with keys ``name``, ``description``, ``schema``; or an
        ``error`` key plus ``available`` names when ``name`` is unknown.
    """
    caps = _app.list_capabilities()
    for cap in caps:
        if cap["name"] == name:
            return cap
    return {
        "error": f"Capability '{name}' not found.",
        "available": sorted(c["name"] for c in caps),
    }


# ===========================================================================
# Catalog tools
# ===========================================================================


def browse_catalog(
    domain: str | None = None,
    search: str | None = None,
    provider: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Search the unified GIS data catalog.

    Args:
        domain:   Optional domain filter — ``projection`` | ``basemap`` |
                  ``flux`` | ``opendata``.
        search:   Optional free-text query matched on name / description.
        provider: Optional provider name filter.
        limit:    Maximum number of entries to return (default 25).

    Returns:
        List of catalog entry dicts; an ``error`` key wraps an invalid
        ``domain`` value.
    """
    domain_enum: CatalogDomain | None = None
    if domain:
        try:
            domain_enum = CatalogDomain(domain)
        except ValueError:
            return [
                {
                    "error": f"Invalid domain '{domain}'.",
                    "available": [d.value for d in CatalogDomain],
                }
            ]
    entries = _app.browse_catalog(
        domain=domain_enum, search=search, provider=provider, limit=limit
    )
    return [asdict(e) for e in entries]


def get_catalog_entry(entry_id: str) -> dict[str, Any]:
    """Look up a single catalog entry by its full id.

    Returns:
        The catalog entry dict, or an ``error`` key when not found.
    """
    entry = _app.get_catalog_entry(entry_id)
    if entry is None:
        return {"error": f"Catalog entry '{entry_id}' not found."}
    return asdict(entry)


# ===========================================================================
# Template tools
# ===========================================================================


def list_templates() -> list[dict[str, Any]]:
    """List the built-in pipeline templates.

    Returns:
        One dict per template with ``name``, ``title`` and ``description``.
    """
    return _app.list_templates()


def get_template(name: str) -> dict[str, Any]:
    """Return the raw JSON of a built-in pipeline template.

    Args:
        name: Template name (the file stem, without ``.json``).

    Returns:
        The template JSON object, or an ``error`` key when unknown.
    """
    try:
        return _app.get_template(name)
    except FileNotFoundError as exc:
        return {"error": str(exc)}


# ===========================================================================
# Dataset / pipeline tools (read-only)
# ===========================================================================


def inspect_dataset(path: str) -> dict[str, Any]:
    """List the layers of a GeoPackage file without loading its features.

    Args:
        path: Absolute path to a ``.gpkg`` file.

    Returns:
        Dict with ``path`` and ``layers`` (list of layer names), or an
        ``error`` key when the file is missing or unreadable.
    """
    import os

    if not os.path.isfile(path):
        return {"error": f"File not found: '{path}'"}
    try:
        layers = _app.list_layers(path)
    except Exception as exc:
        return {"error": f"Cannot read GeoPackage layers: {exc}"}
    return {"path": path, "layers": layers}


def validate_pipeline(path: str) -> dict[str, Any]:
    """Schema-validate a pipeline JSON file (v1 or v2 grammar).

    Args:
        path: Path to a pipeline / rules JSON file.

    Returns:
        Dict with ``valid`` (bool). On success it also carries ``name``
        and ``steps`` (count); on failure, ``errors`` (list of strings).
    """
    try:
        spec = _app.load_pipeline(path, validate=True)
    except FileNotFoundError as exc:
        return {"valid": False, "errors": [str(exc)]}
    except ValueError as exc:
        return {"valid": False, "errors": str(exc).splitlines()}
    except json.JSONDecodeError as exc:
        return {"valid": False, "errors": [f"Invalid JSON: {exc}"]}
    return {"valid": True, "name": spec.name, "steps": len(spec.steps)}


# ===========================================================================
# Plugin tools
# ===========================================================================


def list_plugins() -> list[dict[str, Any]]:
    """List the plugins discovered by the unified PluginHub.

    Returns:
        One dict per plugin record — sources, capabilities, sinks,
        templates and extensions — as produced by ``PluginRecord.as_dict``.
    """
    return [rec.as_dict() for rec in _app.list_plugins()]


# ===========================================================================
# MCP Resources
# ===========================================================================


def resource_capabilities() -> str:
    """MCP resource: all registered GISPulse capabilities (JSON)."""
    return json.dumps(_app.list_capabilities(), indent=2)


def resource_templates() -> str:
    """MCP resource: the built-in pipeline templates (JSON)."""
    return json.dumps(_app.list_templates(), indent=2)


# Module-level compatibility instance used by existing imports / launchers.
mcp = create_mcp_server()
