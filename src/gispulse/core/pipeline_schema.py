"""JSON Schema definitions for GISPulse pipeline v1 and v2 formats.

Provides schema dicts and a :func:`validate_pipeline_json` function that
validates raw parsed JSON against the appropriate schema version.

Usage::

    from gispulse.core.pipeline_schema import validate_pipeline_json

    raw = json.loads(path.read_text())
    errors = validate_pipeline_json(raw)
    if errors:
        raise ValueError(f"Pipeline validation errors: {errors}")
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Shared definitions (predicates)
# ---------------------------------------------------------------------------

_PREDICATE_DEFS = {
    "attr_predicate": {
        "type": "object",
        "required": ["type", "field", "op"],
        "properties": {
            "type": {"const": "attr"},
            "field": {"type": "string", "minLength": 1},
            "op": {
                "type": "string",
                "enum": ["eq", "neq", "gt", "lt", "gte", "lte", "in", "like"],
            },
            "value": {},
        },
        "additionalProperties": False,
    },
    "geom_predicate": {
        "type": "object",
        "required": ["type", "op"],
        "properties": {
            "type": {"const": "geom"},
            "op": {
                "type": "string",
                "enum": [
                    "intersects", "within", "contains", "crosses",
                    "overlaps", "touches", "distance_lt", "distance_gt",
                    "disjoint",
                ],
            },
            "ref_table": {"type": "string"},
            "ref_filter": {"type": "string"},
            "ref_geom_col": {"type": "string"},
            "distance": {"type": "number"},
            "buffer_m": {"type": "number"},
        },
        "additionalProperties": False,
    },
    "compound_predicate": {
        "type": "object",
        "required": ["type", "logic", "predicates"],
        "properties": {
            "type": {"const": "compound"},
            "logic": {"type": "string", "enum": ["AND", "OR", "NOT"]},
            "predicates": {
                "type": "array",
                "items": {"$ref": "#/$defs/any_predicate"},
                "minItems": 1,
            },
        },
        "additionalProperties": False,
    },
    "any_predicate": {
        "oneOf": [
            {"$ref": "#/$defs/attr_predicate"},
            {"$ref": "#/$defs/geom_predicate"},
            {"$ref": "#/$defs/compound_predicate"},
        ],
    },
}

# ---------------------------------------------------------------------------
# V1 schema — flat array of rules
# ---------------------------------------------------------------------------

SCHEMA_V1: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://gispulse.dev/schemas/rules-v1.json",
    "title": "GISPulse Rules v1",
    "description": "Flat array of processing rules (legacy format).",
    "type": "array",
    "items": {
        "type": "object",
        "required": ["capability"],
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "description": "Unique rule identifier.",
            },
            "description": {"type": "string"},
            "capability": {
                "type": "string",
                "minLength": 1,
                "description": "Registered capability name.",
            },
            "config": {
                "type": "object",
                "description": "Capability-specific parameters.",
            },
            "enabled": {"type": "boolean", "default": True},
            "order": {"type": "integer"},
            "id": {"type": "string"},
            "scope": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

# ---------------------------------------------------------------------------
# V2 schema — pipeline with steps, triggers, ref_layers
# ---------------------------------------------------------------------------

SCHEMA_V2: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://gispulse.dev/schemas/pipeline-v2.json",
    "title": "GISPulse Pipeline v2",
    "description": "Declarative pipeline with DAG steps, triggers, and reference layers.",
    "type": "object",
    "required": ["version", "steps"],
    "properties": {
        "version": {"const": 2},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {"$ref": "#/$defs/step"},
            "minItems": 1,
            "description": "Ordered list of processing steps.",
        },
        "triggers": {
            "type": "array",
            "items": {"$ref": "#/$defs/trigger"},
        },
        "ref_layers": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Named reference layers: alias -> source path.",
        },
    },
    "additionalProperties": False,
    "$defs": {
        **_PREDICATE_DEFS,
        "step": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "type": {
                    "type": "string",
                    "enum": ["capability", "filter", "spatial_op", "custom_sql"],
                    "default": "capability",
                },
                "capability": {"type": "string"},
                "params": {"type": "object"},
                "config": {
                    "type": "object",
                    "description": "Alias for params (v1 compat).",
                },
                "name": {
                    "type": "string",
                    "description": "Alias for id (v1 compat).",
                },
                "input": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "null"},
                    ],
                },
                "when": {"$ref": "#/$defs/any_predicate"},
                "enabled": {"type": "boolean", "default": True},
                "order": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "trigger": {
            "type": "object",
            "required": ["on", "then"],
            "properties": {
                "on": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Event descriptor: dml:table:OPS | schedule:cron | manual.",
                },
                "when": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/any_predicate"},
                },
                "then": {
                    "type": "string",
                    "enum": ["run_pipeline", "notify", "webhook", "log_event"],
                },
                "then_config": {"type": "object"},
            },
            "additionalProperties": False,
        },
    },
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_pipeline_json(raw: Any) -> list[str]:
    """Validate raw parsed JSON against the appropriate pipeline schema.

    Returns a list of human-readable error strings (empty = valid).
    Uses ``jsonschema`` if available, otherwise falls back to basic
    structural checks.

    Args:
        raw: Parsed JSON data (dict for v2, list for v1).
    """
    schema = _pick_schema(raw)
    if schema is None:
        return [
            f"Pipeline must be a JSON object (v2) or array (v1), got {type(raw).__name__}"
        ]

    try:
        import jsonschema
        errors: list[str] = []
        validator = jsonschema.Draft202012Validator(schema)
        for err in sorted(validator.iter_errors(raw), key=lambda e: list(e.absolute_path)):
            path = ".".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append(f"{path}: {err.message}")
        return errors
    except ImportError:
        return _validate_basic(raw, schema)


def _pick_schema(raw: Any) -> dict[str, Any] | None:
    """Select schema based on data type and version field."""
    if isinstance(raw, list):
        return SCHEMA_V1
    if isinstance(raw, dict):
        return SCHEMA_V2
    return None


def _validate_basic(raw: Any, schema: dict[str, Any]) -> list[str]:
    """Lightweight structural validation without jsonschema dependency."""
    errors: list[str] = []

    if isinstance(raw, list):
        # V1: array of rules
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                errors.append(f"[{i}]: must be an object, got {type(item).__name__}")
                continue
            if "name" not in item:
                errors.append(f"[{i}]: missing required field 'name'")
            if "capability" not in item:
                errors.append(f"[{i}]: missing required field 'capability'")

    elif isinstance(raw, dict):
        # V2: pipeline object
        if "version" not in raw:
            errors.append("(root): missing required field 'version'")
        elif raw["version"] != 2:
            errors.append(f"(root): version must be 2, got {raw['version']}")
        if "steps" not in raw:
            errors.append("(root): missing required field 'steps'")
        elif not isinstance(raw["steps"], list):
            errors.append("(root): 'steps' must be an array")
        elif len(raw["steps"]) == 0:
            errors.append("(root): 'steps' must contain at least one step")
        else:
            for i, step in enumerate(raw["steps"]):
                if not isinstance(step, dict):
                    errors.append(f"steps[{i}]: must be an object")
                    continue
                if "id" not in step and "name" not in step:
                    errors.append(f"steps[{i}]: missing required field 'id'")

        for i, trigger in enumerate(raw.get("triggers", [])):
            if not isinstance(trigger, dict):
                errors.append(f"triggers[{i}]: must be an object")
                continue
            if "on" not in trigger:
                errors.append(f"triggers[{i}]: missing required field 'on'")
            if "then" not in trigger:
                errors.append(f"triggers[{i}]: missing required field 'then'")

    return errors
