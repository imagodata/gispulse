"""
Pipelines router for the GISPulse HTTP API (v2).

Endpoints:
    POST  /api/pipelines/execute   — execute a PipelineSpec v2 against a dataset
    POST  /api/pipelines/validate  — validate a PipelineSpec without executing
    GET   /api/pipelines/examples  — return example pipeline definitions
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from gispulse.adapters.http.dependencies import get_dataset_repo
from core.logging import get_logger
from persistence.repository import Repository
from persistence.tier import TierError, check_tier

if TYPE_CHECKING:
    from core.pipeline import PipelineSpec

log = get_logger(__name__)


def _gate_dag_executor(steps: list) -> None:
    """DAG executor is a Pro+ feature (cf. pricing.yml `dag_executor`).

    Single-step pipelines (linear, 1 capability) remain available to community
    tier as it covers the basic capability-application use case.  Multi-step
    pipelines and pipelines with branches require Pro.
    """
    if not steps or len(steps) <= 1:
        return
    try:
        check_tier("pro")
    except TierError as exc:
        raise HTTPException(status_code=402, detail=str(exc))


router = APIRouter(prefix="/pipelines", tags=["pipelines"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class StepSpecIn(BaseModel):
    """A single step in a pipeline request."""

    id: str = Field("", description="Unique step identifier.")
    type: str = Field("capability", description="Step kind: capability, filter, spatial_op, custom_sql.")
    capability: str | None = Field(None, description="Capability name (required for type=capability).")
    params: dict[str, Any] = Field(default_factory=dict, description="Parameters for the capability.")
    input: str | list[str] | None = Field(None, description="Upstream step(s) reference.")
    enabled: bool = Field(True, description="Whether the step is active.")
    order: int = Field(0, description="Explicit ordering for linear pipelines.")


class TriggerSpecIn(BaseModel):
    """Inline trigger specification."""

    on: str = Field("", description="Event descriptor (dml:table:INSERT, schedule:cron, manual).")
    then: str = Field("run_pipeline", description="Action type.")
    then_config: dict[str, Any] = Field(default_factory=dict, description="Action configuration.")


class PipelineExecuteRequest(BaseModel):
    """Request to execute a PipelineSpec v2."""

    name: str = Field("", description="Pipeline name.")
    description: str = Field("", description="Pipeline description.")
    steps: list[StepSpecIn] = Field(..., description="Processing steps.", min_length=1)
    triggers: list[TriggerSpecIn] = Field(default_factory=list, description="Inline triggers.")
    ref_layers: dict[str, str] = Field(default_factory=dict, description="Named reference layers.")
    dataset_id: UUID | None = Field(None, description="Dataset UUID to execute against.")
    input_path: str | None = Field(None, description="Direct file path (alternative to dataset_id).")
    layer: str | None = Field(None, description="Layer name for multi-layer inputs.")


class StepResultOut(BaseModel):
    """Result of a single pipeline step."""

    step_id: str
    features_count: int
    columns: list[str]


class PipelineExecuteResponse(BaseModel):
    """Response from pipeline execution."""

    pipeline_name: str
    steps_executed: int
    step_results: list[StepResultOut]
    total_features_out: int
    is_dag: bool


class PipelineValidateRequest(BaseModel):
    """Request to validate a PipelineSpec without executing."""

    steps: list[StepSpecIn] = Field(..., description="Processing steps.", min_length=1)
    ref_layers: dict[str, str] = Field(default_factory=dict, description="Named reference layers.")


class ValidationIssue(BaseModel):
    """A single validation issue."""

    step_id: str
    level: str = Field(description="'error' or 'warning'.")
    message: str


class PipelineValidateResponse(BaseModel):
    """Validation result."""

    valid: bool
    issues: list[ValidationIssue]


class PipelineExampleOut(BaseModel):
    """An example pipeline definition."""

    name: str
    description: str
    spec: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_pipeline_spec(steps: list[StepSpecIn], name: str = "", description: str = "",
                      triggers: list[TriggerSpecIn] | None = None,
                      ref_layers: dict[str, str] | None = None) -> "PipelineSpec":
    """Convert request models to internal PipelineSpec."""
    from core.pipeline import PipelineSpec, StepSpec, TriggerSpec

    parsed_steps = []
    for i, s in enumerate(steps):
        parsed_steps.append(StepSpec(
            id=s.id or f"step_{i}",
            type=s.type,
            capability=s.capability,
            params=dict(s.params),
            input=s.input,
            enabled=s.enabled,
            order=s.order or i,
        ))

    parsed_triggers = []
    for t in (triggers or []):
        parsed_triggers.append(TriggerSpec(
            on=t.on,
            then=t.then,
            then_config=dict(t.then_config),
        ))

    return PipelineSpec(
        version=2,
        name=name,
        description=description,
        steps=parsed_steps,
        triggers=parsed_triggers,
        ref_layers=dict(ref_layers or {}),
    )


def _validate_pipeline(steps: list[StepSpecIn], ref_layers: dict[str, str] | None = None) -> list[ValidationIssue]:
    """Validate pipeline steps without executing."""
    from capabilities import list_all as list_capabilities

    issues: list[ValidationIssue] = []
    known_caps = {c["name"] for c in list_capabilities()}
    step_ids = set()

    for i, step in enumerate(steps):
        sid = step.id or f"step_{i}"

        # Duplicate IDs
        if sid in step_ids:
            issues.append(ValidationIssue(step_id=sid, level="error", message=f"Duplicate step id '{sid}'."))
        step_ids.add(sid)

        # Capability exists
        if step.type == "capability" and step.capability:
            if step.capability not in known_caps:
                issues.append(ValidationIssue(
                    step_id=sid, level="error",
                    message=f"Unknown capability '{step.capability}'. Available: {sorted(known_caps)}.",
                ))

        # Input references valid step or ref_layer. A step can consume
        # another step's output OR a ref_layer as its primary input (the
        # DAG executor exposes ref layers as ``_input_<alias>`` dataset
        # nodes).
        if step.input is not None:
            refs = step.input if isinstance(step.input, list) else [step.input]
            all_ids = {(s.id or f"step_{j}") for j, s in enumerate(steps)}
            ref_layer_names = set((ref_layers or {}).keys())
            for ref in refs:
                # "input" is the reserved alias for the primary layer, seeded
                # by every execute path (see PipelineExecutor.execute and
                # /execute-steps step_outputs_cache initialisation).
                if ref == "input" or ref in step_ids or ref in all_ids or ref in ref_layer_names:
                    continue
                issues.append(ValidationIssue(
                    step_id=sid, level="error",
                    message=f"Input '{ref}' references unknown step or ref_layer.",
                ))

        # Ref layer references
        for key, val in (step.params or {}).items():
            if key == "ref_layer" and ref_layers and val not in ref_layers:
                issues.append(ValidationIssue(
                    step_id=sid, level="warning",
                    message=f"ref_layer '{val}' not found in ref_layers mapping.",
                ))

    return issues


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/execute", response_model=PipelineExecuteResponse)
def execute_pipeline(
    payload: PipelineExecuteRequest,
    dataset_repo: Repository = Depends(get_dataset_repo),
) -> PipelineExecuteResponse:
    """Execute a PipelineSpec v2 against a dataset.

    Provide either ``dataset_id`` (UUID of a registered dataset) or
    ``input_path`` (direct file path). The pipeline steps are executed
    via :class:`PipelineExecutor`.
    """
    _gate_dag_executor(payload.steps)

    import geopandas as gpd
    from persistence.io import read_vector

    # Resolve input GeoDataFrame
    if payload.dataset_id:
        dataset = dataset_repo.get(payload.dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail=f"Dataset '{payload.dataset_id}' not found.")
        try:
            gdf = read_vector(dataset.source_path, layer=payload.layer)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to load dataset: {exc}")
    elif payload.input_path:
        try:
            gdf = read_vector(payload.input_path, layer=payload.layer)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to load file: {exc}")
    else:
        raise HTTPException(status_code=422, detail="Provide either dataset_id or input_path.")

    # Validate before executing
    issues = _validate_pipeline(payload.steps, payload.ref_layers)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise HTTPException(status_code=422, detail=[i.model_dump() for i in errors])

    # Build PipelineSpec
    spec = _to_pipeline_spec(
        payload.steps, payload.name, payload.description,
        payload.triggers, payload.ref_layers,
    )

    # Load ref_layers as GeoDataFrames
    inputs: dict[str, gpd.GeoDataFrame] = {"input": gdf}
    for alias, source_path in spec.ref_layers.items():
        try:
            inputs[alias] = read_vector(source_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to load ref_layer '{alias}': {exc}")

    # Execute
    from orchestration.pipeline_executor import PipelineExecutor

    try:
        executor = PipelineExecutor()
        results = executor.execute(spec, inputs)
    except Exception as exc:
        log.error("pipeline_execute_error", error=str(exc), pipeline=spec.name)
        raise HTTPException(status_code=500, detail=f"Pipeline execution failed: {exc}")

    # Build response
    step_results = []
    for step_id, result_gdf in results.items():
        step_results.append(StepResultOut(
            step_id=step_id,
            features_count=len(result_gdf),
            columns=list(result_gdf.columns),
        ))

    last_gdf = list(results.values())[-1] if results else gdf
    return PipelineExecuteResponse(
        pipeline_name=spec.name,
        steps_executed=len(results),
        step_results=step_results,
        total_features_out=len(last_gdf),
        is_dag=spec.is_dag,
    )


class StepGeoJsonOut(BaseModel):
    """GeoJSON result for a single pipeline step."""

    step_id: str
    capability: str | None = None
    features_count: int
    features_in: int = 0
    features_delta: int = 0
    columns_added: list[str] = []
    columns_removed: list[str] = []
    bbox: list[float] | None = None
    duration_ms: int = 0
    artifact_path: str | None = None
    geojson: dict[str, Any]


class PipelineExecuteStepsResponse(BaseModel):
    """Response with GeoJSON for each intermediate step."""

    pipeline_name: str
    steps: list[StepGeoJsonOut]
    total_features_out: int
    total_duration_ms: int = 0
    artifacts_dir: str | None = None


@router.post("/execute-steps")
def execute_pipeline_steps(
    payload: PipelineExecuteRequest,
    dataset_repo: Repository = Depends(get_dataset_repo),
    simplify: float = 0.0,
    limit: int = 100000,
    persist: bool = False,
) -> PipelineExecuteStepsResponse:
    """Execute a pipeline and return GeoJSON for each intermediate step.

    Unlike ``/execute``, this endpoint returns the full geometry of each
    step result, allowing clients to visualise intermediate pipeline states
    without issuing N separate jobs.
    """
    _gate_dag_executor(payload.steps)

    import time

    import geopandas as gpd
    from persistence.io import read_vector

    # Resolve input
    if payload.dataset_id:
        dataset = dataset_repo.get(payload.dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail=f"Dataset '{payload.dataset_id}' not found.")
        try:
            gdf = read_vector(dataset.source_path, layer=payload.layer)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to load dataset: {exc}")
    elif payload.input_path:
        try:
            gdf = read_vector(payload.input_path, layer=payload.layer)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to load file: {exc}")
    else:
        raise HTTPException(status_code=422, detail="Provide either dataset_id or input_path.")

    # Validate
    issues = _validate_pipeline(payload.steps, payload.ref_layers)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise HTTPException(status_code=422, detail=[i.model_dump() for i in errors])

    # Build PipelineSpec
    spec = _to_pipeline_spec(
        payload.steps, payload.name, payload.description,
        payload.triggers, payload.ref_layers,
    )

    # Load ref_layers — resolve from same dataset/input file when the
    # source looks like a layer name instead of a path. This supports
    # multi-layer GPKG inputs both via dataset_id and via input_path.
    inputs: dict[str, gpd.GeoDataFrame] = {"input": gdf}
    dataset_source: str | None = None
    if payload.dataset_id:
        ds = dataset_repo.get(payload.dataset_id)
        if ds:
            dataset_source = ds.source_path
    if dataset_source is None and payload.input_path:
        dataset_source = payload.input_path

    for alias, source_path in spec.ref_layers.items():
        try:
            # If source_path looks like a layer name (no path separator, no
            # file extension) and we have a container file, resolve from
            # the same multi-layer file using the alias as the layer.
            looks_like_layer_name = (
                "/" not in source_path
                and "\\" not in source_path
                and "." not in source_path
            )
            if dataset_source and looks_like_layer_name:
                inputs[alias] = read_vector(dataset_source, layer=alias)
            else:
                inputs[alias] = read_vector(source_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to load ref_layer '{alias}': {exc}")

    # Execute step-by-step with per-step timing & metadata
    import json
    from capabilities import get as get_capability
    from gispulse.adapters.http.layer_utils import sanitize_datetime_columns

    def _gdf_to_geojson(gdf_in: gpd.GeoDataFrame) -> dict:
        return json.loads(sanitize_datetime_columns(gdf_in).to_json())

    def _bbox(gdf_in: gpd.GeoDataFrame) -> list[float] | None:
        if gdf_in.empty:
            return None
        b = gdf_in.total_bounds  # [minx, miny, maxx, maxy]
        return [round(float(v), 6) for v in b]

    step_outputs: list[StepGeoJsonOut] = []
    full_gdfs: list[tuple[str, gpd.GeoDataFrame]] = []  # for artifact persistence
    current_gdf = gdf
    prev_cols: set[str] = set(gdf.columns)
    total_start = time.monotonic()

    # Per-step output cache so ``step.input`` references resolve to the
    # correct upstream GDF (mirrors PipelineExecutor DAG semantics).
    # Seeded with ``input`` → primary GDF and each ref_layer alias.
    step_outputs_cache: dict[str, gpd.GeoDataFrame] = {"input": gdf}
    for _alias, _gdf in inputs.items():
        step_outputs_cache[_alias] = _gdf

    try:
        for step in spec.enabled_steps:
            if step.type != "capability" or not step.capability:
                continue

            # Pick the step's input: explicit ``input`` → cached upstream
            # GDF (another step or a ref_layer); otherwise carry over the
            # running ``current_gdf`` (linear mode).
            if step.input is not None:
                ref = step.input if isinstance(step.input, str) else step.input[0]
                if ref in step_outputs_cache:
                    step_input_gdf = step_outputs_cache[ref]
                else:
                    step_input_gdf = current_gdf
            else:
                step_input_gdf = current_gdf

            features_in = len(step_input_gdf)
            step_start = time.monotonic()

            cap = get_capability(step.capability)
            params = dict(step.params)
            ref_layer_alias = params.pop("ref_layer", None)
            if ref_layer_alias and ref_layer_alias in inputs:
                params["ref_gdf"] = inputs[ref_layer_alias]
            elif ref_layer_alias and ref_layer_alias in step_outputs_cache:
                # ref_layer may also reference an earlier step's output
                params["ref_gdf"] = step_outputs_cache[ref_layer_alias]

            # Plural ref_layers → ref_gdfs: resolve each alias against the
            # ref_layer inputs first, then earlier step outputs. Mirrors
            # PipelineExecutor so capabilities like classify_by_ring and
            # merge_layers receive concrete GeoDataFrames (not just names).
            ref_layers_aliases = params.pop("ref_layers", None)
            if isinstance(ref_layers_aliases, list) and ref_layers_aliases:
                resolved_gdfs = []
                for a in ref_layers_aliases:
                    if a in inputs:
                        resolved_gdfs.append(inputs[a])
                    elif a in step_outputs_cache:
                        resolved_gdfs.append(step_outputs_cache[a])
                if resolved_gdfs:
                    params["ref_gdfs"] = resolved_gdfs

            # Auto-inject a metric CRS when the capability needs one and
            # the GDF is angular — mirrors PipelineExecutor behaviour so
            # rules omitting ``crs_meters`` still produce correct
            # distances/areas on EPSG:4326 data.
            from orchestration.pipeline_executor import _auto_inject_crs_meters
            _auto_inject_crs_meters(cap, step.id, params, step_input_gdf)

            current_gdf = cap.execute_safe(step_input_gdf, **params)
            step_outputs_cache[step.id] = current_gdf

            step_ms = int((time.monotonic() - step_start) * 1000)
            current_cols = set(current_gdf.columns)

            if persist:
                full_gdfs.append((step.id, current_gdf.copy()))

            # Prepare GeoJSON output (simplified/truncated for response).
            # When the step output exceeds ``limit``, fall back to a uniform
            # random sample (deterministic seed) instead of ``head(limit)``.
            # ``head`` was biasing previews on multi-source layers like S6
            # ``dvf_ventes``: rows are ordered commune-by-commune in the source
            # GPKG, so the first 3 000 hits were 100 % Versailles even when
            # the layer covered 8 communes — making the truncated preview
            # look like the pre-extension dataset.
            if len(current_gdf) > limit:
                truncated = current_gdf.sample(n=limit, random_state=42).sort_index()
            else:
                truncated = current_gdf
            if simplify > 0 and not truncated.empty:
                truncated = truncated.copy()
                truncated.geometry = truncated.geometry.simplify(simplify)

            step_outputs.append(StepGeoJsonOut(
                step_id=step.id,
                capability=step.capability,
                features_count=len(current_gdf),
                features_in=features_in,
                features_delta=len(current_gdf) - features_in,
                columns_added=sorted(current_cols - prev_cols - {"geometry"}),
                columns_removed=sorted(prev_cols - current_cols - {"geometry"}),
                bbox=_bbox(current_gdf),
                duration_ms=step_ms,
                geojson=_gdf_to_geojson(truncated),
            ))

            prev_cols = current_cols
            log.debug("pipeline_step_done", step_id=step.id,
                       features=len(current_gdf), duration_ms=step_ms)

    except Exception as exc:
        log.error("pipeline_execute_steps_error", error=str(exc), pipeline=spec.name)
        raise HTTPException(status_code=500, detail=f"Pipeline execution failed: {exc}")

    total_ms = int((time.monotonic() - total_start) * 1000)
    last_count = len(current_gdf)

    # Persist intermediate artifacts to GPKG if requested
    artifacts_dir = None
    if persist and full_gdfs:
        import uuid
        from pathlib import Path

        base = Path("~/.gispulse/data").expanduser()
        run_id = uuid.uuid4().hex[:12]
        art_dir = base / "artifacts" / run_id
        art_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = str(art_dir)

        for i, (step_id, step_gdf) in enumerate(full_gdfs):
            art_path = art_dir / f"{i:02d}_{step_id}.gpkg"
            step_gdf.to_file(str(art_path), driver="GPKG")
            step_outputs[i].artifact_path = str(art_path)

        log.info("pipeline_artifacts_saved", dir=artifacts_dir, steps=len(full_gdfs))

    return PipelineExecuteStepsResponse(
        pipeline_name=spec.name,
        steps=step_outputs,
        total_features_out=last_count,
        total_duration_ms=total_ms,
        artifacts_dir=artifacts_dir,
    )


@router.post("/validate", response_model=PipelineValidateResponse)
def validate_pipeline(
    payload: PipelineValidateRequest,
) -> PipelineValidateResponse:
    """Validate a PipelineSpec without executing.

    Checks that all referenced capabilities exist and that step
    input references are valid.
    """
    issues = _validate_pipeline(payload.steps, payload.ref_layers)
    return PipelineValidateResponse(
        valid=not any(i.level == "error" for i in issues),
        issues=issues,
    )


@router.get("/examples", response_model=list[PipelineExampleOut])
def list_examples() -> list[PipelineExampleOut]:
    """Return built-in example pipeline definitions."""
    from core.pipeline import pipeline_to_dict

    examples = [
        PipelineExampleOut(
            name="filter_and_buffer",
            description="Filter features by attribute, then buffer the result.",
            spec=pipeline_to_dict(_to_pipeline_spec(
                steps=[
                    StepSpecIn(id="filter", type="capability", capability="filter",
                               params={"expression": "area > 1000"}),
                    StepSpecIn(id="buffer", type="capability", capability="buffer",
                               params={"distance": 50}, input="filter"),
                ],
                name="filter_and_buffer",
                description="Filter features by attribute, then buffer the result.",
            )),
        ),
        PipelineExampleOut(
            name="spatial_join",
            description="Join attributes from a reference layer using spatial intersection.",
            spec=pipeline_to_dict(_to_pipeline_spec(
                steps=[
                    StepSpecIn(id="join", type="capability", capability="spatial_join",
                               params={"ref_layer": "zones", "predicate": "intersects",
                                        "columns": ["zone_name", "zone_code"]}),
                ],
                name="spatial_join",
                description="Join attributes from a reference layer.",
                ref_layers={"zones": "data/zones.gpkg"},
            )),
        ),
        PipelineExampleOut(
            name="dag_pipeline",
            description="DAG pipeline: two parallel filters merged by a spatial join.",
            spec=pipeline_to_dict(_to_pipeline_spec(
                steps=[
                    StepSpecIn(id="filter_big", type="capability", capability="filter",
                               params={"expression": "area > 5000"}, order=0),
                    StepSpecIn(id="filter_small", type="capability", capability="filter",
                               params={"expression": "area <= 5000"}, order=1),
                    StepSpecIn(id="buffer_big", type="capability", capability="buffer",
                               params={"distance": 100}, input="filter_big"),
                ],
                name="dag_pipeline",
                description="DAG pipeline with branching.",
            )),
        ),
    ]
    return examples
