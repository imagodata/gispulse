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
from typing import Any

import geopandas as gpd

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
from gispulse.orchestration.pipeline_executor import PipelineExecutor

log = get_logger(__name__)

__all__ = [
    "MaterializationMode",
    "RefreshMode",
    "MaterializedModel",
    "Materializer",
    "ManifestRunResult",
    "run_manifest",
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
    """A model after materialization."""

    name: str
    mode: MaterializationMode
    refresh: RefreshMode
    result: gpd.GeoDataFrame
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
        gdf: gpd.GeoDataFrame,
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
SourceLoader = Callable[[SourceSpec], gpd.GeoDataFrame]


@dataclass
class ManifestRunResult:
    """Outcome of one :func:`run_manifest` call."""

    materialized: dict[str, MaterializedModel] = field(default_factory=dict)
    #: Topological order in which the models were executed.
    execution_order: list[str] = field(default_factory=list)


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


def run_manifest(
    manifest: ManifestV3,
    *,
    engine: Any | None = None,
    source_loader: SourceLoader | None = None,
    materializer: Materializer | None = None,
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

    source_cache: dict[str, gpd.GeoDataFrame] = {}

    def _resolve(ref: str) -> gpd.GeoDataFrame:
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

    executor = PipelineExecutor(execution_context=None)

    for model_name in order:
        model = manifest.models[model_name]
        primary = _resolve(model.select)
        sub_spec = _build_sub_pipeline(model, manifest.sources, model_names)

        # Inputs: primary GDF first (PipelineExecutor's linear path keys
        # off the first value); any `with: <ref>` resolves into a named
        # entry that the existing ``ref_layer`` plumbing picks up.
        inputs: dict[str, gpd.GeoDataFrame] = {model.select: primary}
        for step in sub_spec.steps:
            alias = step.params.get("ref_layer")
            if isinstance(alias, str) and alias not in inputs:
                inputs[alias] = _resolve(alias)

        results = executor.execute(sub_spec, inputs)

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

    return ManifestRunResult(
        materialized=dict(materializer.models),
        execution_order=order,
    )
