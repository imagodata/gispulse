"""
JSON rules loader for GISPulse Phase 1 CLI.

Loads a list of Rule objects from a JSON file. Expected format::

    [
        {
            "name": "buffer_50m",
            "capability": "buffer",
            "config": {"distance": 50},
            "enabled": true
        },
        {
            "name": "filter_large",
            "capability": "filter",
            "config": {"expression": "area > 1000"},
            "enabled": true
        }
    ]
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

from core.models import Rule

_KNOWN_RULE_KEYS = {"name", "description", "capability", "config", "enabled", "scope", "id", "order"}


def load_rules(path: str | Path, *, validate: bool = True) -> list[Rule]:
    """Load Rule objects from a JSON file.

    Args:
        path:     Path to the JSON rules file.
        validate: If True (default), validate the JSON against the v1
                  schema before parsing.

    Returns:
        List of Rule domain objects, ordered by their position in the file
        (each rule gets an ``order`` key injected in config if missing).

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If the JSON structure is invalid or fails schema validation.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Rules file not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(raw, list):
        raise ValueError(f"Rules file must contain a JSON array, got {type(raw).__name__}")

    if validate:
        from core.pipeline_schema import validate_pipeline_json

        errors = validate_pipeline_json(raw)
        if errors:
            msg = f"Rules schema validation failed for {Path(path).name}:\n"
            msg += "\n".join(f"  - {e}" for e in errors[:20])
            raise ValueError(msg)

    rules: list[Rule] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Rule #{i} must be a JSON object, got {type(entry).__name__}")

        unknown_keys = set(entry.keys()) - _KNOWN_RULE_KEYS
        if unknown_keys:
            warnings.warn(
                f"Rule #{i} ('{entry.get('name', '')}') has unknown fields: "
                f"{sorted(unknown_keys)}. These will be ignored.",
                stacklevel=2,
            )

        config = entry.get("config", {})
        # Extract order from config (legacy) or top-level, defaulting to position
        order = entry.get("order", config.pop("order", i))

        rule = Rule(
            name=entry.get("name", f"rule_{i}"),
            description=entry.get("description", ""),
            capability=entry.get("capability", ""),
            config=config,
            enabled=entry.get("enabled", True),
            order=order,
        )
        rules.append(rule)

    return rules
