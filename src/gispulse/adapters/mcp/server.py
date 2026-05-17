"""
GISPulse MCP server — FastMCP facade exposing GISPulse to LLM agents.

Tools
-----
Capabilities:
  list_capabilities()          -> list of capability metadata dicts
  get_capability_info(name)    -> single capability detail dict

Rules:
  create_rule(...)             -> creates and stores a Rule, returns its id
  list_rules()                 -> lists all stored rules
  validate_rule(rule_id)       -> validates a rule, returns result dict
  delete_rule(rule_id)         -> removes a rule, returns bool

Datasets:
  list_datasets()              -> lists loaded datasets
  load_gpkg(path, name)        -> loads a GPKG into the engine (requires fiona)

Jobs:
  run_job(name, dataset_id, rule_ids) -> executes a job, returns status dict

Resources
---------
  gispulse://capabilities      -> JSON list of capabilities
  gispulse://rules             -> JSON list of rules
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

try:
    from fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "fastmcp is required for the MCP facade. "
        "Install it with: pip install fastmcp"
    ) from exc

from gispulse.capabilities import list_all as _list_all_capabilities
from gispulse.capabilities.registry import REGISTRY
from gispulse.core.plugin_hub import PluginHub
from gispulse.core.models import Dataset, Job, JobStatus, Rule
from gispulse.core.logging import get_logger
from gispulse.persistence.repository import InMemoryRepository
from gispulse.rules.engine import RuleEngine
from gispulse.rules.validation import validate_rule as _validate_rule
from gispulse.orchestration.runner import JobRunner

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (in-memory repositories for the MCP session)
# ---------------------------------------------------------------------------

_rule_repo: InMemoryRepository[Rule] = InMemoryRepository()
_dataset_repo: InMemoryRepository[Dataset] = InMemoryRepository()
_job_repo: InMemoryRepository[Job] = InMemoryRepository()

_rule_engine = RuleEngine(repository=_rule_repo)
_job_runner = JobRunner(repository=_rule_repo, rule_engine=_rule_engine)

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
        create_rule,
        list_rules,
        validate_rule,
        delete_rule,
        list_datasets,
        load_gpkg,
        run_job,
    ):
        server.tool()(fn)
    server.resource("gispulse://capabilities")(resource_capabilities)
    server.resource("gispulse://rules")(resource_rules)


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
            log.warning("plugin_mcp_resources_failed", plugin=factory.name, error=str(exc))


# ===========================================================================
# Capability tools
# ===========================================================================


def list_capabilities() -> list[dict[str, Any]]:
    """List all registered GISPulse capabilities with their schemas.

    Returns:
        List of dicts with keys ``name``, ``description``, ``schema``.
    """
    return _list_all_capabilities()


def get_capability_info(name: str) -> dict[str, Any]:
    """Return detail for a single capability.

    Args:
        name: Registered capability name (e.g. 'buffer', 'filter').

    Returns:
        Dict with keys ``name``, ``description``, ``schema``.
        Contains an ``error`` key if the capability is not found.
    """
    if name not in REGISTRY:
        registered = sorted(REGISTRY.keys())
        return {"error": f"Capability '{name}' not found.", "available": registered}

    cls = REGISTRY[name]
    instance = cls()
    return {
        "name": cls.name,
        "description": cls.description,
        "schema": instance.get_schema(),
    }


# ===========================================================================
# Rule tools
# ===========================================================================


def create_rule(
    name: str,
    capability: str,
    config: dict[str, Any],
    description: str = "",
) -> dict[str, Any]:
    """Create and store a Rule in the session repository.

    Args:
        name:        Human-readable rule name.
        capability:  Registered capability name to invoke (e.g. 'buffer').
        config:      Capability-specific parameter dict (e.g. {'distance': 100}).
        description: Optional rule description.

    Returns:
        Dict with ``rule_id`` (str UUID) and ``name`` on success,
        or ``error`` key on failure.
    """
    try:
        rule = Rule(
            name=name,
            capability=capability,
            config=config,
            description=description,
        )
        _rule_repo.save(rule)
        log.info("mcp_rule_created", rule_id=str(rule.id), name=name)
        return {"rule_id": str(rule.id), "name": rule.name}
    except Exception as exc:  # pragma: no cover
        log.error("mcp_create_rule_error", error=str(exc))
        return {"error": str(exc)}


def list_rules() -> list[dict[str, Any]]:
    """List all rules stored in the session repository.

    Returns:
        List of dicts with keys ``rule_id``, ``name``, ``capability``,
        ``description``, ``enabled``.
    """
    return [
        {
            "rule_id": str(rule.id),
            "name": rule.name,
            "capability": rule.capability,
            "description": rule.description,
            "config": rule.config,
            "enabled": rule.enabled,
        }
        for rule in _rule_repo.list_all()
    ]


def validate_rule(rule_id: str) -> dict[str, Any]:
    """Validate a stored rule against its capability's schema.

    Args:
        rule_id: String UUID of the rule to validate.

    Returns:
        Dict with keys ``valid`` (bool), ``errors`` (list of dicts with
        ``field`` and ``message``), or an ``error`` key if rule not found.
    """
    try:
        uid = UUID(rule_id)
    except ValueError:
        return {"error": f"Invalid UUID format: '{rule_id}'"}

    rule = _rule_repo.get(uid)
    if rule is None:
        return {"error": f"Rule '{rule_id}' not found."}

    result = _validate_rule(rule)
    return {
        "valid": result.valid,
        "errors": [
            {"field": err.field, "message": err.message}
            for err in result.errors
        ],
    }


def delete_rule(rule_id: str) -> dict[str, Any]:
    """Delete a stored rule from the session repository.

    Args:
        rule_id: String UUID of the rule to delete.

    Returns:
        Dict with ``deleted`` (bool) and ``rule_id``, or ``error`` key if
        UUID is malformed.
    """
    try:
        uid = UUID(rule_id)
    except ValueError:
        return {"error": f"Invalid UUID format: '{rule_id}'"}

    deleted = _rule_repo.delete(uid)
    log.info("mcp_rule_deleted", rule_id=rule_id, deleted=deleted)
    return {"deleted": deleted, "rule_id": rule_id}


# ===========================================================================
# Dataset tools
# ===========================================================================


def list_datasets() -> list[dict[str, Any]]:
    """List all datasets loaded in the current session.

    Returns:
        List of dicts with keys ``dataset_id``, ``name``, ``source_path``,
        ``crs``, ``format``.
    """
    return [
        {
            "dataset_id": str(ds.id),
            "name": ds.name,
            "source_path": ds.source_path,
            "crs": ds.crs,
            "format": ds.format,
        }
        for ds in _dataset_repo.list_all()
    ]


def load_gpkg(path: str, name: str = "") -> dict[str, Any]:
    """Load a GeoPackage file into the session engine.

    Requires fiona to be installed. The dataset is registered in the
    in-memory repository; individual layers are listed in the metadata.

    Args:
        path: Absolute path to the .gpkg file.
        name: Optional display name (defaults to the file basename).

    Returns:
        Dict with ``dataset_id``, ``name``, ``layers`` (list of layer names),
        or ``error`` key on failure.
    """
    try:
        import pyogrio  # noqa: PLC0415
    except ImportError:
        return {"error": "pyogrio is required to load GPKG files. Install it with: pip install pyogrio"}

    import os

    if not os.path.isfile(path):
        return {"error": f"File not found: '{path}'"}

    try:
        layer_info = pyogrio.list_layers(path)
        layer_names: list[str] = [row[0] for row in layer_info]
    except Exception as exc:
        return {"error": f"Cannot read GPKG layers: {exc}"}

    display_name = name or os.path.basename(path)
    dataset = Dataset(
        name=display_name,
        source_path=path,
        format="GPKG",
        metadata={"layers": layer_names},
    )
    _dataset_repo.save(dataset)
    log.info(
        "mcp_gpkg_loaded",
        dataset_id=str(dataset.id),
        name=display_name,
        layers=layer_names,
    )
    return {
        "dataset_id": str(dataset.id),
        "name": display_name,
        "layers": layer_names,
    }


# ===========================================================================
# Job tools
# ===========================================================================


def run_job(
    name: str,
    dataset_id: str,
    rule_ids: list[str],
) -> dict[str, Any]:
    """Execute a GISPulse job applying rules to a loaded dataset.

    The dataset must have been loaded with ``load_gpkg``. Each rule in
    ``rule_ids`` must exist in the session repository. Rules are applied in
    order using the rule engine.

    Args:
        name:       Human-readable job name.
        dataset_id: String UUID of a loaded dataset.
        rule_ids:   List of string UUIDs identifying rules to apply in order.

    Returns:
        Dict with ``job_id``, ``status``, ``rules_applied`` on success,
        or ``error`` key on failure.
    """
    try:
        ds_uid = UUID(dataset_id)
    except ValueError:
        return {"error": f"Invalid dataset_id UUID: '{dataset_id}'"}

    dataset = _dataset_repo.get(ds_uid)
    if dataset is None:
        return {"error": f"Dataset '{dataset_id}' not found. Load it first with load_gpkg."}

    if dataset.source_path is None:
        return {"error": f"Dataset '{dataset_id}' has no source path."}

    # Validate all rule IDs before running
    for rid in rule_ids:
        try:
            uid = UUID(rid)
        except ValueError:
            return {"error": f"Invalid rule_id UUID: '{rid}'"}
        if _rule_repo.get(uid) is None:
            return {"error": f"Rule '{rid}' not found."}

    # Load first layer of the GPKG for processing
    try:
        import geopandas as gpd  # noqa: PLC0415

        layers: list[str] = dataset.metadata.get("layers", [])
        if not layers:
            return {"error": "Dataset has no layers to process."}
        gdf = gpd.read_file(dataset.source_path, layer=layers[0])
    except Exception as exc:
        return {"error": f"Failed to read dataset: {exc}"}

    job = Job(
        name=name,
        dataset_id=ds_uid,
        parameters={"rule_ids": rule_ids},
    )
    _job_repo.save(job)

    try:
        updated_job, _ = _job_runner.run(job, gdf)
        return {
            "job_id": str(updated_job.id),
            "status": updated_job.status.value,
            "rules_applied": len(rule_ids),
        }
    except Exception as exc:
        return {
            "job_id": str(job.id),
            "status": JobStatus.FAILED.value,
            "error": str(exc),
        }


# ===========================================================================
# MCP Resources
# ===========================================================================


def resource_capabilities() -> str:
    """MCP resource: list of all registered GISPulse capabilities (JSON)."""
    return json.dumps(_list_all_capabilities(), indent=2)


def resource_rules() -> str:
    """MCP resource: list of all rules stored in the session (JSON)."""
    rules = [
        {
            "rule_id": str(rule.id),
            "name": rule.name,
            "capability": rule.capability,
            "description": rule.description,
            "config": rule.config,
            "enabled": rule.enabled,
        }
        for rule in _rule_repo.list_all()
    ]
    return json.dumps(rules, indent=2)


# Module-level compatibility instance used by existing imports.
mcp = create_mcp_server()
