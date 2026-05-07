"""Project import/export — YAML/JSON ↔ GPKG project file.

Exports all declarative config (rules, triggers, ref_layers, relations,
scenarios) from a GPKG project into a human-readable YAML or JSON file
suitable for version control.

Usage::

    from persistence.project_io import export_project, import_project

    # Export
    export_project("project.gpkg", "project.yaml")

    # Import (merges into existing GPKG)
    import_project("project.yaml", "project.gpkg")
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields as dc_fields
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from core.models import (
    RefLayerDef,
    Rule,
    Scenario,
    TableRelation,
    Trigger,
)
from persistence.schema import SCHEMA_VERSION

# Optional YAML support — fall back to JSON if not installed
try:
    import yaml

    _YAML_AVAILABLE = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    _YAML_AVAILABLE = False


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _to_dict(obj: Any) -> dict[str, Any]:
    """Serialise a dataclass to a JSON-safe dict."""
    raw = asdict(obj)
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, UUID):
            cleaned[key] = str(value)
        elif isinstance(value, datetime):
            cleaned[key] = value.isoformat()
        elif isinstance(value, list):
            cleaned[key] = [
                _to_dict(item) if hasattr(item, "__dataclass_fields__") else
                (str(item) if isinstance(item, UUID) else item)
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _strip_defaults(data: dict[str, Any], model_cls: type) -> dict[str, Any]:
    """Remove fields that match the dataclass default values."""
    defaults: dict[str, Any] = {}
    for f in dc_fields(model_cls):
        if f.default is not f.default_factory:  # type: ignore[attr-defined]
            if f.default is not f.default:
                defaults[f.name] = f.default
    return {k: v for k, v in data.items() if k not in defaults or v != defaults.get(k)}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_project(
    gpkg_path: str | Path,
    output_path: str | Path,
    *,
    format: str = "auto",
    include_ids: bool = True,
) -> Path:
    """Export project config from a GPKG file to YAML or JSON.

    Args:
        gpkg_path:   Path to the source GPKG project file.
        output_path: Destination file path (.yaml, .yml, or .json).
        format:      ``"yaml"``, ``"json"``, or ``"auto"`` (detect from extension).
        include_ids: Whether to include UUIDs in the output.

    Returns:
        Path to the written output file.
    """
    import sqlite3

    gpkg_path = Path(gpkg_path)
    output_path = Path(output_path)

    if format == "auto":
        if output_path.suffix in (".yaml", ".yml"):
            format = "yaml"
        else:
            format = "json"

    from persistence.gpkg_connection import connect_gpkg

    conn = connect_gpkg(gpkg_path, row_factory=sqlite3.Row)

    project: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "source": gpkg_path.name,
    }

    # Export each domain table
    for table_key, table_name, model_cls in [
        ("rules", "_gispulse_rules", Rule),
        ("triggers", "_gispulse_triggers", Trigger),
        ("ref_layers", "_gispulse_ref_layers", RefLayerDef),
        ("relations", "_gispulse_table_relations", TableRelation),
        ("scenarios", "_gispulse_scenarios", Scenario),
    ]:
        try:
            rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
            items = []
            for row in rows:
                item = dict(row)
                # Parse JSON columns
                for col in ("config", "conditions", "predicates", "actions",
                            "metadata", "computed_fields", "spatial_config",
                            "jobs", "rules", "triggers", "graph"):
                    if col in item and isinstance(item[col], str):
                        try:
                            item[col] = json.loads(item[col])
                        except (json.JSONDecodeError, TypeError):
                            pass
                # Strip IDs if requested
                if not include_ids:
                    item.pop("id", None)
                items.append(item)
            if items:
                project[table_key] = items
        except sqlite3.OperationalError:
            pass  # Table doesn't exist yet

    conn.close()

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if format == "yaml":
        if not _YAML_AVAILABLE:
            raise ImportError(
                "PyYAML is required for YAML export. Install with: pip install pyyaml"
            )
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(project, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    else:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(project, f, indent=2, default=str, ensure_ascii=False)

    return output_path


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def import_project(
    input_path: str | Path,
    gpkg_path: str | Path,
    *,
    merge: bool = True,
) -> dict[str, int]:
    """Import project config from YAML/JSON into a GPKG project file.

    Args:
        input_path: Path to the YAML or JSON config file.
        gpkg_path:  Path to the target GPKG project file (created if absent).
        merge:      If True, merges with existing data (upsert by id/name).
                    If False, replaces all config tables.

    Returns:
        Dict mapping table names to the number of rows imported.
    """
    import sqlite3

    input_path = Path(input_path)
    gpkg_path = Path(gpkg_path)

    # Parse input
    raw = input_path.read_text(encoding="utf-8")
    if input_path.suffix in (".yaml", ".yml"):
        if not _YAML_AVAILABLE:
            raise ImportError("PyYAML is required for YAML import.")
        project = yaml.safe_load(raw)
    else:
        project = json.loads(raw)

    if not isinstance(project, dict):
        raise ValueError("Project file must be a JSON/YAML object")

    # Open/bootstrap GPKG
    from persistence.gpkg_schema import bootstrap_gpkg_project

    from persistence.gpkg_connection import connect_gpkg

    conn = connect_gpkg(gpkg_path, row_factory=sqlite3.Row)
    bootstrap_gpkg_project(conn)

    stats: dict[str, int] = {}

    for table_key, table_name in [
        ("rules", "_gispulse_rules"),
        ("triggers", "_gispulse_triggers"),
        ("ref_layers", "_gispulse_ref_layers"),
        ("relations", "_gispulse_table_relations"),
        ("scenarios", "_gispulse_scenarios"),
    ]:
        items = project.get(table_key, [])
        if not items:
            continue

        if not merge:
            conn.execute(f"DELETE FROM {table_name}")

        count = 0
        for item in items:
            # Serialise nested dicts/lists back to JSON strings
            serialised = {}
            for k, v in item.items():
                if isinstance(v, (dict, list)):
                    serialised[k] = json.dumps(v, default=str)
                elif isinstance(v, bool):
                    serialised[k] = int(v)
                else:
                    serialised[k] = v

            columns = list(serialised.keys())
            placeholders = ", ".join("?" for _ in columns)
            col_names = ", ".join(columns)
            updates = ", ".join(f"{c} = ?" for c in columns)
            values = list(serialised.values())

            sql = (
                f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}"
            )
            try:
                conn.execute(sql, tuple(values + values))
                count += 1
            except sqlite3.OperationalError:
                pass  # Column mismatch — skip silently

        stats[table_key] = count

    conn.commit()
    conn.close()
    return stats
