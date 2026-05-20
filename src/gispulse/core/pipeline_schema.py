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
# Schema v3 — ADR 0005 unified manifest (sources / models / triggers)
# ---------------------------------------------------------------------------

SCHEMA_V3: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://gispulse.dev/schemas/manifest-v3.json",
    "title": "GISPulse Manifest v3",
    "description": (
        "Unified ADR 0005 manifest: declared sources, an optional "
        "staging facade, a `models:` DAG that compiles to PipelineSpec, "
        "reactive triggers, plus security/runtime sections."
    ),
    "type": "object",
    "required": ["version"],
    "properties": {
        "version": {"const": 3},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "sources": {
            "type": "object",
            "description": "Declared input sources, alias → spec.",
            "additionalProperties": {"$ref": "#/$defs/source"},
        },
        "staging": {"$ref": "#/$defs/staging"},
        "models": {
            "type": "object",
            "description": "DAG of derived models — compiled to PipelineSpec.steps.",
            "additionalProperties": {"$ref": "#/$defs/model"},
        },
        "triggers": {
            "type": "array",
            "items": {"$ref": "#/$defs/v3_trigger"},
        },
        "security": {"type": "object"},
        "runtime": {"type": "object"},
    },
    "additionalProperties": False,
    "$defs": {
        "source": {
            "type": "object",
            "required": ["uri"],
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "URI of the source — local path, http(s), s3, virtual:…",
                },
                "layer": {
                    "type": ["string", "null"],
                    "description": "Layer name inside multi-layer sources (GPKG, GeoParquet…).",
                },
                "geometry": {
                    "type": ["string", "null"],
                    "description": "Logical geometry column name (Q3 — geometry-agnostic).",
                },
                "crs": {
                    "type": ["string", "null"],
                    "description": "Logical CRS — never the physical encoding.",
                },
                "format": {
                    "type": ["string", "null"],
                    "description": "Optional explicit format hint (gpkg, parquet, …).",
                },
            },
            "additionalProperties": True,
        },
        "staging": {
            "type": "object",
            "properties": {
                "engine": {
                    "type": "string",
                    "enum": ["duckdb", "postgis", "gpkg", "spatialite"],
                    "description": "GISPulseConfig.engine — global, not per-model.",
                },
                "attach": {"type": "boolean", "default": True},
                "cdc": {
                    "type": "string",
                    "enum": ["off", "snapshot", "incremental"],
                    "default": "off",
                },
            },
            "additionalProperties": False,
        },
        "model": {
            "type": "object",
            "required": ["select"],
            "properties": {
                "select": {
                    "type": "string",
                    "description": "Upstream layer reference — a source name or another model.",
                },
                "transform": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/transform_step"},
                    "default": [],
                },
                "materialize": {
                    "type": "string",
                    "enum": ["view", "table", "incremental"],
                    "default": "view",
                },
                "refresh": {
                    "type": "string",
                    "enum": ["on_change", "manual", "schedule"],
                    "default": "manual",
                },
                "assert": {
                    "type": "array",
                    "description": (
                        "Data-quality gates run after materialization (ELT "
                        "Lot 4F): not_null / unique / geometry_valid / "
                        "expect_rows. Each entry is one assertion kind plus "
                        "an optional severity ('error' default, or 'warning')."
                    ),
                    "items": {"$ref": "#/$defs/assertion"},
                    "default": [],
                },
            },
            "additionalProperties": False,
        },
        "assertion": {
            "type": "object",
            "description": (
                "One data-quality assertion — exactly one kind key (not_null"
                " / unique / geometry_valid / expect_rows), with an optional"
                " 'severity' alongside it."
            ),
            "minProperties": 1,
            "additionalProperties": True,
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["error", "warning"],
                    "default": "error",
                },
                "not_null": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "unique": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "geometry_valid": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "boolean"},
                    ],
                },
                "expect_rows": {
                    "type": "object",
                    "properties": {
                        "min": {"type": "integer", "minimum": 0},
                        "max": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "transform_step": {
            "type": "object",
            "description": (
                "Single transform — exactly one key (the capability name) "
                "mapping to its params object. e.g. `{filter: {where: ...}}`."
            ),
            "minProperties": 1,
            "maxProperties": 1,
            "additionalProperties": {"type": "object"},
        },
        "v3_trigger": {
            "type": "object",
            "required": ["name", "on"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "on": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "Event(s): INSERT, UPDATE, DELETE, schedule, manual.",
                },
                "table": {"type": "string"},
                "when": {
                    "oneOf": [
                        {"type": "array"},
                        {"type": "object"},
                        {"type": "null"},
                    ],
                },
                "actions": {
                    "type": "array",
                    "items": {"type": "object"},
                },
            },
            "additionalProperties": True,
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
    """Select schema based on data type and version field.

    A dict with ``version: 3`` (ADR 0005 unified manifest) routes to
    :data:`SCHEMA_V3`; otherwise ``version: 2`` is the default for any
    dict. A list is the legacy v1 flat rule format.
    """
    if isinstance(raw, list):
        return SCHEMA_V1
    if isinstance(raw, dict):
        if raw.get("version") == 3:
            return SCHEMA_V3
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
        if raw.get("version") == 3:
            errors.extend(_validate_v3_basic(raw))
        else:
            errors.extend(_validate_v2_basic(raw))

    return errors


def _validate_v2_basic(raw: dict[str, Any]) -> list[str]:
    """Structural check for a v2 PipelineSpec dict (no jsonschema)."""
    errors: list[str] = []
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


def _validate_v3_basic(raw: dict[str, Any]) -> list[str]:
    """Structural check for a v3 manifest dict (no jsonschema).

    Mirrors the rules in :data:`SCHEMA_V3` enough to catch the obvious
    mistakes — missing ``uri`` on a source, missing ``select`` on a
    model, a transform step with the wrong number of keys, a trigger
    missing ``name`` / ``on``. Falls back here when ``jsonschema`` is
    not installed; the full schema check still happens when it is.
    """
    errors: list[str] = []
    sources = raw.get("sources", {})
    if not isinstance(sources, dict):
        errors.append("sources: must be an object (alias -> source spec)")
        sources = {}
    for name, spec in sources.items():
        if not isinstance(spec, dict):
            errors.append(f"sources.{name}: must be an object")
            continue
        if "uri" not in spec:
            errors.append(f"sources.{name}: missing required field 'uri'")

    models = raw.get("models", {})
    if not isinstance(models, dict):
        errors.append("models: must be an object (name -> model spec)")
        models = {}
    for name, model in models.items():
        if not isinstance(model, dict):
            errors.append(f"models.{name}: must be an object")
            continue
        if "select" not in model:
            errors.append(f"models.{name}: missing required field 'select'")
        transform = model.get("transform")
        if transform is not None:
            if not isinstance(transform, list):
                errors.append(
                    f"models.{name}.transform: must be an array of "
                    "single-key {capability: params} objects"
                )
                continue
            for i, step in enumerate(transform):
                if not isinstance(step, dict) or len(step) != 1:
                    errors.append(
                        f"models.{name}.transform[{i}]: must have exactly "
                        "one key (capability name)"
                    )

    for i, trigger in enumerate(raw.get("triggers", []) or []):
        if not isinstance(trigger, dict):
            errors.append(f"triggers[{i}]: must be an object")
            continue
        for required in ("name", "on"):
            if required not in trigger:
                errors.append(f"triggers[{i}]: missing required field {required!r}")
    return errors
