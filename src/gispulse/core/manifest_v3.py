"""Unified GISPulse manifest version 3 — ADR 0005 / EPIC #243 / issue #247.

The v3 manifest unifies the three legacy configuration surfaces
(``triggers.yaml`` v1, pipeline JSON v2, bare rule lists) into one
declarative schema with ``sources`` / ``staging`` / ``models`` /
``triggers`` / ``security`` / ``runtime`` sections.

This module is the **declarative surface**: typed dataclasses + a
loader + a compiler that turns the authored ``models:`` form into the
existing :class:`~gispulse.core.pipeline.PipelineSpec` —
*no new DAG engine, no new dispatcher*. ``models:`` syntax compiles
directly to ``StepSpec.input`` edges; the rest of the engine
(``GraphExecutor`` + the strategy dispatch + the capability layer)
runs the pipeline as before.

Public surface:

- :class:`ManifestV3` (with :class:`SourceSpec`, :class:`StagingSpec`,
  :class:`ModelSpec`, :class:`V3TriggerSpec`).
- :func:`load_manifest_v3` — parse a YAML / JSON file, validate against
  :data:`gispulse.core.pipeline_schema.SCHEMA_V3`, return ManifestV3.
- :func:`compile_to_pipeline` — turn the manifest's models into a
  :class:`PipelineSpec`. Sources are surfaced as ``ref_layers``;
  each model's transforms become a chain of StepSpecs linked through
  ``StepSpec.input``.
- :func:`migrate_to_v3` — convert a v1 (flat rule list) or v2
  (PipelineSpec dict) raw payload into a v3 dict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gispulse.core.pipeline import (
    PipelineSpec,
    StepSpec,
    TriggerSpec,
)
from gispulse.core.dag import CycleError, topological_sort
from gispulse.core.pipeline_schema import validate_pipeline_json

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gispulse.core.assertions import AssertionSpec

__all__ = [
    "SourceSpec",
    "StagingSpec",
    "ModelSpec",
    "V3TriggerSpec",
    "ManifestV3",
    "ManifestValidationError",
    "validate_manifest",
    "load_manifest_v3",
    "compile_to_pipeline",
    "migrate_to_v3",
    "manifest_to_dict",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SourceSpec:
    """A declared input source — ``sources: { <name>: … }``."""

    name: str
    uri: str
    layer: str | None = None
    geometry: str | None = None
    crs: str | None = None
    format: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class StagingSpec:
    """Optional ``staging:`` facade — engine / attach / cdc knobs."""

    engine: str | None = None
    attach: bool = True
    cdc: str = "off"  # off | snapshot | incremental


@dataclass
class ModelSpec:
    """A derived model — a node in the manifest DAG.

    Attributes:
        name:        Model name (the key under ``models:``). Also the
                     terminal step id after compilation.
        select:      Upstream layer reference — a source name or
                     another model.
        transform:   List of ``{capability: params}`` shorthand items.
                     Compiles to a chain of :class:`StepSpec`.
        materialize: ``view`` (default), ``table``, ``incremental``.
        refresh:     ``manual`` (default), ``on_change``, ``schedule``.
        assertions:  Data-quality gates run after materialization —
                     see :mod:`gispulse.core.assertions` and ELT Lot 4F
                     (issue #252). ``severity=error`` failures raise;
                     ``severity=warning`` collect on the run result.
    """

    name: str
    select: str
    transform: list[dict[str, Any]] = field(default_factory=list)
    materialize: str = "view"
    refresh: str = "manual"
    assertions: list["AssertionSpec"] = field(default_factory=list)


@dataclass
class V3TriggerSpec:
    """Reactive trigger declared at the manifest top level."""

    name: str
    on: str | list[str]
    table: str = ""
    when: list[Any] | dict[str, Any] | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ManifestV3:
    """Parsed v3 manifest — the in-memory shape after loading."""

    name: str = ""
    description: str = ""
    sources: dict[str, SourceSpec] = field(default_factory=dict)
    staging: StagingSpec = field(default_factory=StagingSpec)
    models: dict[str, ModelSpec] = field(default_factory=dict)
    triggers: list[V3TriggerSpec] = field(default_factory=list)
    security: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parse / load
# ---------------------------------------------------------------------------


def _parse_sources(raw: dict[str, Any]) -> dict[str, SourceSpec]:
    out: dict[str, SourceSpec] = {}
    for name, spec in (raw or {}).items():
        known = {"uri", "layer", "geometry", "crs", "format"}
        extras = {k: v for k, v in spec.items() if k not in known}
        out[name] = SourceSpec(
            name=name,
            uri=spec["uri"],
            layer=spec.get("layer"),
            geometry=spec.get("geometry"),
            crs=spec.get("crs"),
            format=spec.get("format"),
            extra=extras,
        )
    return out


def _parse_staging(raw: dict[str, Any] | None) -> StagingSpec:
    if not raw:
        return StagingSpec()
    return StagingSpec(
        engine=raw.get("engine"),
        attach=bool(raw.get("attach", True)),
        cdc=raw.get("cdc", "off"),
    )


def _parse_models(raw: dict[str, Any]) -> dict[str, ModelSpec]:
    from gispulse.core.assertions import parse_assertions

    out: dict[str, ModelSpec] = {}
    for name, spec in (raw or {}).items():
        out[name] = ModelSpec(
            name=name,
            select=spec["select"],
            transform=list(spec.get("transform") or []),
            materialize=spec.get("materialize", "view"),
            refresh=spec.get("refresh", "manual"),
            # ADR 0005 reserves the ``assert:`` block per model for
            # data-quality gates run after materialization (Lot 4F).
            assertions=parse_assertions(spec.get("assert") or []),
        )
    return out


def _parse_v3_triggers(raw: list[Any]) -> list[V3TriggerSpec]:
    out: list[V3TriggerSpec] = []
    for spec in raw or []:
        out.append(
            V3TriggerSpec(
                name=spec["name"],
                on=spec["on"],
                table=spec.get("table", ""),
                when=spec.get("when"),
                actions=list(spec.get("actions") or []),
            )
        )
    return out


def _load_raw(path: Path) -> Any:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in (".yaml", ".yml"):
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(text)
    return json.loads(text)


def load_manifest_v3(path: str | Path, *, validate: bool = True) -> ManifestV3:
    """Load and parse a v3 manifest from YAML / JSON.

    Args:
        path:     File path (``.yaml`` / ``.yml`` / ``.json``).
        validate: When ``True`` (default), validate against ``SCHEMA_V3``
            **and** run the load-time graph check
            (:func:`validate_manifest`) — unresolved ``select:``/``with:``
            references and inter-model cycles raise
            :class:`ManifestValidationError`. Orphan-model warnings are
            logged through :mod:`gispulse.core.logging`.

    Returns:
        Typed :class:`ManifestV3`.
    """
    from gispulse.core.logging import get_logger

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Manifest file not found: {p}")
    raw = _load_raw(p)
    if not isinstance(raw, dict):
        raise ValueError(
            f"v3 manifest must be a YAML/JSON object, got {type(raw).__name__}"
        )
    if raw.get("version") != 3:
        raise ValueError(
            f"Expected version: 3 manifest, got version={raw.get('version')!r}"
        )

    if validate:
        errors = validate_pipeline_json(raw)
        if errors:
            msg = f"Manifest v3 schema validation failed for {p.name}:\n"
            msg += "\n".join(f"  - {e}" for e in errors[:20])
            if len(errors) > 20:
                msg += f"\n  ... and {len(errors) - 20} more errors"
            raise ValueError(msg)

    manifest = ManifestV3(
        name=raw.get("name", ""),
        description=raw.get("description", ""),
        sources=_parse_sources(raw.get("sources", {})),
        staging=_parse_staging(raw.get("staging")),
        models=_parse_models(raw.get("models", {})),
        triggers=_parse_v3_triggers(raw.get("triggers", [])),
        security=dict(raw.get("security") or {}),
        runtime=dict(raw.get("runtime") or {}),
    )

    if validate:
        # Load-time graph check — raises ManifestValidationError on
        # unresolved refs / cycles, logs orphan warnings.
        log = get_logger(__name__)
        for warning in validate_manifest(manifest):
            log.warning("manifest_v3_warning", manifest=p.name, message=warning)

    return manifest


# ---------------------------------------------------------------------------
# Compile: ManifestV3 → PipelineSpec
# ---------------------------------------------------------------------------


def _resolve_ref(
    ref: str, sources: dict[str, SourceSpec], model_names: set[str]
) -> tuple[str, bool]:
    """Resolve a ``select:`` reference.

    Returns ``(resolved, is_model_ref)``:

    - For a *source* reference → ``(source_name, False)`` — the compiler
      registers it under ``PipelineSpec.ref_layers``.
    - For a *model* reference → ``(model_name, True)`` — used as the
      upstream step id in the generated DAG.
    """
    if ref in sources:
        return ref, False
    if ref in model_names:
        return ref, True
    raise ValueError(
        f"Unknown select target {ref!r} — not a declared source or model"
    )


def _split_transform(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Pull the ``{capability_name: params}`` pair out of a transform item."""
    if len(item) != 1:
        raise ValueError(
            f"transform step must have exactly one key, got {list(item)!r}"
        )
    (cap_name, params), = item.items()
    return cap_name, dict(params or {})


def _compile_model_steps(
    model: ModelSpec,
    sources: dict[str, SourceSpec],
    model_names: set[str],
) -> list[StepSpec]:
    """Turn one ModelSpec into a chain of StepSpecs.

    The terminal step's id is the model name (so cross-model
    ``select: other_model`` resolves to the right upstream node). Each
    intermediate step is named ``<model>__t<index>``. A transform's
    ``with: <ref>`` is converted into a multi-input edge plus a
    ``ref_layer`` capability param for the legacy ref-layer plumbing.
    """
    primary, primary_is_model = _resolve_ref(model.select, sources, model_names)
    prev_input: str | list[str] | None
    prev_input = primary if primary_is_model else None
    # For a source-rooted model, no upstream step exists; the first step
    # reads the primary input set by the executor (the source registered
    # in ref_layers).

    transforms = model.transform
    if not transforms:
        # Passthrough — an identity model. Compile to a single
        # filter-no-op step that simply forwards the input.
        return [
            StepSpec(
                id=model.name,
                type="capability",
                capability="filter",  # no params → identity
                params={},
                input=prev_input,
                order=0,
            )
        ]

    steps: list[StepSpec] = []
    n = len(transforms)
    for i, item in enumerate(transforms):
        cap_name, params = _split_transform(item)
        # `with: <ref>` ⇒ multi-input edge + ref_layer capability param.
        extra_ref = params.pop("with", None)
        inputs: str | list[str] | None = prev_input
        if extra_ref is not None:
            resolved, is_model = _resolve_ref(
                extra_ref, sources, model_names
            )
            # ref_layer carries the secondary input for the existing
            # ref-layer plumbing (spatial_join, attribute_join, …).
            params.setdefault("ref_layer", resolved)
            # When the primary input is itself an upstream *step* AND the
            # with-ref names another *model*, surface the dep as a
            # multi-input edge so the DAG executor wires both branches.
            # When the primary is a *source* (prev_input is None), the
            # secondary stays on ref_layer alone — adding it to step.input
            # would silently swap the primary with the secondary.
            if is_model and prev_input is not None:
                base = (
                    [prev_input]
                    if isinstance(prev_input, str)
                    else list(prev_input)
                )
                inputs = base + [resolved]
        step_id = model.name if i == n - 1 else f"{model.name}__t{i}"
        steps.append(
            StepSpec(
                id=step_id,
                type="capability",
                capability=cap_name,
                params=params,
                input=inputs,
                order=i,
            )
        )
        prev_input = step_id
    return steps


def _topo_models(models: dict[str, ModelSpec]) -> list[str]:
    """Stable topological order over the inter-model select edges.

    Models that ``select`` a *source* (no model dep) come first; models
    that ``select`` another model wait for that one. The result drives
    the order in which model step-chains are appended to the compiled
    :class:`PipelineSpec` — useful for the readability of the emitted
    pipeline. (The cycle-detection / DAG-execution semantics belong to
    the engine, not the compiler — see #248 for the load-time cycle
    check planned alongside this lot.)
    """
    remaining = dict(models)
    ordered: list[str] = []
    progress = True
    while remaining and progress:
        progress = False
        for name in list(remaining):
            ref = remaining[name].select
            if ref not in remaining:
                ordered.append(name)
                del remaining[name]
                progress = True
    # Any leftover models are part of a cycle — append in declaration
    # order so the compiler still emits something deterministic.
    ordered.extend(remaining)
    return ordered


def compile_to_pipeline(manifest: ManifestV3) -> PipelineSpec:
    """Compile a v3 manifest's ``models:`` into a runnable PipelineSpec.

    The DAG is the union of every model's step chain, linked through
    ``StepSpec.input``. Sources land in ``PipelineSpec.ref_layers``.
    Manifest-level v3 triggers are not represented on PipelineSpec —
    they are reactive and live next to the pipeline, not inside it.
    """
    model_names = set(manifest.models)
    ref_layers = {name: src.uri for name, src in manifest.sources.items()}

    steps: list[StepSpec] = []
    order = 0
    for model_name in _topo_models(manifest.models):
        model_steps = _compile_model_steps(
            manifest.models[model_name],
            manifest.sources,
            model_names,
        )
        for step in model_steps:
            step.order = order
            order += 1
            steps.append(step)

    return PipelineSpec(
        version=2,  # compiled output — the runtime sees v2 DAG semantics.
        name=manifest.name,
        description=manifest.description,
        steps=steps,
        triggers=[],
        ref_layers=ref_layers,
    )


# ---------------------------------------------------------------------------
# Migration: v1 / v2 → v3
# ---------------------------------------------------------------------------


def _v2_to_v3(raw: dict[str, Any]) -> dict[str, Any]:
    """Compile a v2 PipelineSpec dict to an equivalent v3 manifest dict.

    Each v2 step becomes a single-transform model whose select points at
    the upstream step's id (or at the primary input, surfaced as the
    pseudo source ``input``).
    """
    out: dict[str, Any] = {
        "version": 3,
        "name": raw.get("name", ""),
        "description": raw.get("description", ""),
    }
    sources: dict[str, dict[str, Any]] = {}
    for alias, uri in (raw.get("ref_layers") or {}).items():
        sources[alias] = {"uri": uri}
    # The primary input is unknown to v2 statically; surface it as a
    # placeholder source so the v3 file is self-contained.
    sources.setdefault("input", {"uri": "<primary_input>"})
    out["sources"] = sources

    models: dict[str, dict[str, Any]] = {}
    for step in raw.get("steps") or []:
        if not step.get("enabled", True):
            continue
        sid = step.get("id") or step.get("name") or f"step_{len(models)}"
        cap = step.get("capability") or step.get("type") or "filter"
        params = dict(step.get("params") or step.get("config") or {})
        upstream = step.get("input")
        select: str
        if isinstance(upstream, list):
            select = upstream[0] if upstream else "input"
            # Carry the remaining inputs through the legacy ``ref_layer`` arg.
            if len(upstream) > 1 and "ref_layer" not in params:
                params["ref_layer"] = upstream[1]
        elif isinstance(upstream, str) and upstream:
            select = upstream
        else:
            select = "input"
        models[sid] = {
            "select": select,
            "transform": [{cap: params}] if cap else [],
        }
    out["models"] = models

    triggers_in = raw.get("triggers") or []
    if triggers_in:
        out["triggers"] = [
            {
                "name": t.get("name", f"trigger_{i}"),
                "on": t.get("on", "manual"),
                "table": t.get("table", ""),
                "actions": t.get("then_config", {}).get("actions", [])
                or [{"type": t.get("then", "log_event"), **t.get("then_config", {})}],
            }
            for i, t in enumerate(triggers_in)
        ]
    return out


def _v1_to_v3(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Compile a v1 flat rule list to a v3 manifest dict — a linear chain."""
    sources = {"input": {"uri": "<primary_input>"}}
    models: dict[str, dict[str, Any]] = {}
    prev_id: str | None = None
    for i, entry in enumerate(raw):
        rid = entry.get("name") or f"step_{i}"
        cap = entry.get("capability", "filter")
        params = dict(entry.get("config") or {})
        params.pop("order", None)
        select = prev_id or "input"
        models[rid] = {
            "select": select,
            "transform": [{cap: params}],
        }
        prev_id = rid
    return {"version": 3, "sources": sources, "models": models}


def migrate_to_v3(raw: Any) -> dict[str, Any]:
    """Convert a v1 (list) or v2 (dict) raw payload to a v3 manifest dict."""
    if isinstance(raw, list):
        return _v1_to_v3(raw)
    if isinstance(raw, dict):
        version = raw.get("version")
        if version == 3:
            return raw  # already v3
        if version == 2:
            return _v2_to_v3(raw)
        raise ValueError(
            f"migrate_to_v3: unsupported pipeline dict (version={version!r})"
        )
    raise TypeError(
        f"migrate_to_v3: expected dict or list, got {type(raw).__name__}"
    )


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def manifest_to_dict(manifest: ManifestV3) -> dict[str, Any]:
    """Render a :class:`ManifestV3` back to a canonical v3 dict.

    Useful for ``gispulse migrate`` (write the migrated manifest out) and
    for golden tests on the loader.
    """
    out: dict[str, Any] = {"version": 3}
    if manifest.name:
        out["name"] = manifest.name
    if manifest.description:
        out["description"] = manifest.description
    if manifest.sources:
        out["sources"] = {
            name: {
                k: v
                for k, v in {
                    "uri": src.uri,
                    "layer": src.layer,
                    "geometry": src.geometry,
                    "crs": src.crs,
                    "format": src.format,
                    **src.extra,
                }.items()
                if v is not None
            }
            for name, src in manifest.sources.items()
        }
    if manifest.staging.engine or manifest.staging.cdc != "off":
        st: dict[str, Any] = {}
        if manifest.staging.engine:
            st["engine"] = manifest.staging.engine
        if not manifest.staging.attach:
            st["attach"] = False
        if manifest.staging.cdc != "off":
            st["cdc"] = manifest.staging.cdc
        out["staging"] = st
    if manifest.models:
        out["models"] = {
            name: {
                "select": m.select,
                **({"transform": m.transform} if m.transform else {}),
                **({"materialize": m.materialize} if m.materialize != "view" else {}),
                **({"refresh": m.refresh} if m.refresh != "manual" else {}),
            }
            for name, m in manifest.models.items()
        }
    if manifest.triggers:
        out["triggers"] = [
            {
                "name": t.name,
                "on": t.on,
                **({"table": t.table} if t.table else {}),
                **({"when": t.when} if t.when is not None else {}),
                **({"actions": t.actions} if t.actions else {}),
            }
            for t in manifest.triggers
        ]
    if manifest.security:
        out["security"] = manifest.security
    if manifest.runtime:
        out["runtime"] = manifest.runtime
    return out


# Silence the unused-import warning when TYPE_CHECKING is False.
_ = TriggerSpec


# ---------------------------------------------------------------------------
# Load-time validation (ELT Lot 4B — issue #248)
# ---------------------------------------------------------------------------


class ManifestValidationError(ValueError):
    """One or more load-time problems in a v3 manifest.

    Attributes:
        errors: Human-readable error lines (unresolved refs, cycles).
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__(
            "v3 manifest validation failed:\n  - "
            + "\n  - ".join(errors)
        )


def _extract_with_ref(params: object) -> str | None:
    if isinstance(params, dict):
        ref = params.get("with")
        if isinstance(ref, str):
            return ref
    return None


def validate_manifest(manifest: ManifestV3) -> list[str]:
    """Validate a v3 manifest at load-time.

    Catches the three load-time concerns ADR 0005 / #248 calls for:

    - Unresolved ``select:`` and transform-level ``with:`` references —
      a ``ModelSpec`` selecting a name that is neither a declared source
      nor another model. Error (blocking).
    - Cycles in the inter-model dependency graph, detected by
      :func:`~gispulse.core.dag.topological_sort` (Kahn's algorithm,
      shared with :class:`GraphExecutor`). Error (blocking).
    - Orphan models — models nobody else selects. They are often the
      pipeline's terminal outputs (perfectly legitimate), so this is
      surfaced as a warning, not an error.

    Returns the list of *warnings*. Errors raise
    :class:`ManifestValidationError`.
    """
    errors: list[str] = []
    warnings: list[str] = []

    sources = set(manifest.sources)
    models = set(manifest.models)
    known = sources | models

    referenced_models: set[str] = set()
    inter_model_edges: list[tuple[str, str]] = []

    for name, model in manifest.models.items():
        if not isinstance(model.select, str) or not model.select:
            errors.append(
                f"models.{name}: missing or empty 'select' reference"
            )
        elif model.select not in known:
            errors.append(
                f"models.{name}: select={model.select!r} is not a "
                "declared source or model"
            )
        elif model.select in models:
            inter_model_edges.append((model.select, name))
            referenced_models.add(model.select)

        for i, step in enumerate(model.transform or []):
            if not isinstance(step, dict) or len(step) != 1:
                errors.append(
                    f"models.{name}.transform[{i}]: must be a single-key "
                    "{capability: params} object"
                )
                continue
            (cap, params), = step.items()
            with_ref = _extract_with_ref(params)
            if with_ref is None:
                continue
            if with_ref not in known:
                errors.append(
                    f"models.{name}.transform[{i}].{cap}: with={with_ref!r} "
                    "is not a declared source or model"
                )
            elif with_ref in models:
                inter_model_edges.append((with_ref, name))
                referenced_models.add(with_ref)

    if errors:
        raise ManifestValidationError(errors)

    # Inter-model cycle detection (shared utility — same Kahn's algorithm
    # GraphExecutor uses at execution time).
    try:
        topological_sort(list(models), inter_model_edges)
    except CycleError as exc:
        raise ManifestValidationError(
            [
                "model-dependency cycle involving: "
                + ", ".join(repr(n) for n in sorted(exc.cycle))
            ]
        ) from exc

    # Orphan models: nobody selects them. Often a pipeline's terminal
    # outputs — emit as a warning so the user can confirm.
    for name in sorted(models - referenced_models):
        warnings.append(
            f"models.{name}: nobody selects this model "
            "(terminal output, or dead code?)"
        )
    return warnings
