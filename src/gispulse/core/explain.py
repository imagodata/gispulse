"""Inspect a v3 manifest's compiled DAG and per-node strategy dispatch.

ELT Lot 4E (issue #251, ADR 0005 / EPIC #243). Re-scoped per ADR 0005:
the DAG (``models:``→``PipelineSpec.steps``) already exists (Lot 4A /
#247), the cycle check already runs at load time (Lot 4B / #248), and
``select_strategy()`` already drives the per-node ELT/ETL dispatch at
execution time (the Lot 1-3 push-down). This module *surfaces* that
information — it does not invent it.

The output answers the three questions ``gispulse explain`` exists to
make obvious before running anything:

1. **What runs** — execution order over the models, dependencies, and
   each model's materialization / refresh mode.
2. **How each operation will run** — which :class:`ExecutionStrategy`
   wins per step under the configured engine, with its priority. A
   capability whose only available strategy is the Python fallback is
   flagged ``etl_strict``.
3. **Where the SQL chain breaks** — any node forced onto Python becomes
   a yellow flag in the rendered text so the user can see the
   ETL→Python re-materialisation point at a glance.

Predictability is an argument-of-sale against the FME / ETL incumbent —
this module's contract is to render that predictability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gispulse.capabilities import get as _get_capability
from gispulse.capabilities.base import Capability
from gispulse.capabilities.strategy import (
    ExecutionContext,
    ExecutionStrategy,
    select_strategy,
)
from gispulse.core.dag import topological_sort
from gispulse.core.manifest_v3 import (
    ManifestV3,
    ModelSpec,
    _compile_model_steps,
    validate_manifest,
)

__all__ = [
    "StrategyInfo",
    "StepExplanation",
    "ModelExplanation",
    "ManifestExplanation",
    "explain_manifest",
    "format_explanation_text",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StrategyInfo:
    """One available :class:`ExecutionStrategy` on a capability."""

    #: ``"python"`` / ``"duckdb"`` / ``"postgis"``.
    mode: str
    priority: int
    #: ``True`` when this strategy's :meth:`can_execute` returns True under
    #: the synthetic explain context (engine name + step params).
    eligible: bool


@dataclass
class StepExplanation:
    """One step (capability call) within a model's transform chain."""

    step_id: str
    capability: str
    params: dict[str, Any]
    strategies: list[StrategyInfo] = field(default_factory=list)
    #: The :class:`StrategyInfo` ``select_strategy`` would pick — None if
    #: no strategy is eligible (the capability falls back to ``execute()``).
    picked: StrategyInfo | None = None
    #: ``True`` when the capability has no SQL strategy at all (only the
    #: Python fallback) — a hard ETL re-materialisation point.
    etl_strict: bool = False


@dataclass
class ModelExplanation:
    """One model from the manifest."""

    name: str
    select: str
    #: ``"view"`` / ``"table"`` / ``"incremental"``.
    materialize: str
    #: ``"manual"`` / ``"on_change"`` / ``"schedule"``.
    refresh: str
    #: Other model names this one depends on (via ``select:`` or
    #: transform-level ``with:`` to a model — sources are not listed
    #: here, they appear under :attr:`select` instead).
    depends_on: list[str] = field(default_factory=list)
    steps: list[StepExplanation] = field(default_factory=list)


@dataclass
class ManifestExplanation:
    """The full explain report for one :class:`ManifestV3`."""

    manifest_name: str
    #: ``staging.engine`` value used to score strategy eligibility.
    engine: str
    execution_order: list[str] = field(default_factory=list)
    models: list[ModelExplanation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Strategy probing
# ---------------------------------------------------------------------------


class _ExplainEngine:
    """Minimal duck-type used by :func:`select_strategy` during explain.

    Captures only the attribute the Lot 1-3 strategies read
    (``backend_name``) so we can score eligibility without spinning up a
    real :class:`SpatialEngine`. Anything else stays ``None``.
    """

    def __init__(self, backend_name: str) -> None:
        self.backend_name = backend_name


def _probe_strategies(
    capability: Capability,
    backend_name: str,
    params: dict[str, Any],
) -> tuple[list[StrategyInfo], StrategyInfo | None, bool]:
    """Return (strategies, picked, etl_strict) for one step.

    Mirrors :func:`select_strategy` exactly — calls each strategy's
    ``can_execute`` against an :class:`ExecutionContext` carrying the
    configured engine and the step's params.
    """
    strategies = list(getattr(capability, "_strategies", ()) or [])
    info: list[StrategyInfo] = []
    if not strategies:
        # No strategies declared — the capability runs through plain
        # ``execute()`` (Python). Strictly ETL.
        return [
            StrategyInfo(mode="python", priority=10, eligible=True)
        ], StrategyInfo(mode="python", priority=10, eligible=True), True

    ctx = ExecutionContext(
        engine=_ExplainEngine(backend_name),
        feature_count=1,
        params=dict(params),
    )
    eligible: list[ExecutionStrategy] = []
    for s in strategies:
        mode = getattr(s, "mode", None)
        mode_name = (
            mode.value if hasattr(mode, "value") else (mode or "python")
        )
        try:
            is_eligible = bool(s.can_execute(ctx))
        except Exception:
            is_eligible = False
        info.append(
            StrategyInfo(
                mode=str(mode_name),
                priority=int(s.priority),
                eligible=is_eligible,
            )
        )
        if is_eligible:
            eligible.append(s)

    has_sql = any(i.mode in {"duckdb", "postgis"} for i in info)
    etl_strict = not has_sql

    picked_strategy = (
        max(eligible, key=lambda s: s.priority) if eligible else None
    )
    picked_info: StrategyInfo | None = None
    if picked_strategy is not None:
        pmode = getattr(picked_strategy, "mode", None)
        pmode_name = (
            pmode.value if hasattr(pmode, "value") else (pmode or "python")
        )
        picked_info = StrategyInfo(
            mode=str(pmode_name),
            priority=int(picked_strategy.priority),
            eligible=True,
        )
    # Sanity check that our probe matches ``select_strategy`` proper.
    assert (
        select_strategy(strategies, ctx)
        is (picked_strategy if eligible else None)
    )
    return info, picked_info, etl_strict


# ---------------------------------------------------------------------------
# Manifest walking
# ---------------------------------------------------------------------------


def _model_depends_on(
    model: ModelSpec, model_names: set[str]
) -> list[str]:
    """Names of other models this model depends on (deduped, ordered).

    Excludes sources — only model-to-model edges are listed here, the
    source appears under :attr:`ModelExplanation.select` directly.
    """
    deps: list[str] = []
    seen: set[str] = set()

    def _add(ref: str) -> None:
        if ref in model_names and ref not in seen:
            deps.append(ref)
            seen.add(ref)

    _add(model.select)
    for step in model.transform or []:
        if not isinstance(step, dict) or len(step) != 1:
            continue
        (_cap, params), = step.items()
        ref = params.get("with") if isinstance(params, dict) else None
        if isinstance(ref, str):
            _add(ref)
    return deps


def explain_manifest(
    manifest: ManifestV3,
    *,
    engine: str | None = None,
) -> ManifestExplanation:
    """Walk *manifest* and produce a structured explain report.

    Args:
        manifest: Parsed v3 manifest. Re-validated through
            :func:`validate_manifest` so cycles / unresolved refs
            surface here too, not only at load time.
        engine:   Backend name used to score strategy eligibility.
            Defaults to ``manifest.staging.engine`` and falls back to
            ``"duckdb"`` when unset.

    Returns:
        :class:`ManifestExplanation` with one :class:`ModelExplanation`
        per model, in topological order, each carrying its steps and
        their picked strategies.
    """
    validate_manifest(manifest)

    chosen_engine = engine or manifest.staging.engine or "duckdb"
    model_names = set(manifest.models)
    # Reuse the inter-model edge set the runner builds.
    edges: list[tuple[str, str]] = []
    for name, model in manifest.models.items():
        if model.select in model_names:
            edges.append((model.select, name))
        for step in model.transform or []:
            if not isinstance(step, dict) or len(step) != 1:
                continue
            (_cap, params), = step.items()
            ref = params.get("with") if isinstance(params, dict) else None
            if isinstance(ref, str) and ref in model_names:
                edges.append((ref, name))
    order = topological_sort(list(model_names), edges)

    explanation = ManifestExplanation(
        manifest_name=manifest.name or "(unnamed)",
        engine=chosen_engine,
        execution_order=order,
    )

    for model_name in order:
        model = manifest.models[model_name]
        steps = _compile_model_steps(model, manifest.sources, model_names)
        step_reports: list[StepExplanation] = []
        for step in steps:
            cap_name = step.capability or ""
            try:
                capability = _get_capability(cap_name)
            except KeyError:
                # Unknown capability — surface it as a step that can't be
                # explained, but don't break the whole report.
                step_reports.append(
                    StepExplanation(
                        step_id=step.id,
                        capability=cap_name,
                        params=dict(step.params),
                        strategies=[],
                        picked=None,
                        etl_strict=True,
                    )
                )
                continue
            strategies, picked, etl_strict = _probe_strategies(
                capability, chosen_engine, step.params
            )
            step_reports.append(
                StepExplanation(
                    step_id=step.id,
                    capability=cap_name,
                    params=dict(step.params),
                    strategies=strategies,
                    picked=picked,
                    etl_strict=etl_strict,
                )
            )

        explanation.models.append(
            ModelExplanation(
                name=model.name,
                select=model.select,
                materialize=model.materialize,
                refresh=model.refresh,
                depends_on=_model_depends_on(model, model_names),
                steps=step_reports,
            )
        )
    return explanation


# ---------------------------------------------------------------------------
# Text renderer
# ---------------------------------------------------------------------------


def format_explanation_text(explanation: ManifestExplanation) -> str:
    """Render an explain report as a human-readable text block.

    The format is stable enough for ``gispulse explain`` users to grep
    over but optimised for readability, not machine parsing — for that,
    convert :class:`ManifestExplanation` to JSON directly.
    """
    lines: list[str] = []
    lines.append(f"Manifest: {explanation.manifest_name}")
    lines.append(f"Engine:   {explanation.engine}")
    lines.append(
        "Order:    " + " → ".join(explanation.execution_order)
    )
    lines.append("")
    for model in explanation.models:
        lines.append(f"model: {model.name}")
        lines.append(f"  select:      {model.select}")
        lines.append(f"  materialize: {model.materialize}")
        lines.append(f"  refresh:     {model.refresh}")
        if model.depends_on:
            lines.append(
                "  depends_on:  " + ", ".join(model.depends_on)
            )
        for step in model.steps:
            tag = "⚠ ETL-strict" if step.etl_strict else "✓ "
            picked = (
                f"{step.picked.mode}@{step.picked.priority}"
                if step.picked
                else "python (fallback)"
            )
            lines.append(
                f"  • {step.step_id} — {step.capability} "
                f"→ {picked}  [{tag}]"
            )
            if step.strategies:
                avail = ", ".join(
                    f"{s.mode}@{s.priority}"
                    + ("" if s.eligible else "(gated)")
                    for s in step.strategies
                )
                lines.append(f"      available: {avail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
