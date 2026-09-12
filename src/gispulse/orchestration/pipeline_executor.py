"""Unified pipeline executor for GISPulse.

Executes a :class:`PipelineSpec` by converting it to a graph and delegating
to :class:`GraphExecutor`, or running steps linearly for simple pipelines.

This is the convergence point for the 4 previous execution paths
(SessionManager, JobRunner, ScenarioRunner, GraphExecutor). New code
should prefer ``PipelineExecutor`` over the older APIs.

Usage::

    from gispulse.core.pipeline import load_pipeline
    from gispulse.orchestration.pipeline_executor import PipelineExecutor

    spec = load_pipeline("pipeline.json")
    executor = PipelineExecutor()
    results = executor.execute(spec, {"input": gdf})
"""

from __future__ import annotations

from typing import Any, Callable

import geopandas as gpd

from gispulse.core.graph import EdgeDef, NodeDef, NodeType
from gispulse.core.logging import get_logger
from gispulse.core.pipeline import PipelineSpec, StepSpec
from gispulse.orchestration.event_sink import NoOpSink, RunEventSink

log = get_logger(__name__)


def _persist_step_artifacts(
    run_id: str,
    step_id: str,
    artifacts: dict,
    run_repo: Any,
) -> None:
    """Persist step artifacts into the PipelineRunStep in the run repository.

    Called after a non-capability step completes so that log_tail, skip_marker,
    and other artifacts are available via GET /runs/{id}.

    Args:
        run_id:   String representation of the run UUID.
        step_id:  Step id within the run.
        artifacts: Artifacts dict from StepResult.
        run_repo:  RunRepository (or any repo with .get(UUID) / .save(run)).
    """
    from uuid import UUID

    try:
        uid = UUID(run_id)
        run = run_repo.get(uid)
        if run is None:
            return
        step = next((s for s in run.steps if s.step_id == step_id), None)
        if step is not None:
            step.artifacts = artifacts
            run_repo.save(run)
    except Exception as exc:  # noqa: BLE001 — never abort execution for persistence failure
        log.warning(
            "persist_step_artifacts_failed",
            run_id=run_id,
            step_id=step_id,
            error=str(exc),
        )


def _execute_step_kind(
    step: "StepSpec",
    *,
    run_id: str,
    scope: str,
    event_sink: "RunEventSink",
    cancel_check: Callable[[], bool],
    heartbeat: Callable[[], None],
    run_repo: Any | None = None,
    resume_marker: str = "",
) -> "Any":
    """Dispatch a non-capability step to the step-kind registry.

    Returns a :class:`~gispulse.orchestration.step_kinds.StepResult`.
    Raises :exc:`KeyError` if the kind is not registered (surfaces as a
    failed step with a clear message).

    Args:
        resume_marker: Opaque token from the previous run's step artifacts
                       (``PipelineRunStep.artifacts["skip_marker"]``).
                       Passed to ``StepContext.resume_marker`` so the
                       ``external`` kind can inject it as
                       ``GISPULSE_RESUME_MARKER`` in the subprocess env.
    """
    from gispulse.orchestration.step_kinds import (
        StepContext,
        _ensure_builtin_kinds,
        get_step_kind_handler,
    )

    _ensure_builtin_kinds()
    handler = get_step_kind_handler(step.kind)
    if handler is None:
        from gispulse.orchestration.step_kinds import StepResult
        return StepResult(
            status="failed",
            error=(
                f"step '{step.id}': unknown step kind {step.kind!r}; "
                "register it with register_step_kind() before executing this pipeline"
            ),
        )

    ctx = StepContext(
        run_id=run_id,
        step_id=step.id,
        scope=scope,
        event_sink=event_sink,
        cancel_check=cancel_check,
        heartbeat=heartbeat,
        resume_marker=resume_marker,
    )
    return handler(step.params, ctx)


def _validate_step_params(capability_instance: Any, step_id: str, params: dict) -> None:
    """Validate step params against the capability instance schema; raise if invalid.

    Strips plumbing keys (ref_gdf/ref_layer) that are not part of the capability
    JSON schema before validating. Uses the already-resolved capability instance
    so test stubs work (they bypass the global registry).
    """
    from gispulse.rules.validation import _PLUMBING_KEYS, validate_params_for_instance

    filtered = {k: v for k, v in params.items() if k not in _PLUMBING_KEYS}
    result = validate_params_for_instance(capability_instance, filtered, field_prefix="params")
    if not result.valid:
        name = getattr(capability_instance, "name", "?")
        details = "; ".join(f"[{e.field}] {e.message}" for e in result.errors)
        raise ValueError(f"Step '{step_id}' ({name}) invalid params: {details}")


def _auto_inject_crs_meters(
    capability_instance: Any,
    step_id: str,
    params: dict,
    gdf: gpd.GeoDataFrame,
) -> None:
    """Inject a metric CRS when the capability needs one and the input is angular.

    Many capabilities (buffer, area_length, isochrone, …) expose a
    ``crs_meters`` schema param. If the user omits it and the primary
    GeoDataFrame is in a geographic CRS (e.g. EPSG:4326), computed
    distances and areas are silently wrong. This helper fills in a
    sensible metric CRS (EPSG:2154 in France, local UTM elsewhere) and
    logs a single line so users can spot the auto-reprojection.
    """
    from gispulse.core.crs import is_angular, suggest_metric_crs

    if "crs_meters" in params:
        return
    if not is_angular(gdf):
        return
    try:
        schema = capability_instance.get_schema()
    except Exception:
        return
    props = (schema or {}).get("properties", {})
    if "crs_meters" not in props:
        return

    crs = suggest_metric_crs(gdf)
    params["crs_meters"] = crs
    log.info(
        "pipeline_auto_crs_meters",
        step_id=step_id,
        capability=getattr(capability_instance, "name", "?"),
        crs_meters=crs,
    )


class PipelineExecutor:
    """Execute a PipelineSpec — unified entry point.

    For linear pipelines (no step references another), runs steps
    sequentially for simplicity. For DAG pipelines, converts to
    NodeDef/EdgeDef and delegates to GraphExecutor.

    Args:
        capability_getter: ``(name) -> Capability`` — defaults to
            ``capabilities.registry.get``.
        execution_context: Optional engine context for strategy-based
            execution (DuckDB/PostGIS acceleration).
        cancel_check:   Callable returning ``True`` when the current job
                        has been cancelled.  Passed to non-capability step
                        handlers via :class:`~step_kinds.StepContext`.
        heartbeat:      Callable that the executor calls periodically
                        during non-capability step execution so the
                        worker's stuck-job recovery does not kill a
                        healthy long-running step.
        scope:          Optional scope string propagated to StepContext.
        run_repo:       Optional run repository for persisting step
                        artifacts after non-capability step completion.
    """

    def __init__(
        self,
        capability_getter: Callable[[str], Any] | None = None,
        execution_context: Any | None = None,
        *,
        cancel_check: Callable[[], bool] | None = None,
        heartbeat: Callable[[], None] | None = None,
        scope: str = "",
        run_repo: Any | None = None,
        resume_markers: dict[str, str] | None = None,
    ) -> None:
        if capability_getter is None:
            from gispulse.capabilities import get as _get
            capability_getter = _get
        self._get_cap = capability_getter
        self._execution_context = execution_context
        self._cancel_check: Callable[[], bool] = cancel_check or (lambda: False)
        self._heartbeat: Callable[[], None] = heartbeat or (lambda: None)
        self._scope = scope
        self._run_repo = run_repo
        # resume_markers: step_id → skip_marker from the source run's artifacts.
        # When non-empty, the external kind injects the marker as
        # GISPULSE_RESUME_MARKER in the subprocess environment so the script
        # can restart from a known checkpoint.
        self._resume_markers: dict[str, str] = resume_markers or {}

    def execute(
        self,
        spec: PipelineSpec,
        inputs: dict[str, gpd.GeoDataFrame],
        params: dict[str, Any] | None = None,
        *,
        event_sink: RunEventSink | None = None,
        run_id: str | None = None,
    ) -> dict[str, gpd.GeoDataFrame]:
        """Execute a pipeline specification.

        Args:
            spec:       The pipeline to execute.
            inputs:     Named input GeoDataFrames. For linear pipelines, the
                        first value is used as the primary input. For DAG
                        pipelines, keys must match dataset node ids.
            params:     Template parameters for ``$var`` substitution.
            event_sink: Optional sink for lifecycle events (run.step.started,
                        run.step.completed, run.step.failed). Defaults to
                        NoOpSink — backward-compat: all existing callers that
                        omit this parameter are unaffected.
            run_id:     Optional run identifier included in emitted events.

        Returns:
            Dict of step-id → result GeoDataFrame for all steps that
            produced output.
        """
        sink = event_sink if event_sink is not None else NoOpSink()
        if spec.is_dag:
            return self._execute_dag(spec, inputs, params, event_sink=sink, run_id=run_id)
        return self._execute_linear(spec, inputs, event_sink=sink, run_id=run_id)

    # ------------------------------------------------------------------
    # Linear execution (simple pipeline, no DAG)
    # ------------------------------------------------------------------

    def _execute_linear(
        self,
        spec: PipelineSpec,
        inputs: dict[str, gpd.GeoDataFrame],
        *,
        event_sink: RunEventSink | None = None,
        run_id: str | None = None,
    ) -> dict[str, gpd.GeoDataFrame]:
        """Run steps sequentially, piping output of each to the next."""
        from datetime import datetime, timezone

        sink = event_sink if event_sink is not None else NoOpSink()
        # Use the first input as the primary GeoDataFrame.
        # For pipelines that contain only non-capability steps (e.g. manifests
        # whose models have select=None), inputs may be empty — initialize gdf
        # lazily so those pipelines don't raise here.
        gdf: gpd.GeoDataFrame | None = next(iter(inputs.values()), None)
        results: dict[str, gpd.GeoDataFrame] = {}

        for step in spec.enabled_steps:
            # --- Non-capability kind: dispatch to step-kind registry ------
            if step.kind != "capability":
                step_started_at = datetime.now(timezone.utc)
                sink.emit("run.step.started", {
                    "run_id": run_id or "",
                    "step_id": step.id,
                    "started_at": step_started_at.isoformat(),
                })
                try:
                    step_result = _execute_step_kind(
                        step,
                        run_id=run_id or "",
                        scope=self._scope,
                        event_sink=sink,
                        cancel_check=self._cancel_check,
                        heartbeat=self._heartbeat,
                        run_repo=self._run_repo,
                        resume_marker=self._resume_markers.get(step.id, ""),
                    )
                except Exception as exc:
                    step_ended_at = datetime.now(timezone.utc)
                    sink.emit("run.step.failed", {
                        "run_id": run_id or "",
                        "step_id": step.id,
                        "status": "failed",
                        "ended_at": step_ended_at.isoformat(),
                        "error": str(exc),
                    })
                    raise

                step_ended_at = datetime.now(timezone.utc)
                # Persist artifacts to PipelineRunStep if run_repo is available
                # (done via the sink's RecordingSink path — we emit the event
                # with artifacts so RecordingSink can forward them)
                if step_result.status == "failed":
                    sink.emit("run.step.failed", {
                        "run_id": run_id or "",
                        "step_id": step.id,
                        "status": "failed",
                        "ended_at": step_ended_at.isoformat(),
                        "error": step_result.error,
                        "artifacts": step_result.artifacts,
                        "metrics": step_result.metrics,
                    })
                    raise RuntimeError(
                        f"Step '{step.id}' (kind={step.kind!r}) failed: "
                        f"{step_result.error}"
                    )
                sink.emit("run.step.completed", {
                    "run_id": run_id or "",
                    "step_id": step.id,
                    "status": "completed",
                    "ended_at": step_ended_at.isoformat(),
                    "artifacts": step_result.artifacts,
                    "metrics": step_result.metrics,
                })
                # Store artifacts on the PipelineRunStep via run_repo if available
                if self._run_repo is not None:
                    _persist_step_artifacts(
                        run_id or "", step.id, step_result.artifacts, self._run_repo
                    )
                log.info(
                    "pipeline_kind_step_done",
                    step_id=step.id,
                    kind=step.kind,
                    status=step_result.status,
                )
                continue

            # --- Capability path (existing unchanged behaviour) -----------
            if step.type != "capability" or not step.capability:
                log.warning("pipeline_skip_non_capability", step_id=step.id, step_type=step.type)
                continue

            # Conditional execution
            if step.when is not None and not self._evaluate_when(step, gdf):
                log.debug("pipeline_step_skipped_by_when", step_id=step.id)
                results[step.id] = gdf
                continue

            step_started_at = datetime.now(timezone.utc)
            sink.emit("run.step.started", {
                "run_id": run_id or "",
                "step_id": step.id,
                "started_at": step_started_at.isoformat(),
            })

            try:
                cap = self._get_cap(step.capability)

                # Resolve ref_layer from spec.ref_layers if present. ``pop``
                # (not ``get``) so the original alias key doesn't leak into
                # the capability call — execute_safe rejects unknown kwargs,
                # and ``ref_layer`` is a pipeline-level routing field that
                # capabilities never see directly (they consume ``ref_gdf``).
                params = dict(step.params)
                ref_layer_alias = params.pop("ref_layer", None)
                if ref_layer_alias and ref_layer_alias in inputs:
                    params["ref_gdf"] = inputs[ref_layer_alias]

                # Plural variant: list of ref layers → list of GeoDataFrames. Used
                # by merge_layers to stack N layers at once; sibling of the scalar
                # ``ref_layer``/``ref_gdf`` plumbing above. Silently skips aliases
                # not yet produced so the capability can tolerate partial inputs.
                ref_layers_aliases = params.pop("ref_layers", None)
                if isinstance(ref_layers_aliases, list) and ref_layers_aliases:
                    params["ref_gdfs"] = [
                        inputs[a] for a in ref_layers_aliases if a in inputs
                    ]

                _auto_inject_crs_meters(cap, step.id, params, gdf)
                _validate_step_params(cap, step.id, params)

                if self._execution_context is not None and hasattr(cap, "execute_with_context"):
                    from gispulse.capabilities.strategy import ExecutionContext
                    ctx = ExecutionContext(
                        engine=self._execution_context.engine,
                        feature_count=len(gdf),
                        has_spatial_index=self._execution_context.has_spatial_index,
                        params=params,
                    )
                    gdf = cap.execute_with_context(gdf, ctx)
                else:
                    from gispulse.capabilities.base import safe_execute
                    gdf = safe_execute(cap, gdf, **params)
            except Exception as exc:
                step_ended_at = datetime.now(timezone.utc)
                sink.emit("run.step.failed", {
                    "run_id": run_id or "",
                    "step_id": step.id,
                    "status": "failed",
                    "ended_at": step_ended_at.isoformat(),
                    "error": str(exc),
                })
                raise

            step_ended_at = datetime.now(timezone.utc)
            sink.emit("run.step.completed", {
                "run_id": run_id or "",
                "step_id": step.id,
                "status": "completed",
                "ended_at": step_ended_at.isoformat(),
            })

            results[step.id] = gdf
            log.debug("pipeline_step_done", step_id=step.id, features=len(gdf))

        return results

    # ------------------------------------------------------------------
    # DAG execution (delegates to GraphExecutor)
    # ------------------------------------------------------------------

    def _execute_dag(
        self,
        spec: PipelineSpec,
        inputs: dict[str, gpd.GeoDataFrame],
        params: dict[str, Any] | None = None,
        *,
        event_sink: RunEventSink | None = None,
        run_id: str | None = None,
    ) -> dict[str, gpd.GeoDataFrame]:
        """Run non-capability DAG steps, then delegate capability nodes."""
        from datetime import datetime, timezone

        from gispulse.orchestration.graph_executor import GraphExecutor

        sink = event_sink if event_sink is not None else NoOpSink()
        from gispulse.core.pipeline import validate_step_kind_inputs

        validation_errors = validate_step_kind_inputs(spec)
        if validation_errors:
            raise ValueError(
                "step kind validation failed: " + "; ".join(validation_errors)
            )

        non_cap_steps = [s for s in spec.enabled_steps if s.kind != "capability"]
        cap_steps = [s for s in spec.enabled_steps if s.kind == "capability"]

        for step in non_cap_steps:
            step_started_at = datetime.now(timezone.utc)
            sink.emit("run.step.started", {
                "run_id": run_id or "",
                "step_id": step.id,
                "started_at": step_started_at.isoformat(),
            })
            try:
                step_result = _execute_step_kind(
                    step,
                    run_id=run_id or "",
                    scope=self._scope,
                    event_sink=sink,
                    cancel_check=self._cancel_check,
                    heartbeat=self._heartbeat,
                    run_repo=self._run_repo,
                    resume_marker=self._resume_markers.get(step.id, ""),
                )
            except Exception as exc:
                step_ended_at = datetime.now(timezone.utc)
                sink.emit("run.step.failed", {
                    "run_id": run_id or "",
                    "step_id": step.id,
                    "status": "failed",
                    "ended_at": step_ended_at.isoformat(),
                    "error": str(exc),
                })
                raise

            step_ended_at = datetime.now(timezone.utc)
            if step_result.status == "failed":
                sink.emit("run.step.failed", {
                    "run_id": run_id or "",
                    "step_id": step.id,
                    "status": "failed",
                    "ended_at": step_ended_at.isoformat(),
                    "error": step_result.error,
                    "artifacts": step_result.artifacts,
                    "metrics": step_result.metrics,
                })
                raise RuntimeError(
                    f"Step '{step.id}' (kind={step.kind!r}) failed: {step_result.error}"
                )

            sink.emit("run.step.completed", {
                "run_id": run_id or "",
                "step_id": step.id,
                "status": "completed",
                "ended_at": step_ended_at.isoformat(),
                "artifacts": step_result.artifacts,
                "metrics": step_result.metrics,
            })
            if self._run_repo is not None:
                _persist_step_artifacts(run_id or "", step.id, step_result.artifacts, self._run_repo)

        if not cap_steps:
            return {}

        nodes, edges, dataset_inputs = self._spec_to_graph(spec, inputs)

        executor = GraphExecutor(
            capability_getter=self._get_cap,
            execution_context=self._execution_context,
            event_sink=sink,
            run_id=run_id,
        )
        return executor.execute(nodes, edges, dataset_inputs, params or {})

    def _spec_to_graph(
        self,
        spec: PipelineSpec,
        inputs: dict[str, gpd.GeoDataFrame],
    ) -> tuple[list[NodeDef], list[EdgeDef], dict[str, gpd.GeoDataFrame]]:
        """Convert a PipelineSpec into NodeDef/EdgeDef lists."""
        nodes: list[NodeDef] = []
        edges: list[EdgeDef] = []
        dataset_inputs: dict[str, gpd.GeoDataFrame] = {}

        # Create a dataset node for each input
        for key, gdf in inputs.items():
            ds_node_id = f"_input_{key}"
            nodes.append(NodeDef(id=ds_node_id, node_type=NodeType.DATASET, bind=key))
            dataset_inputs[ds_node_id] = gdf

        # Track which step is the first without an explicit input
        first_input_key = f"_input_{next(iter(inputs))}" if inputs else None
        step_ids = {s.id for s in spec.enabled_steps if s.kind == "capability"}

        def _resolve_source(src: str) -> str:
            # Allow step.input to reference a ref-layer / dataset alias
            # directly (e.g. ``"cours_eau"``). Step ids win over dataset
            # aliases to preserve normal chaining semantics.
            if src in step_ids:
                return src
            if f"_input_{src}" in dataset_inputs:
                return f"_input_{src}"
            return src

        for step in spec.enabled_steps:
            if step.kind != "capability" or step.type != "capability":
                continue

            node = NodeDef(
                id=step.id,
                node_type=NodeType.CAPABILITY,
                capability=step.capability or "",
                params=dict(step.params),
            )
            nodes.append(node)

            # Wire edges
            if step.input is not None:
                # Explicit upstream reference(s)
                sources = step.input if isinstance(step.input, list) else [step.input]
                for src in sources:
                    edges.append(EdgeDef(source=_resolve_source(src), target=step.id))
            elif first_input_key:
                # No explicit input — connect to the first dataset node
                edges.append(EdgeDef(source=first_input_key, target=step.id))
                # After the first step, subsequent steps without input
                # should chain from the previous step (linear fallback)
                first_input_key = None

            # Implicit dependency: if ``ref_layer`` points to another step's
            # id, add an ordering edge so the ref step runs first. Ref layer
            # aliases (dataset inputs) don't need this — they exist from t=0.
            # The ``ref_dep`` handle marks this as an ordering-only edge so the
            # graph executor does not feed the ref gdf as a data input to the
            # capability (the ``ref_layer`` param resolves it from the live
            # results dict instead).
            ref_alias = step.params.get("ref_layer") if isinstance(step.params, dict) else None
            if isinstance(ref_alias, str) and ref_alias in step_ids and ref_alias != step.id:
                edges.append(EdgeDef(source=ref_alias, target=step.id, handle="ref_dep"))

            # Plural ref_layers: add one ref_dep edge per referenced step so
            # the DAG ordering guarantees all of them run before this node.
            ref_aliases = step.params.get("ref_layers") if isinstance(step.params, dict) else None
            if isinstance(ref_aliases, list):
                for alias in ref_aliases:
                    if isinstance(alias, str) and alias in step_ids and alias != step.id:
                        edges.append(EdgeDef(source=alias, target=step.id, handle="ref_dep"))

        return nodes, edges, dataset_inputs

    # ------------------------------------------------------------------
    # Predicate evaluation for conditional steps
    # ------------------------------------------------------------------

    @staticmethod
    def _evaluate_when(step: StepSpec, gdf: gpd.GeoDataFrame) -> bool:
        """Evaluate a step's ``when`` predicate against the current GDF.

        For attr predicates, checks if ANY row in the GDF satisfies the
        condition. This is a lightweight check — full per-row filtering
        should use a filter capability step instead.
        """
        from gispulse.core.predicates import AttrPredicate

        pred = step.when
        if pred is None:
            return True

        if isinstance(pred, AttrPredicate):
            if pred.field not in gdf.columns:
                return False
            col = gdf[pred.field]
            if pred.op == "eq":
                return (col == pred.value).any()
            if pred.op == "neq":
                return (col != pred.value).any()
            if pred.op == "gt":
                return (col > pred.value).any()
            if pred.op == "lt":
                return (col < pred.value).any()
            if pred.op == "gte":
                return (col >= pred.value).any()
            if pred.op == "lte":
                return (col <= pred.value).any()
            return True

        # For compound/geom predicates, default to True (execute the step)
        return True
