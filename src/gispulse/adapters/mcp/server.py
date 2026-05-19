"""GISPulse MCP server — a thin FastMCP adapter over :class:`GISPulseApp`.

Every tool is a one-liner onto a :class:`gispulse.app.GISPulseApp` use-case
(Chantier B of the v1.8.0 "Foundations" refonte). The server holds **no
state of its own** — the pre-v1.8.0 in-memory ``Rule`` / ``Job`` / ``Dataset``
repositories are gone; they modelled a rule grammar that predated the
pipeline-v2 unification and drifted from the rest of the product.

Filesystem scoping (#204)
-------------------------
Every tool that takes a path argument routes it through
:func:`gispulse.adapters.mcp.workdir.resolve_in_workdir`, which bounds the
read to the **MCP workdir** (``GISPULSE_MCP_WORKDIR`` env var, or the
process cwd). A path that escapes the workdir is refused with
``{"error": "path outside MCP workdir: ..."}``. The MCP server is driven
by an untrusted LLM, so an unbounded ``open(path)`` would be a
path-traversal sink.

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

Datasets / pipelines (read-only — workdir-scoped, no session state):
  inspect_dataset(path)        -> layer names of a GeoPackage
  validate_pipeline(path)      -> schema-validate a pipeline JSON file

Triggers / change-log (workdir-scoped reads; dryrun has no side effects):
  load_triggers(path)          -> summary of a triggers.yaml config
  list_triggers(path)          -> detailed trigger list of a config
  validate_triggers(path, gpkg)-> structural validation against the GPKG
  inspect_changelog(gpkg, n)   -> _gispulse_change_log status + recent rows
  watch_status(gpkg)           -> tracked layers + pending change-log count
  dryrun_trigger(path, gpkg)   -> evaluate a config with NO outbound effects

Plugins / sources:
  list_plugins()               -> ExtensionHub inventory records
  list_sources()               -> registered ETL data-source plugins
  refresh_worldwide_catalog()  -> data.gouv.fr freshness probe of FR entries

Resources
---------
  gispulse://capabilities      -> JSON list of capabilities
  gispulse://templates         -> JSON list of built-in templates
  gispulse://sources           -> JSON list of ETL data-source plugins
  gispulse://triggers/{path}   -> JSON summary of a triggers.yaml (template)
  gispulse://changelog/{path}  -> JSON change-log status of a GPKG (template)

The MCP surface is realigned with the rest of the product as part of the
v1.8.0 MCP epic (#202–#205): execution-oriented trigger / change-log /
dryrun tools and outbound FS scoping are now part of the built-in
surface; plugin-contributed tools arrive via the ``gispulse.mcp_tools``
entry-point (#205).
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

from gispulse.adapters.mcp.workdir import WorkdirError, resolve_in_workdir
from gispulse.app import GISPulseApp
from gispulse.catalog.models import CatalogDomain
from gispulse.core.logging import get_logger
from gispulse.core.plugin_hub import ExtensionHub

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
        list_sources,
        load_triggers,
        list_triggers,
        validate_triggers,
        inspect_changelog,
        watch_status,
        dryrun_trigger,
        refresh_worldwide_catalog,
    ):
        server.tool()(fn)
    server.resource("gispulse://capabilities")(resource_capabilities)
    server.resource("gispulse://templates")(resource_templates)
    server.resource("gispulse://sources")(resource_sources)
    # Resource templates — the ``{path}`` placeholder makes FastMCP treat
    # these as parameterised resources, so an MCP client can read e.g.
    # ``gispulse://triggers/configs/parcels.yaml``. The path is still
    # workdir-scoped inside the handler (#204).
    server.resource("gispulse://triggers/{path}")(resource_triggers)
    server.resource("gispulse://changelog/{path}")(resource_changelog)


def register_plugin_mcp_surface(server: Any) -> None:
    """Install MCP tools/resources provided through ExtensionHub entry-points."""
    hub = ExtensionHub.get()
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
# Dataset / pipeline tools (read-only, workdir-scoped)
# ===========================================================================


def inspect_dataset(path: str) -> dict[str, Any]:
    """List the layers of a GeoPackage file without loading its features.

    The ``path`` is bounded to the MCP workdir (#204).

    Args:
        path: Path to a ``.gpkg`` file, inside the MCP workdir.

    Returns:
        Dict with ``path`` and ``layers`` (list of layer names), or an
        ``error`` key when the file is missing, out of scope, or
        unreadable.
    """
    try:
        gpkg = resolve_in_workdir(path)
    except WorkdirError as exc:
        return {"error": str(exc)}
    if not gpkg.is_file():
        return {"error": f"File not found: '{path}'"}
    try:
        layers = _app.list_layers(gpkg)
    except Exception as exc:
        return {"error": f"Cannot read GeoPackage layers: {exc}"}
    return {"path": str(gpkg), "layers": layers}


def validate_pipeline(path: str) -> dict[str, Any]:
    """Schema-validate a pipeline JSON file (v1 or v2 grammar).

    The ``path`` is bounded to the MCP workdir (#204).

    Args:
        path: Path to a pipeline / rules JSON file, inside the workdir.

    Returns:
        Dict with ``valid`` (bool). On success it also carries ``name``
        and ``steps`` (count); on failure, ``errors`` (list of strings).
    """
    try:
        pipeline = resolve_in_workdir(path)
    except WorkdirError as exc:
        return {"valid": False, "errors": [str(exc)]}
    try:
        spec = _app.load_pipeline(pipeline, validate=True)
    except FileNotFoundError as exc:
        return {"valid": False, "errors": [str(exc)]}
    except ValueError as exc:
        return {"valid": False, "errors": str(exc).splitlines()}
    except json.JSONDecodeError as exc:
        return {"valid": False, "errors": [f"Invalid JSON: {exc}"]}
    return {"valid": True, "name": spec.name, "steps": len(spec.steps)}


# ===========================================================================
# Trigger / change-log tools (#202)
# ===========================================================================


def _trigger_summary(config: Any) -> dict[str, Any]:
    """Build the JSON-safe summary dict shared by ``load_triggers`` and the
    ``gispulse://triggers/{path}`` resource."""
    triggers: list[dict[str, Any]] = []
    for entry in config.triggers:
        if entry.on is not None:
            when: Any = {"source_changed": entry.on.source_changed}
            action_types = [a.type for a in entry.actions]
        else:
            when = list(entry.when)
            action_types = [a.type for a in entry.actions]
        triggers.append(
            {
                "id": entry.name,
                "table": entry.table,
                "when": when,
                "action_types": action_types,
                "enabled": entry.enabled,
            }
        )
    return {
        "gpkg": str(config.gpkg),
        "trigger_count": len(config.triggers),
        "triggers": triggers,
    }


def load_triggers(path: str) -> dict[str, Any]:
    """Load a ``triggers.yaml`` config and return a structured summary.

    The ``path`` is bounded to the MCP workdir (#204).

    Args:
        path: Path to a YAML triggers config inside the workdir.

    Returns:
        ``{path, gpkg, trigger_count, triggers: [...]}`` — or an
        ``error`` key on a traversal / schema violation.
    """
    try:
        cfg_path = resolve_in_workdir(path)
        config = _app.load_trigger_config(cfg_path)
    except Exception as exc:  # noqa: BLE001 - uniform error shape
        return {"error": str(exc)}
    summary = _trigger_summary(config)
    summary["path"] = str(cfg_path)
    return summary


def list_triggers(path: str) -> dict[str, Any]:
    """Return the detailed trigger list of a ``triggers.yaml`` config.

    The ``path`` is bounded to the MCP workdir (#204).

    Args:
        path: Path to a YAML triggers config inside the workdir.

    Returns:
        ``{path, gpkg, trigger_count, triggers: [...]}`` where each
        trigger carries its predicate and per-action config — or an
        ``error`` key.
    """
    try:
        cfg_path = resolve_in_workdir(path)
        config = _app.load_trigger_config(cfg_path)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    triggers: list[dict[str, Any]] = []
    for entry in config.triggers:
        actions = [
            {k: v for k, v in a.model_dump().items() if v is not None}
            for a in entry.actions
        ]
        triggers.append(
            {
                "id": entry.name,
                "table": entry.table,
                "kind": entry.kind,
                "pk_col": entry.pk_col,
                "when": list(entry.when),
                "predicate": entry.predicate,
                "source_changed": (
                    entry.on.source_changed if entry.on is not None else None
                ),
                "actions": actions,
                "enabled": entry.enabled,
            }
        )
    return {
        "path": str(cfg_path),
        "gpkg": str(config.gpkg),
        "trigger_count": len(triggers),
        "triggers": triggers,
    }


def validate_triggers(path: str, gpkg: str | None = None) -> dict[str, Any]:
    """Structurally validate a ``triggers.yaml`` against its GeoPackage.

    Runs the schema check *and* opens the GPKG to confirm every DML
    trigger references a real layer. Both ``path`` and the optional
    ``gpkg`` override are bounded to the MCP workdir (#204).

    Args:
        path: Path to a YAML triggers config inside the workdir.
        gpkg: Optional GPKG path that wins over the config's ``gpkg:``.

    Returns:
        ``{valid: bool, errors: [...]}``.
    """
    try:
        cfg_path = resolve_in_workdir(path)
        gpkg_override = resolve_in_workdir(gpkg) if gpkg else None
    except WorkdirError as exc:
        return {"valid": False, "errors": [str(exc)]}
    errors = _app.validate_trigger_config(cfg_path, gpkg_override=gpkg_override)
    return {"valid": not errors, "errors": errors}


def inspect_changelog(gpkg: str, limit: int = 50) -> dict[str, Any]:
    """Inspect the ``_gispulse_change_log`` of a GeoPackage.

    The ``gpkg`` path is bounded to the MCP workdir (#204).

    Args:
        gpkg:  Path to a ``.gpkg`` file inside the workdir.
        limit: Max recent change-log rows to return (1..10000).

    Returns:
        ``{tracked, pending, latest_seq, recent: [...]}`` — ``tracked``
        is ``False`` when change-tracking has not been installed. An
        ``error`` key wraps a traversal violation or an unreadable file.
    """
    try:
        gpkg_path = resolve_in_workdir(gpkg)
    except WorkdirError as exc:
        return {"error": str(exc)}
    try:
        return _app.changelog_status(gpkg_path, limit=limit)
    except Exception as exc:  # noqa: BLE001 - report cleanly to the model
        return {"error": f"Cannot read change-log: {exc}"}


def watch_status(gpkg: str) -> dict[str, Any]:
    """Report change-tracking status for a GeoPackage.

    Combines the installed-trigger scan (which layers are tracked) with
    the change-log snapshot (how much is pending). The ``gpkg`` path is
    bounded to the MCP workdir (#204).

    Args:
        gpkg: Path to a ``.gpkg`` file inside the workdir.

    Returns:
        ``{gpkg, tracked_layers: {layer: [ops]}, pending, latest_seq}``
        — or an ``error`` key.
    """
    try:
        gpkg_path = resolve_in_workdir(gpkg)
    except WorkdirError as exc:
        return {"error": str(exc)}
    try:
        tracked = _app.tracked_layers(gpkg_path)
        cl = _app.changelog_status(gpkg_path, limit=1)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Cannot read watch status: {exc}"}
    return {
        "gpkg": str(gpkg_path),
        "tracked_layers": tracked,
        "pending": cl["pending"],
        "latest_seq": cl["latest_seq"],
    }


def dryrun_trigger(path: str, gpkg: str | None = None) -> dict[str, Any]:
    """Evaluate a ``triggers.yaml`` against the live change-log — no effects.

    Builds the real headless runtime but injects collecting no-op
    executors for SQL writes and webhooks, and neutralises the change-log
    ack. The trigger predicates and DSL run for real; the *effects* are
    captured instead of applied, so a subsequent ``gispulse watch`` still
    sees the same pending rows. See :mod:`gispulse.adapters.mcp.dryrun`
    for the side-effect-free contract.

    Both ``path`` and the optional ``gpkg`` override are bounded to the
    MCP workdir (#204).

    Args:
        path: Path to a YAML triggers config inside the workdir.
        gpkg: Optional GPKG path that wins over the config's ``gpkg:``.

    Returns:
        ``{gpkg, trigger_count, rows_evaluated, sql_actions,
        webhook_actions}`` — or an ``error`` key.
    """
    from gispulse.adapters.mcp.dryrun import dryrun_trigger_config

    try:
        cfg_path = resolve_in_workdir(path)
        gpkg_override = resolve_in_workdir(gpkg) if gpkg else None
    except WorkdirError as exc:
        return {"error": str(exc)}
    try:
        return dryrun_trigger_config(cfg_path, gpkg_override=gpkg_override)
    except Exception as exc:  # noqa: BLE001 - report cleanly to the model
        return {"error": f"Dry-run failed: {exc}"}


# ===========================================================================
# Plugin / source tools
# ===========================================================================


def list_plugins() -> list[dict[str, Any]]:
    """List the plugins discovered by the unified ExtensionHub.

    Returns:
        One dict per plugin record — sources, capabilities, sinks,
        templates and extensions — as produced by ``PluginRecord.as_dict``.
    """
    return [rec.as_dict() for rec in _app.list_plugins()]


def list_sources() -> list[dict[str, Any]]:
    """List the ETL data-source plugins registered with the ExtensionHub.

    Data sources are the *extract* leg of the ETL plugin model — plugins
    published under the ``gispulse.data_sources`` entry-point group.

    Returns:
        One dict per data-source plugin record.
    """
    return _app.list_sources()


def refresh_worldwide_catalog() -> dict[str, Any]:
    """Probe data.gouv.fr for stale French entries of the worldwide catalogue.

    Walks the worldwide aggregator catalogue (EPIC #226) and, for every
    entry that declares a ``datagouv_dataset`` slug, queries data.gouv.fr
    for its current publication timestamp — reporting which curated
    ``revision_token`` values have drifted (A13, #239).

    The probe is **read-only and idempotent**: it mutates nothing and two
    calls against an unchanged remote return the same report. The MCP
    server exposes no URL argument, so the probe is pinned to the
    data.gouv.fr API host.

    Returns:
        ``{catalog, checked, entries: [...], stale: [...]}`` — each entry
        record carries ``current_token`` / ``datagouv_revision`` /
        ``up_to_date``, or an ``error`` key when its slug failed to
        resolve.
    """
    from gispulse.plugins.datagouv_refresh import refresh_datagouv_entries

    try:
        return refresh_datagouv_entries()
    except Exception as exc:  # noqa: BLE001 - report cleanly to the model
        return {"error": f"Catalogue refresh failed: {exc}"}


# ===========================================================================
# MCP Resources
# ===========================================================================


def resource_capabilities() -> str:
    """MCP resource: all registered GISPulse capabilities (JSON)."""
    return json.dumps(_app.list_capabilities(), indent=2)


def resource_templates() -> str:
    """MCP resource: the built-in pipeline templates (JSON)."""
    return json.dumps(_app.list_templates(), indent=2)


def resource_sources() -> str:
    """MCP resource: the registered ETL data-source plugins (JSON)."""
    return json.dumps(_app.list_sources(), indent=2)


def resource_triggers(path: str) -> str:
    """MCP resource template: summary of a ``triggers.yaml`` config (JSON).

    Bound as ``gispulse://triggers/{path}``. The ``path`` is workdir-scoped
    exactly like the :func:`load_triggers` tool.
    """
    return json.dumps(load_triggers(path), indent=2, default=str)


def resource_changelog(path: str) -> str:
    """MCP resource template: change-log status of a GeoPackage (JSON).

    Bound as ``gispulse://changelog/{path}``. The ``path`` is workdir-scoped
    exactly like the :func:`inspect_changelog` tool.
    """
    return json.dumps(inspect_changelog(path), indent=2, default=str)


# Module-level compatibility instance used by existing imports / launchers.
mcp = create_mcp_server()
