"""Unified declarative pipeline specification for GISPulse (v2).

Provides a single grammar that converges rules, triggers, and graph
execution into one format. Backward-compatible with v1 flat rule lists.

Usage::

    from gispulse.core.pipeline import load_pipeline, PipelineSpec

    # v2 format (steps + triggers)
    spec = load_pipeline("pipeline.json")

    # v1 format (flat rule list) — auto-converted to v2
    spec = load_pipeline("rules.json")

    # Programmatic construction
    spec = PipelineSpec(
        name="enrich_parcels",
        steps=[
            StepSpec(id="filter", type="capability", capability="filter",
                     params={"expression": "area > 1000"}),
            StepSpec(id="buffer", type="capability", capability="buffer",
                     params={"distance": 50}, input="filter"),
        ],
    )

JSON format (v2)::

    {
      "version": 2,
      "name": "my_pipeline",
      "steps": [
        {"id": "s1", "type": "capability", "capability": "filter",
         "params": {"expression": "area > 500"}},
        {"id": "s2", "type": "capability", "capability": "buffer",
         "params": {"distance": 30}, "input": "s1"}
      ],
      "triggers": [
        {"on": "dml:parcelles:INSERT,UPDATE",
         "when": [{"type": "attr", "field": "area", "op": "gt", "value": 500}],
         "then": "run_pipeline"}
      ],
      "ref_layers": {"flood_zones": "data/flood_zones.gpkg"}
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from gispulse.core.predicates import AnyPredicate, AttrPredicate, CompoundPredicate, GeomPredicate


# ---------------------------------------------------------------------------
# Step specification
# ---------------------------------------------------------------------------


@dataclass
class StepSpec:
    """A single step in a pipeline.

    Attributes:
        id:         Unique identifier within the pipeline.
        type:       Step kind — ``"capability"`` runs a registered capability,
                    ``"filter"`` is shorthand for a filter capability,
                    ``"spatial_op"`` maps to an OperationExecutor operation,
                    ``"custom_sql"`` runs a raw SQL expression.
        capability: Capability name (required for type="capability").
        params:     Parameters passed to the capability / operation.
        input:      Reference to upstream step(s). ``None`` means the step
                    receives the pipeline's primary input. A string references
                    a single upstream step by id. A list references multiple
                    upstream steps (multi-input node).
        when:       Optional predicate — step is skipped when it evaluates to
                    ``False``. Enables conditional pipelines.
        enabled:    Soft-disable a step without removing it.
        order:      Explicit ordering for linear pipelines (used when no DAG
                    edges are present).
    """

    id: str = ""
    type: Literal["capability", "filter", "spatial_op", "custom_sql"] = "capability"
    capability: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    input: str | list[str] | None = None
    when: AnyPredicate | None = None
    enabled: bool = True
    order: int = 0


# ---------------------------------------------------------------------------
# Trigger specification (inline)
# ---------------------------------------------------------------------------


@dataclass
class TriggerSpec:
    """Declarative trigger attached to a pipeline.

    Attributes:
        on:          Event descriptor — ``"dml:table:INSERT,UPDATE"`` or
                     ``"schedule:*/5 * * * *"`` or ``"manual"``.
        when:        List of predicates that must ALL pass for the trigger
                     to fire.
        then:        Action to take — ``"run_pipeline"``, ``"notify"``,
                     ``"webhook"``, ``"log_event"``, etc.
        then_config: Configuration dict for the action.
    """

    on: str = ""
    when: list[AnyPredicate] = field(default_factory=list)
    then: str = "run_pipeline"
    then_config: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline specification (top-level)
# ---------------------------------------------------------------------------


@dataclass
class PipelineSpec:
    """Top-level declarative pipeline definition.

    Attributes:
        version:     Grammar version — ``1`` for legacy rule lists, ``2``
                     for the unified format.
        name:        Human-readable pipeline name.
        description: Optional longer description.
        steps:       Ordered list of processing steps.
        triggers:    Optional inline triggers that can invoke this pipeline.
        ref_layers:  Named reference layers — ``alias → source_path``.
                     Steps can reference these by alias in params.
    """

    version: int = 2
    name: str = ""
    description: str = ""
    steps: list[StepSpec] = field(default_factory=list)
    triggers: list[TriggerSpec] = field(default_factory=list)
    ref_layers: dict[str, str] = field(default_factory=dict)

    @property
    def is_dag(self) -> bool:
        """True if any step references another by id (DAG mode)."""
        return any(s.input is not None for s in self.steps)

    @property
    def enabled_steps(self) -> list[StepSpec]:
        """Steps that are enabled, sorted by order."""
        return sorted(
            [s for s in self.steps if s.enabled],
            key=lambda s: s.order,
        )


# ---------------------------------------------------------------------------
# Predicate parsing helpers
# ---------------------------------------------------------------------------


def _parse_predicate(raw: dict[str, Any]) -> AnyPredicate:
    """Parse a raw dict into a typed predicate."""
    pred_type = raw.get("type", "")

    if pred_type == "attr":
        return AttrPredicate(
            field=raw["field"],
            op=raw["op"],
            value=raw.get("value"),
        )
    if pred_type == "geom":
        return GeomPredicate(
            op=raw["op"],
            ref_table=raw.get("ref_table", ""),
            ref_filter=raw.get("ref_filter"),
            ref_geom_col=raw.get("ref_geom_col", "geom"),
            distance=raw.get("distance"),
            buffer_m=raw.get("buffer_m"),
        )
    if pred_type == "compound":
        return CompoundPredicate(
            logic=raw.get("logic", "AND"),
            predicates=[_parse_predicate(p) for p in raw.get("predicates", [])],
        )

    raise ValueError(f"Unknown predicate type: {pred_type!r}")


# ---------------------------------------------------------------------------
# Loader (v1 + v2)
# ---------------------------------------------------------------------------


def _parse_step(raw: dict[str, Any], index: int) -> StepSpec:
    """Parse a single step dict."""
    when_raw = raw.get("when")
    when = _parse_predicate(when_raw) if when_raw else None

    inp = raw.get("input")

    return StepSpec(
        id=raw.get("id", raw.get("name", f"step_{index}")),
        type=raw.get("type", "capability"),
        capability=raw.get("capability"),
        params=raw.get("params", raw.get("config", {})),
        input=inp,
        when=when,
        enabled=raw.get("enabled", True),
        order=raw.get("order", index),
    )


def _parse_trigger_spec(raw: dict[str, Any]) -> TriggerSpec:
    """Parse an inline trigger spec."""
    when_raw = raw.get("when", [])
    when = [_parse_predicate(p) for p in when_raw] if when_raw else []

    return TriggerSpec(
        on=raw.get("on", ""),
        when=when,
        then=raw.get("then", "run_pipeline"),
        then_config=raw.get("then_config", {}),
    )


def _parse_v2(raw: dict[str, Any]) -> PipelineSpec:
    """Parse a v2 pipeline spec."""
    steps = [_parse_step(s, i) for i, s in enumerate(raw.get("steps", []))]
    triggers = [_parse_trigger_spec(t) for t in raw.get("triggers", [])]
    ref_layers = raw.get("ref_layers", {})

    return PipelineSpec(
        version=2,
        name=raw.get("name", ""),
        description=raw.get("description", ""),
        steps=steps,
        triggers=triggers,
        ref_layers=ref_layers,
    )


def _parse_v1(raw: list[dict[str, Any]]) -> PipelineSpec:
    """Parse a v1 flat rule list and convert to PipelineSpec."""
    steps: list[StepSpec] = []
    for i, entry in enumerate(raw):
        config = dict(entry.get("config", {}))
        order = config.pop("order", i)

        steps.append(StepSpec(
            id=entry.get("name", f"step_{i}"),
            type="capability",
            capability=entry.get("capability", ""),
            params=config,
            enabled=entry.get("enabled", True),
            order=order,
        ))

    return PipelineSpec(
        version=1,
        name="",
        steps=steps,
    )


def load_pipeline(path: str | Path, *, validate: bool = True) -> PipelineSpec:
    """Load a PipelineSpec from a JSON file.

    Supports both v2 format (dict with ``"version": 2``) and v1 format
    (flat JSON array of rules). v1 files are auto-converted to v2.

    Args:
        path:     Path to the JSON pipeline file.
        validate: If True (default), validate the JSON against the
                  schema before parsing.  Validation errors are raised
                  as :class:`ValueError`.

    Returns:
        Parsed :class:`PipelineSpec`.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If the JSON structure is invalid or fails schema validation.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Pipeline file not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))

    # Schema validation (opt-in, default on)
    if validate:
        from gispulse.core.pipeline_schema import validate_pipeline_json

        errors = validate_pipeline_json(raw)
        if errors:
            msg = f"Pipeline schema validation failed for {path.name}:\n"
            msg += "\n".join(f"  - {e}" for e in errors[:20])
            if len(errors) > 20:
                msg += f"\n  ... and {len(errors) - 20} more errors"
            raise ValueError(msg)

    # v2 format: dict with version key
    if isinstance(raw, dict):
        return _parse_v2(raw)

    # v1 format: flat list of rules
    if isinstance(raw, list):
        return _parse_v1(raw)

    raise ValueError(
        f"Pipeline file must contain a JSON object (v2) or array (v1), "
        f"got {type(raw).__name__}"
    )


def pipeline_to_dict(spec: PipelineSpec) -> dict[str, Any]:
    """Serialize a PipelineSpec to a dict (for JSON export)."""
    steps = []
    for s in spec.steps:
        step_d: dict[str, Any] = {"id": s.id, "type": s.type}
        if s.capability:
            step_d["capability"] = s.capability
        if s.params:
            step_d["params"] = s.params
        if s.input is not None:
            step_d["input"] = s.input
        if not s.enabled:
            step_d["enabled"] = False
        if s.order:
            step_d["order"] = s.order
        steps.append(step_d)

    result: dict[str, Any] = {
        "version": spec.version,
        "name": spec.name,
        "steps": steps,
    }
    if spec.description:
        result["description"] = spec.description
    if spec.triggers:
        result["triggers"] = [
            {"on": t.on, "then": t.then, **({"then_config": t.then_config} if t.then_config else {})}
            for t in spec.triggers
        ]
    if spec.ref_layers:
        result["ref_layers"] = spec.ref_layers

    return result
