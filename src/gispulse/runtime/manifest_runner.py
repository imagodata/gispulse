"""End-to-end execution of a v3 manifest — materialization layer.

ELT Lot 4C (issue #249, ADR 0005 / EPIC #243). Re-scoped per ADR 0005:
``models:`` already compiles to ``PipelineSpec`` (Lot 4A / #247) and
the inter-model DAG is validated at load time (Lot 4B / #248). This
module wires the two together with the materialization rules ADR 0005
defines:

- ``materialize: view`` (default) — keep the result as an in-memory
  GeoDataFrame; downstream models read it via the materializer cache.
- ``materialize: table`` — same in-memory cache *and* register the
  result on the active engine, so the Lot 1-3 SQL push-down strategies
  can JOIN against it.
- ``materialize: incremental`` — requires ``staging.cdc: incremental``
  and an increment key. Not wired here: this batch covers the view /
  table path and the full end-to-end runner that the EPIC needs. The
  incremental path raises a clear :class:`NotImplementedError` for now.

Refresh modes (``manual`` / ``on_change`` / ``schedule``) are surfaced
on the materialized model but the runner treats every call as a fresh
recompute — schedule-driven refresh and on-change short-circuit go
with #249's continuation alongside the CDC bridge.

DELETE cascade across descendant models (ADR 0002 bounded fixed-point)
is also a follow-up: this batch establishes the runner and the
materialization plumbing; the cascade reuses the existing
:class:`gispulse.persistence.change_log_watcher` plumbing once the
incremental path lands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gispulse.core.assertions import AssertionFailure

from gispulse.core.dag import topological_sort
from gispulse.core.manifest_v3 import (
    ManifestV3,
    ModelSpec,
    SourceSpec,
    _compile_model_steps,
    validate_manifest,
)
from gispulse.core.pipeline import PipelineSpec, StepSpec
from gispulse.core.logging import get_logger
from gispulse.orchestration.event_sink import NoOpSink, RunEventSink
from gispulse.orchestration.pipeline_executor import PipelineExecutor

log = get_logger(__name__)

__all__ = [
    "MaterializationMode",
    "RefreshMode",
    "MaterializedModel",
    "Materializer",
    "ManifestRunResult",
    "run_manifest",
    "validate_steps_filter_models",
]


class MaterializationMode(str, Enum):
    """ADR 0005 materialization modes."""

    VIEW = "view"
    TABLE = "table"
    INCREMENTAL = "incremental"


class RefreshMode(str, Enum):
    """ADR 0005 refresh strategies for a materialized model."""

    MANUAL = "manual"
    ON_CHANGE = "on_change"
    SCHEDULE = "schedule"


@dataclass
class MaterializedModel:
    """A model after materialization.

    ``result`` is a :class:`geopandas.GeoDataFrame` for geo sources and a
    plain :class:`pandas.DataFrame` for tabular (non-geo) sources.
    Downstream steps that require geometry must guard against the plain
    DataFrame case or ensure their source carries geometry.
    """

    name: str
    mode: MaterializationMode
    refresh: RefreshMode
    result: "gpd.GeoDataFrame | pd.DataFrame"
    #: Engine table name when ``mode == TABLE`` — ``None`` otherwise.
    table_ref: str | None = None


# ---------------------------------------------------------------------------
# Materializer
# ---------------------------------------------------------------------------


class Materializer:
    """Caches materialized models and applies the per-mode persistence.

    Keeping the cache out of :func:`run_manifest` lets the caller share
    one across multiple runs (incremental refresh, partial reruns).
    """

    def __init__(self, engine: Any | None = None, *, table_prefix: str = "elt_"):
        self._engine = engine
        self._table_prefix = table_prefix
        self._models: dict[str, MaterializedModel] = {}

    @property
    def models(self) -> dict[str, MaterializedModel]:
        """The cache keyed by model name."""
        return self._models

    def get(self, name: str) -> MaterializedModel | None:
        return self._models.get(name)

    def materialize(
        self,
        name: str,
        gdf: "gpd.GeoDataFrame | pd.DataFrame",
        mode: MaterializationMode,
        refresh: RefreshMode = RefreshMode.MANUAL,
    ) -> MaterializedModel:
        """Apply per-mode persistence and cache the result.

        Raises:
            NotImplementedError: ``mode == INCREMENTAL`` (follow-up).
            RuntimeError: ``mode == TABLE`` with no engine attached.
        """
        if mode == MaterializationMode.INCREMENTAL:
            raise NotImplementedError(
                "incremental materialization requires staging.cdc: incremental "
                "and an increment key — wiring lands in the #249 continuation."
            )
        table_ref: str | None = None
        if mode == MaterializationMode.TABLE:
            if self._engine is None:
                raise RuntimeError(
                    "TABLE materialization requires an engine attached to the "
                    "Materializer (register-as-table is engine-mediated)."
                )
            table_ref = f"{self._table_prefix}{name}"
            # Register the gdf on the engine so the Lot 1-3 push-down
            # strategies can JOIN against it from downstream models.
            self._engine.register(table_ref, gdf)
        materialized = MaterializedModel(
            name=name,
            mode=mode,
            refresh=refresh,
            result=gdf,
            table_ref=table_ref,
        )
        self._models[name] = materialized
        return materialized


# ---------------------------------------------------------------------------
# Run a v3 manifest
# ---------------------------------------------------------------------------

#: Signature of a source-loader. Defaults to ``engine.load_layer(uri, layer)``.
#: May return a plain :class:`pandas.DataFrame` for tabular (non-geo) sources.
SourceLoader = Callable[["SourceSpec"], "gpd.GeoDataFrame | pd.DataFrame"]


@dataclass
class ManifestRunResult:
    """Outcome of one :func:`run_manifest` call."""

    materialized: dict[str, MaterializedModel] = field(default_factory=dict)
    #: Topological order in which the models were executed.
    execution_order: list[str] = field(default_factory=list)
    #: Non-blocking assertion failures (``severity=warning``) collected
    #: from each model's ``assert:`` block. Blocking (``severity=error``)
    #: failures raise :class:`AssertionFailedError` before the run
    #: completes — they never land here.
    assertion_warnings: list["AssertionFailure"] = field(default_factory=list)


def _inter_model_edges(manifest: ManifestV3) -> list[tuple[str, str]]:
    """Inter-model dependency edges, used to order execution."""
    model_names = set(manifest.models)
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
    return edges


def _build_sub_pipeline(
    model: ModelSpec, sources: dict[str, SourceSpec], model_names: set[str]
) -> PipelineSpec:
    """Per-model PipelineSpec — the chain of transforms isolated."""
    raw_steps = _compile_model_steps(model, sources, model_names)
    # Inside the sub-pipeline every step that referenced an upstream model
    # collapses to ``input=None`` — the upstream is resolved into the
    # ``inputs`` dict the runner hands to PipelineExecutor.
    own_ids = {s.id for s in raw_steps}
    sub_steps: list[StepSpec] = []
    for s in raw_steps:
        inp: str | list[str] | None = s.input
        if isinstance(inp, str):
            inp = inp if inp in own_ids else None
        elif isinstance(inp, list):
            inp = [x for x in inp if x in own_ids] or None
        sub_steps.append(
            StepSpec(
                id=s.id,
                type=s.type,
                capability=s.capability,
                params=dict(s.params),
                input=inp,
                when=s.when,
                enabled=s.enabled,
                order=s.order,
            )
        )
    return PipelineSpec(version=2, name=model.name, steps=sub_steps)


def validate_steps_filter_models(
    manifest: ManifestV3,
    steps_filter: list[str],
) -> None:
    """Validate that steps_filter does not orphan a capability-upstream model.

    Checks both ``select`` and ``with:`` (ref_layer) inter-model references.
    For each model whose compiled step IDs overlap with ``steps_filter``, all
    referenced upstream models (via ``select`` or ``with:`` transforms) must
    either also be included in the filter OR have no capability steps.

    If an included model references an excluded model that has capability steps,
    a ``ValueError`` is raised with ``code=EXCLUDED_CAPABILITY_UPSTREAM``.

    This function is called from two boundaries:
    - ``run_manifest`` (runtime): raises ``ValueError``.
    - ``manifests_router`` (HTTP): catches ``ValueError`` and returns 422.

    Args:
        manifest:     Parsed v3 manifest.
        steps_filter: List of flat step IDs to include.

    Raises:
        ValueError: When an included model references an excluded model with
            capability steps. Message contains ``code=EXCLUDED_CAPABILITY_UPSTREAM``.
    """
    if not steps_filter:
        return  # no filter → no orphan possible

    model_names = set(manifest.models)
    filter_set = set(steps_filter)

    # Compute per-model step IDs to determine inclusion
    _model_step_ids: dict[str, set[str]] = {}
    for _mn in model_names:
        _sub = _build_sub_pipeline(manifest.models[_mn], manifest.sources, model_names)
        _model_step_ids[_mn] = {s.id for s in _sub.steps}

    included_models = {
        _mn for _mn, _ids in _model_step_ids.items()
        if _ids.intersection(filter_set)
    }

    for _mn in included_models:
        _mdl = manifest.models[_mn]
        # Build sub-spec to find all ref_layer references (with: compiled refs).
        _sub_for_refs = _build_sub_pipeline(_mdl, manifest.sources, model_names)
        _ref_layer_refs = {
            s.params["ref_layer"]
            for s in _sub_for_refs.steps
            if isinstance(s.params.get("ref_layer"), str)
            and s.params["ref_layer"] in model_names  # model refs only
        }
        # Combine: select ref + all with: refs
        _all_upstream_refs: set[str] = set()
        if _mdl.select in model_names:
            _all_upstream_refs.add(_mdl.select)
        _all_upstream_refs.update(_ref_layer_refs)

        for _ref in _all_upstream_refs:
            if _ref not in included_models:
                _ref_sub = _build_sub_pipeline(
                    manifest.models[_ref], manifest.sources, model_names
                )
                _ref_has_cap = any(s.kind == "capability" for s in _ref_sub.steps)
                if _ref_has_cap:
                    raise ValueError(
                        f"steps_filter: included model '{_mn}' references excluded model "
                        f"'{_ref}' (via select or with:) which has capability steps whose "
                        f"in-memory GeoDataFrame output is required. Either include "
                        f"'{_ref}' in the filter or exclude '{_mn}'. "
                        f"code=EXCLUDED_CAPABILITY_UPSTREAM"
                    )


def run_manifest(
    manifest: ManifestV3,
    *,
    engine: Any | None = None,
    source_loader: SourceLoader | None = None,
    materializer: Materializer | None = None,
    event_sink: RunEventSink | None = None,
    run_id: str | None = None,
    steps_filter: list[str] | None = None,
    resume_markers: dict[str, str] | None = None,
) -> ManifestRunResult:
    """Execute a v3 manifest end-to-end.

    Walks the models in topological order and, for each one, resolves
    its ``select`` and any transform-level ``with:`` references into
    real GeoDataFrames (from declared sources or from previously
    materialized models), compiles the model's transforms into a tiny
    :class:`PipelineSpec`, runs it through :class:`PipelineExecutor`,
    and hands the result to the :class:`Materializer` for per-mode
    persistence.

    Args:
        manifest:      The parsed v3 manifest. The runner re-validates
            it through :func:`validate_manifest` so refs / cycles are
            caught even when callers skip the loader's check.
        engine:        Optional :class:`SpatialEngine`. Required when
            *source_loader* is omitted (the default loader calls
            ``engine.load_layer``) or when any model picks ``materialize:
            table`` (register-as-table goes through the engine).
        source_loader: Override for source loading — useful for tests or
            for in-memory inputs that don't sit behind ``load_layer``.
        materializer:  Reuse an existing materializer to share its cache
            across multiple runs. A fresh one is created when omitted.

    Returns:
        :class:`ManifestRunResult` carrying the materialized models and
        the execution order. The same data also lives on the supplied
        materializer's ``models`` dict.
    """
    validate_manifest(manifest)  # raises on cycles / unresolved refs

    if source_loader is None:
        if engine is None:
            raise RuntimeError(
                "run_manifest requires either an engine (uses engine.load_layer) "
                "or a source_loader callable"
            )

        def source_loader(src: SourceSpec) -> gpd.GeoDataFrame:
            return engine.load_layer(src.uri, layer=src.layer or "")

    if materializer is None:
        materializer = Materializer(engine=engine)

    model_names = set(manifest.models)
    order = topological_sort(list(model_names), _inter_model_edges(manifest))
    log.info(
        "manifest_run_start",
        manifest=manifest.name or "(unnamed)",
        n_models=len(model_names),
        order=order,
    )

    # --- Build an effective filter set (flat step IDs → per-model inclusion map) ---
    # steps_filter contains flat PipelineSpec step IDs. These correspond
    # directly to the sub-spec step IDs produced by _build_sub_pipeline
    # (terminal step id = model name, intermediates = <model>__t<n>).
    # Strategy:
    #   1. Compute the flat step IDs for each model.
    #   2. A model is INCLUDED if at least one of its step IDs is in the filter.
    #   3. Models not included are SKIPPED: primary is materialised as passthrough
    #      WITHOUT executing any steps (critical: no subprocess, no capability exec).
    #   4. Validation delegated to validate_steps_filter_models: covers both
    #      select refs AND with: (ref_layer) refs to excluded capability models.
    _effective_filter: set[str] | None = None
    _included_models: set[str] = set(model_names)  # default = all included
    if steps_filter:
        _effective_filter = set(steps_filter)
        # Validate before computing inclusion — raises ValueError on orphan capability.
        validate_steps_filter_models(manifest, steps_filter)
        # Compute per-model step IDs to determine inclusion
        _model_step_ids: dict[str, set[str]] = {}
        for _mn in model_names:
            _sub = _build_sub_pipeline(manifest.models[_mn], manifest.sources, model_names)
            _model_step_ids[_mn] = {s.id for s in _sub.steps}
        _included_models = {
            _mn for _mn, _ids in _model_step_ids.items()
            if _ids.intersection(_effective_filter)
        }

    source_cache: "dict[str, gpd.GeoDataFrame | pd.DataFrame]" = {}

    def _resolve(ref: str) -> "gpd.GeoDataFrame | pd.DataFrame":
        if ref in manifest.sources:
            if ref not in source_cache:
                source_cache[ref] = source_loader(manifest.sources[ref])
            return source_cache[ref]
        cached = materializer.get(ref)
        if cached is not None:
            return cached.result
        raise KeyError(
            f"run_manifest: reference {ref!r} resolves to neither a declared "
            "source nor a previously materialized model"
        )

    _sink = event_sink if event_sink is not None else NoOpSink()
    assertion_warnings: list = []
    _resume_markers: dict[str, str] = resume_markers or {}
    executed_order: list[str] = []  # models actually materialized (filter-aware)

    for model_name in order:
        model = manifest.models[model_name]

        if model_name not in _included_models:
            # --- Skipped model: materialise primary as passthrough, NO steps executed ---
            # This is intentionally a passthrough and NOT a NoOpSink-execute.
            # No subprocess is spawned, no capability pipeline runs.
            # The passthrough puts the raw source GDF into the materializer so
            # that downstream models referencing this one via _resolve() can find it.
            # Safety: the validation above guarantees no included model depends on
            # this model's capability output.
            log.debug(
                "manifest_model_skipped_by_filter",
                model=model_name,
                steps_filter=list(_effective_filter) if _effective_filter else [],
            )
            primary = _resolve(model.select)
            materializer.materialize(
                model_name,
                primary,
                MaterializationMode(model.materialize),
                RefreshMode(model.refresh),
            )
            # Do NOT add to executed_order — the model was not run.
            continue

        # --- Included model: build sub-spec, optionally filtered ---
        sub_spec = _build_sub_pipeline(model, manifest.sources, model_names)

        if _effective_filter is not None:
            # Keep only the listed steps within this model's sub-spec.
            from gispulse.core.pipeline import PipelineSpec as _PS
            filtered_steps = [s for s in sub_spec.steps if s.id in _effective_filter]
            sub_spec = _PS(
                version=sub_spec.version,
                name=sub_spec.name,
                steps=filtered_steps,
                triggers=sub_spec.triggers,
                ref_layers=sub_spec.ref_layers,
            )

        primary = _resolve(model.select)

        # --- resume_markers: pass per-step markers to this model's executor ---
        model_step_ids_for_resume = {s.id for s in sub_spec.steps}
        model_resume_markers = {
            sid: marker
            for sid, marker in _resume_markers.items()
            if sid in model_step_ids_for_resume
        }

        # Inputs: primary first (PipelineExecutor's linear path keys
        # off the first value); any `with: <ref>` resolves into a named
        # entry that the existing ``ref_layer`` plumbing picks up.
        # When the primary is a plain pd.DataFrame (tabular/non-geo source)
        # and the model has no transform steps, we short-circuit the executor
        # to avoid handing a non-GeoDataFrame to geometry-aware capabilities.
        inputs: "dict[str, gpd.GeoDataFrame | pd.DataFrame]" = {model.select: primary}
        for step in sub_spec.steps:
            alias = step.params.get("ref_layer")
            if isinstance(alias, str) and alias not in inputs:
                inputs[alias] = _resolve(alias)

        # Tabular guard: a plain pd.DataFrame source cannot be passed to
        # geometry-aware capability steps. We distinguish two sub-cases:
        #
        # 1. Source is tabular AND the model has declared transform steps
        #    → FAIL EXPLICITLY with a message naming the model, so the author
        #    can fix their manifest rather than receiving silently wrong data.
        # 2. Source is tabular AND no transform steps (passthrough)
        #    → short-circuit: skip PipelineExecutor and use primary as terminal.
        if not isinstance(primary, gpd.GeoDataFrame) and isinstance(primary, pd.DataFrame):
            real_transforms = [t for t in (model.transform or []) if isinstance(t, dict)]
            if real_transforms:
                n = len(real_transforms)
                raise ValueError(
                    f"run_manifest: model '{model_name}' declares {n} transform "
                    f"step(s) but its source '{model.select}' is tabular "
                    f"(no geometry). Capability steps require a GeoDataFrame. "
                    f"Either use a geo source or remove the transform steps."
                )
            # Passthrough — no transforms, materialise primary directly.
            results: "dict[str, gpd.GeoDataFrame | pd.DataFrame]" = {}
        else:
            # For geo sources, all inputs must be GeoDataFrames — cast check
            # delegated to capabilities at call time (existing behaviour).
            # A per-model executor is created so resume_markers are scoped to
            # the current model's step IDs (no cross-model marker leakage).
            model_executor = PipelineExecutor(
                execution_context=None,
                resume_markers=model_resume_markers if model_resume_markers else None,
            )
            results = model_executor.execute(sub_spec, inputs, None, event_sink=_sink, run_id=run_id)  # type: ignore[arg-type]

        # The model's output is the last step's result — fall back to
        # any single produced output, then to the primary, so a
        # transform-less passthrough still materializes something.
        if model_name in results:
            terminal = results[model_name]
        elif results:
            terminal = list(results.values())[-1]
        else:
            terminal = primary

        materializer.materialize(
            model_name,
            terminal,
            MaterializationMode(model.materialize),
            RefreshMode(model.refresh),
        )
        log.debug(
            "manifest_model_materialized",
            model=model_name,
            mode=model.materialize,
            rows=len(terminal),
        )

        # ELT Lot 4F (#252) — data-quality gates. ``severity=error``
        # failures raise immediately so the user sees the offending
        # model before downstream models run on a tainted output;
        # ``severity=warning`` failures collect on the run result.
        if model.assertions:
            from gispulse.core.assertions import run_assertions

            failures = run_assertions(
                model_name, terminal, model.assertions, raise_on_error=True
            )
            for f in failures:
                log.warning(
                    "assertion_warning",
                    model=f.model,
                    kind=f.kind,
                    message=f.message,
                )
            assertion_warnings.extend(failures)

        executed_order.append(model_name)

    return ManifestRunResult(
        materialized=dict(materializer.models),
        execution_order=executed_order,
        assertion_warnings=assertion_warnings,
    )
