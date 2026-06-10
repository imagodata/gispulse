"""
Job runner for GISPulse orchestration layer.

Executes Job objects by resolving their rule_ids from the repository,
delegating processing to the RuleEngine, and updating job status accordingly.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import geopandas as gpd

from gispulse.core.observability import MetricsCollector
from gispulse.core.logging import get_logger
from gispulse.core.models import Job, JobStatus, Rule
from gispulse.orchestration.event_sink import RunEventSink
from gispulse.persistence.repository import Repository
from gispulse.rules.engine import RuleEngine
# run_manifest is imported locally inside _execute_manifest to avoid a
# circular import: manifest_runner → orchestration.event_sink →
# orchestration.__init__ → runner → manifest_runner.

# Key injected by PipelineScheduler._enqueue_pipeline into job.parameters.
_PIPELINE_CONFIG_KEY = "pipeline_config"
# Key injected by POST /manifests/run into job.parameters.
_MANIFEST_KEY = "manifest"

log = get_logger(__name__)
_metrics = MetricsCollector.get()

# Default timeout for job execution (seconds). Override via Job.parameters["timeout"].
DEFAULT_JOB_TIMEOUT = 300
# Max retries for transient failures. Override via Job.parameters["max_retries"].
DEFAULT_MAX_RETRIES = 0


def _compute_effective_timeout(job_timeout: int, spec: Any) -> int:
    """Compute the effective execution timeout for a pipeline spec.

    Non-capability steps (external, dbt_build, …) declare their own
    ``timeout_seconds`` in ``params``.  A job-level timeout of 300 s would
    silently kill a dbt step that declares ``timeout_seconds=14400``.

    Formula::

        effective = max(job_timeout, sum(step.params["timeout_seconds"]
                                         for non-capability steps) + 60)

    The 60-second margin covers orchestration overhead (subprocess spawn,
    heartbeat thread, final I/O).  If no non-capability step declares a
    timeout, the job-level timeout is returned unchanged (backward-compatible).

    Args:
        job_timeout: Timeout from job.parameters["timeout"] or DEFAULT_JOB_TIMEOUT.
        spec:        Parsed PipelineSpec (must expose ``.steps`` iterable with
                     ``.kind`` and ``.params`` attributes on each step).

    Returns:
        Effective timeout in seconds (always >= job_timeout).
    """
    step_sum = sum(
        int(s.params.get("timeout_seconds", 0))
        for s in spec.steps
        if s.kind != "capability"
    )
    if step_sum <= 0:
        return job_timeout
    return max(job_timeout, step_sum + 60)


def _apply_steps_filter(
    spec: "Any",
    steps_filter: list[str],
    *,
    job_id: str = "",
) -> "Any":
    """Validate and apply a steps_filter to a PipelineSpec (partial run).

    Spec contract:
    - All step IDs in steps_filter must exist in spec.steps → ValueError (422).
    - A capability step in the filter whose ``input`` references a step OUTSIDE
      the filter → ValueError (no upstream GeoDataFrame).
    - A non-capability step whose ``input`` references a step outside the
      filter → ACCEPTED (no GeoDataFrame dependency between external steps).

    Returns a new PipelineSpec with only the selected steps enabled.

    Args:
        spec:         Parsed PipelineSpec to filter.
        steps_filter: List of step IDs to include.
        job_id:       Used in error messages.

    Raises:
        ValueError: Invalid filter (unknown ids, orphan capability).
    """
    from gispulse.core.pipeline import PipelineSpec

    all_ids = {s.id for s in spec.steps}

    # 1. Unknown ID check
    unknown = [sid for sid in steps_filter if sid not in all_ids]
    if unknown:
        valid_ids = sorted(all_ids)
        raise ValueError(
            f"Job {job_id}: steps_filter contains unknown step IDs: "
            f"{unknown!r}. Valid IDs: {valid_ids!r}."
        )

    filter_set = set(steps_filter)

    # 2. Capability orphan check — capability step whose input is outside filter
    for step in spec.steps:
        if step.id not in filter_set:
            continue
        if step.kind != "capability":
            continue
        if step.input is None:
            continue
        refs = step.input if isinstance(step.input, list) else [step.input]
        orphan_refs = [r for r in refs if r in all_ids and r not in filter_set]
        if orphan_refs:
            raise ValueError(
                f"Job {job_id}: steps_filter — capability step '{step.id}' "
                f"requires upstream step(s) {orphan_refs!r} which are excluded "
                "from the filter. A capability step cannot run without its "
                "GeoDataFrame input. Either include the upstream step(s) or "
                "remove this step from the filter."
            )

    # 3. Build filtered spec: keep only steps in filter_set
    filtered_steps = [s for s in spec.steps if s.id in filter_set]
    return PipelineSpec(
        version=spec.version,
        name=spec.name,
        description=spec.description,
        steps=filtered_steps,
        triggers=spec.triggers,
        ref_layers=spec.ref_layers,
    )


def _replay_resume(
    spec: "Any",
    resume_from_run_id: str,
    run_id: str,
    event_sink: "Any | None",
    run_repo: "Any",
    job_id: str = "",
) -> "tuple[Any, dict[str, str]]":
    """Replay completed steps from a source run and filter the spec.

    Skip/replay semantics are split by step kind:

    **Non-capability steps** (``step.kind != "capability"``, e.g. ``external``,
    ``dbt_build``, …) whose side effects persist outside the current process
    (dbt tables, tiles, files on disk) ARE SKIPPED when they were COMPLETED
    in the source run:
    1. Emit ``run.step.started`` + ``run.step.completed`` on the event_sink
       (so the cockpit sees a complete graph).
    2. Add ``skipped_resume=True`` to the step artifacts in the current run
       (via event_sink which drives RecordingSink).
    3. If the source step has a ``skip_marker`` in its artifacts, record it
       in ``resume_markers`` so the executor can pass it to the subprocess
       via ``GISPULSE_RESUME_MARKER``.
    4. Remove the COMPLETED non-capability step from the spec.

    **Capability steps** (``step.kind == "capability"``) transform the GDF
    in-memory. Their output does NOT survive the process boundary. A capability
    step COMPLETED in the source run MUST be re-executed to rebuild the in-memory
    GeoDataFrame chain so that subsequent steps receive the correct input.
    Replaying them as "skipped" would silently produce wrong data (downstream
    steps would run on the raw input GDF, bypassing the upstream transforms).
    Capability steps are therefore kept in the returned spec regardless of their
    COMPLETED status in the source run.

    Contract: non-capability steps are assumed idempotent at the side-effect
    level (app contract). Capability steps are always cheap to re-run (in-memory).

    Args:
        spec:                 Parsed PipelineSpec to filter.
        resume_from_run_id:   UUID of the source run to resume from.
        run_id:               UUID of the new (current) run.
        event_sink:           Where to emit replay events. ``None`` = no events.
        run_repo:             Repository for loading the source run.
        job_id:               Used in log messages.

    Returns:
        (filtered_spec, resume_markers) where resume_markers maps
        step_id → skip_marker for the first non-skipped step following each
        skipped non-capability step block.
    """
    from datetime import datetime, timezone
    from uuid import UUID

    from gispulse.core.models import JobStatus as _JS
    from gispulse.core.pipeline import PipelineSpec

    # Load the source run.
    # Narrow except: only catch ValueError from UUID parsing (malformed string).
    # Repository errors must propagate — they indicate infrastructure problems.
    try:
        source_uuid = UUID(resume_from_run_id)
    except ValueError as exc:
        raise ValueError(
            f"Job {job_id}: resume_from_run_id={resume_from_run_id!r} is not a "
            f"valid UUID — {exc}. "
            "code=RESUME_SOURCE_RUN_NOT_FOUND"
        ) from exc

    source_run = run_repo.get(source_uuid)

    if source_run is None:
        raise ValueError(
            f"Job {job_id}: source run '{resume_from_run_id}' not found in the "
            "run repository. It may have been deleted or the run_id is wrong. "
            "code=RESUME_SOURCE_RUN_NOT_FOUND"
        )

    # Build a map of step_id → source PipelineRunStep
    completed_step_ids: set[str] = {
        s.step_id
        for s in source_run.steps
        if s.status == _JS.COMPLETED
    }
    source_artifacts: dict[str, dict] = {
        s.step_id: s.artifacts
        for s in source_run.steps
    }

    steps_in_order = spec.steps  # preserved insertion order

    # Only non-capability steps that are COMPLETED in the source run are skipped.
    # Capability steps that were COMPLETED must be re-executed to rebuild the
    # in-memory GeoDataFrame chain — skipping them would silently corrupt data.
    skipped_ids: set[str] = {
        s.id
        for s in steps_in_order
        if s.id in completed_step_ids and s.kind != "capability"
    }

    # Collect resume_markers: one per non-skipped step that has a skip_marker
    # in the source run's artifacts for THAT SAME step_id — regardless of the
    # step's status (COMPLETED or FAILED).
    #
    # Rationale: skip_marker is a per-step opaque checkpoint token. The step
    # itself writes GISPULSE_SKIP_MARKER=<token> on stdout during its run (either
    # before completing cleanly or before crashing). At resume time, the SAME step
    # re-executes and expects to receive GISPULSE_RESUME_MARKER=<token> so it can
    # fast-forward to the checkpoint rather than starting from scratch.
    #
    # The old "inherit the previous skipped step's marker" logic was wrong:
    # the marker belongs to the step that produced it, not its successor.
    resume_markers: dict[str, str] = {}
    for step in steps_in_order:
        if step.id in skipped_ids:
            # Skipped steps don't re-execute → no marker injection needed.
            continue
        art = source_artifacts.get(step.id, {})
        if art and art.get("skip_marker"):
            resume_markers[step.id] = art["skip_marker"]

    # Replay skipped (non-capability COMPLETED) steps onto the sink
    # so the cockpit sees a complete graph for the new run.
    if event_sink is not None:
        now_iso = datetime.now(timezone.utc).isoformat()
        for step in steps_in_order:
            if step.id not in skipped_ids:
                continue
            art = dict(source_artifacts.get(step.id, {}))
            art["skipped_resume"] = True
            event_sink.emit("run.step.started", {
                "run_id": run_id,
                "step_id": step.id,
                "started_at": now_iso,
            })
            event_sink.emit("run.step.completed", {
                "run_id": run_id,
                "step_id": step.id,
                "status": "completed",
                "ended_at": now_iso,
                "artifacts": art,
                "metrics": {},
            })

    # Return a filtered spec without the skipped (non-capability COMPLETED) steps.
    # Capability COMPLETED steps stay — they will be re-executed normally.
    remaining_steps = [s for s in steps_in_order if s.id not in skipped_ids]
    filtered_spec = PipelineSpec(
        version=spec.version,
        name=spec.name,
        description=spec.description,
        steps=remaining_steps,
        triggers=spec.triggers,
        ref_layers=spec.ref_layers,
    )
    log.info(
        "pipeline_resume_applied",
        job_id=job_id,
        resume_from_run_id=resume_from_run_id,
        skipped_count=len(skipped_ids),
        remaining_count=len(remaining_steps),
    )
    return filtered_spec, resume_markers


class JobRunner:
    """
    Exécuteur de Jobs GISPulse.

    Usage::

        runner = JobRunner(repository=repo, rule_engine=engine)
        updated_job, result_gdf = runner.run(job, gdf)
    """

    def __init__(
        self,
        repository: Repository,
        rule_engine: RuleEngine,
    ) -> None:
        """
        Args:
            repository:  In-memory repository holding Rule objects (and others).
            rule_engine: RuleEngine instance used to apply the rules.
        """
        self.repository = repository
        self.rule_engine = rule_engine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        job: Job,
        gdf: gpd.GeoDataFrame,
        layer_resolver: Any | None = None,
        *,
        event_sink: RunEventSink | None = None,
        run_id: str | None = None,
        heartbeat: Any | None = None,
        cancel_check: Any | None = None,
        scope: str = "",
        run_repo: Any | None = None,
    ) -> tuple[Job, gpd.GeoDataFrame]:
        """Execute a Job against a GeoDataFrame.

        The job's ``parameters`` dict may contain a ``rule_ids`` key with a
        list of UUID strings (or UUID objects) identifying Rule objects to
        apply in order.

        The job's status is updated in-place:
        - Set to RUNNING at start.
        - Set to COMPLETED on success (with ``completed_at``).
        - Set to FAILED on error (with ``completed_at``), then re-raises.

        Args:
            job: Job domain object to execute.
            gdf: Input GeoDataFrame.

        Returns:
            Tuple ``(updated_job, result_gdf)``.

        Raises:
            Exception: Any exception raised by the rule pipeline is propagated
                       after marking the job as FAILED.
        """
        timeout = job.parameters.get("timeout", DEFAULT_JOB_TIMEOUT)
        max_retries = job.parameters.get("max_retries", job.max_retries)
        attempt = 0
        last_exc: Exception | None = None

        # Dispatch priority: manifest > pipeline_config > rule_ids.
        # If multiple keys are present, the highest-priority one wins and a
        # warning is emitted so the discrepancy is visible in logs.
        use_manifest = _MANIFEST_KEY in job.parameters
        use_pipeline_config = _PIPELINE_CONFIG_KEY in job.parameters
        if use_manifest and use_pipeline_config:
            log.warning(
                "job_manifest_takes_priority",
                job_id=str(job.id),
                detail="Both manifest and pipeline_config are present; "
                       "manifest takes priority.",
            )
        if use_manifest and job.parameters.get("rule_ids"):
            log.warning(
                "job_manifest_takes_priority_over_rule_ids",
                job_id=str(job.id),
                detail="Both manifest and rule_ids are present; "
                       "manifest takes priority.",
            )
        if use_pipeline_config and not use_manifest and job.parameters.get("rule_ids"):
            log.warning(
                "job_pipeline_config_takes_priority",
                job_id=str(job.id),
                detail="Both pipeline_config and rule_ids are present; "
                       "pipeline_config takes priority and rule_ids is ignored.",
            )

        while attempt <= max_retries:
            attempt += 1
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(timezone.utc)
            log.info("job_started", job_id=str(job.id), job_name=job.name, attempt=attempt)

            try:
                with _metrics.timer("job_duration_seconds"):
                    if use_manifest:
                        result_gdf = self._execute_manifest(
                            job, gdf, timeout,
                            event_sink=event_sink,
                            run_id=run_id,
                            run_repo=run_repo,
                        )
                        steps_count = len(
                            job.parameters[_MANIFEST_KEY].get("models", {})
                        )
                    elif use_pipeline_config:
                        result_gdf = self._execute_pipeline_config(
                            job, gdf, timeout,
                            event_sink=event_sink,
                            run_id=run_id,
                            heartbeat=heartbeat,
                            cancel_check=cancel_check,
                            scope=scope,
                            run_repo=run_repo,
                        )
                        steps_count = len(
                            job.parameters[_PIPELINE_CONFIG_KEY].get("steps", [])
                        )
                    else:
                        requested_ids = job.parameters.get("rule_ids", [])
                        rules = self._resolve_rules(requested_ids)

                        if requested_ids and not rules:
                            raise ValueError(
                                f"Job {job.id}: none of the {len(requested_ids)} "
                                f"requested rule(s) could be resolved"
                            )

                        result_gdf = self._execute_with_timeout(rules, gdf, timeout, layer_resolver=layer_resolver)
                        steps_count = len(rules)
                        if len(rules) < len(requested_ids):
                            log.warning(
                                "job_rules_missing",
                                job_id=str(job.id),
                                requested=len(requested_ids),
                                resolved=len(rules),
                            )

                job.status = JobStatus.COMPLETED
                job.completed_at = datetime.now(timezone.utc)
                _metrics.inc("jobs_total")
                log.info(
                    "job_completed",
                    job_id=str(job.id),
                    job_name=job.name,
                    rules_applied=steps_count,
                    attempt=attempt,
                )
                return job, result_gdf

            except FuturesTimeoutError:
                last_exc = TimeoutError(f"Job timed out after {timeout}s")
                log.warning("job_timeout", job_id=str(job.id), timeout=timeout, attempt=attempt)
            except Exception as exc:
                last_exc = exc
                log.warning("job_attempt_failed", job_id=str(job.id), attempt=attempt, error=str(exc))

            if attempt <= max_retries:
                log.info("job_retrying", job_id=str(job.id), next_attempt=attempt + 1)

        # All retries exhausted
        job.status = JobStatus.FAILED
        job.completed_at = datetime.now(timezone.utc)
        job.error_message = str(last_exc)
        _metrics.inc("jobs_total")
        _metrics.inc("jobs_failed")
        log.error(
            "job_failed",
            job_id=str(job.id),
            job_name=job.name,
            error=str(last_exc),
            attempts=attempt,
        )
        raise last_exc  # type: ignore[misc]

    def _execute_pipeline_config(
        self,
        job: Job,
        gdf: gpd.GeoDataFrame,
        timeout: int,
        *,
        event_sink: RunEventSink | None = None,
        run_id: str | None = None,
        heartbeat: Any | None = None,
        cancel_check: Any | None = None,
        scope: str = "",
        run_repo: Any | None = None,
    ) -> gpd.GeoDataFrame:
        """Execute a job whose parameters contain a ``pipeline_config`` dict.

        Validates the dict against SCHEMA_V2 (the canonical path used by
        ``POST /pipelines/validate`` and ``load_pipeline``), parses it into a
        :class:`~gispulse.core.pipeline.PipelineSpec`, then delegates to
        :class:`~gispulse.orchestration.pipeline_executor.PipelineExecutor`.
        The last step's output GeoDataFrame is returned.

        Validation rules enforced here (in addition to the JSON Schema):
        - Must be a dict (not a list/v1, not a string).
        - ``version`` must be 2; v1 lists and v3 manifests are rejected with
          a message naming the received version.
        - At least one enabled step must be present; a spec that would run
          zero steps is rejected (silent no-op is the bug we are fixing).
        - Input GeoDataFrame must be non-empty when the spec has runnable steps
          and no dataset_id was set on the job (guards against the scheduler
          firing on an empty GDF because dataset_id was not wired).

        Raises:
            ValueError: Malformed config, wrong version, zero runnable steps,
                        or empty input — caught by the caller's retry/FAILED path.

        Note on timeout behaviour:
            ``future.result(timeout)`` raises ``concurrent.futures.TimeoutError``
            when the step computation exceeds ``timeout`` seconds. We then call
            ``executor.shutdown(wait=False, cancel_futures=True)`` to avoid
            blocking the calling thread indefinitely (the worker thread itself
            may still linger — same known limitation as ``_execute_with_timeout``
            on the rule_ids path; a proper fix requires a process-level interrupt
            which is out of scope for this PR).
        """
        from gispulse.core.pipeline import _parse_v2, validate_step_kind_inputs
        from gispulse.core.pipeline_schema import validate_pipeline_json
        from gispulse.orchestration.pipeline_executor import PipelineExecutor

        raw_config = job.parameters[_PIPELINE_CONFIG_KEY]

        # --- Type guard ---------------------------------------------------
        if not isinstance(raw_config, dict):
            raise ValueError(
                f"Job {job.id}: pipeline_config must be a v2 dict, "
                f"got {type(raw_config).__name__}"
            )

        # --- Version guard (catch v3 manifests, v1 lists stored as dicts) --
        received_version = raw_config.get("version")
        if received_version != 2:
            raise ValueError(
                f"Job {job.id}: pipeline_config must be version 2, "
                f"got version={received_version!r}. "
                "Use a v2 pipeline spec ({{\"version\": 2, \"steps\": [...]}}). "
                "v3 manifests and v1 rule lists are not accepted here."
            )

        # --- Canonical JSON Schema validation (same as load_pipeline) ------
        errors = validate_pipeline_json(raw_config)
        if errors:
            summary = "; ".join(errors[:5])
            if len(errors) > 5:
                summary += f" … and {len(errors) - 5} more"
            raise ValueError(
                f"Job {job.id}: pipeline_config schema validation failed — {summary}"
            )

        # --- Parse ---------------------------------------------------------
        try:
            spec = _parse_v2(raw_config)
        except Exception as exc:
            raise ValueError(
                f"Job {job.id}: pipeline_config could not be parsed — {exc}"
            ) from exc

        # --- Partial run (steps_filter) ------------------------------------
        # Validates and applies the steps_filter to the spec before execution.
        # Must happen AFTER parse so we have the full step id set for validation.
        steps_filter: list[str] = job.parameters.get("steps_filter", [])
        if steps_filter:
            spec = _apply_steps_filter(spec, steps_filter, job_id=str(job.id))

        # --- Compile-time step-kind validation -----------------------------
        # Detects capability steps that depend on non-capability steps at
        # plan time — before any subprocess is spawned.
        kind_errors = validate_step_kind_inputs(spec)
        if kind_errors:
            summary = "; ".join(kind_errors[:5])
            if len(kind_errors) > 5:
                summary += f" … and {len(kind_errors) - 5} more"
            raise ValueError(
                f"Job {job.id}: pipeline_config step-kind validation failed — {summary}"
            )

        # --- Zero runnable steps guard ------------------------------------
        runnable = spec.enabled_steps
        if not runnable:
            raise ValueError(
                f"Job {job.id}: pipeline_config produced no runnable steps "
                "(all steps are disabled or the steps list is empty). "
                "At least one enabled capability step is required."
            )

        # --- Empty-input guard (P1b companion) ----------------------------
        if gdf.empty and job.dataset_id is None:
            raise ValueError(
                f"Job {job.id}: scheduled pipeline has no input dataset — "
                "set dataset_id on the ScheduledPipeline so the worker can "
                "load real data before executing the pipeline."
            )

        # --- Resume: replay completed steps from source run ---------------
        # When resume_from_run_id is present, load the source run and replay
        # its COMPLETED steps onto the event sink (so the cockpit sees a full
        # graph), then restrict the spec to only the non-COMPLETED steps.
        # Contract: resume assumes steps are idempotent (app contract).
        resume_from_run_id: str = job.parameters.get("resume_from_run_id", "")
        resume_markers: dict[str, str] = {}  # step_id → skip_marker from source run
        if resume_from_run_id and run_repo is not None and event_sink is not None:
            spec, resume_markers = _replay_resume(
                spec=spec,
                resume_from_run_id=resume_from_run_id,
                run_id=run_id or "",
                event_sink=event_sink,
                run_repo=run_repo,
                job_id=str(job.id),
            )
        elif resume_from_run_id and run_repo is not None:
            # No event_sink (e.g. tests without sink) — still filter the spec
            spec, resume_markers = _replay_resume(
                spec=spec,
                resume_from_run_id=resume_from_run_id,
                run_id=run_id or "",
                event_sink=None,
                run_repo=run_repo,
                job_id=str(job.id),
            )

        # --- Compute effective timeout -------------------------------------
        # Non-capability steps (external, dbt_build, …) declare their own
        # timeout_seconds in params.  Their declared timeouts RAISE the job
        # plafond automatically so the manifest is the single source of truth.
        # Formula: max(job_timeout, sum(non-capability step timeout_seconds) + 60s).
        effective_timeout = _compute_effective_timeout(timeout, spec)
        if effective_timeout != timeout:
            log.info(
                "job_timeout_elevated",
                job_id=str(job.id),
                original_timeout=timeout,
                effective_timeout=effective_timeout,
            )

        # --- Execute with timeout -----------------------------------------
        executor = PipelineExecutor(
            cancel_check=cancel_check,
            heartbeat=heartbeat,
            scope=scope,
            run_repo=run_repo,
            resume_markers=resume_markers,
        )
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(
                executor.execute, spec, {"input": gdf},
                None,  # params
                event_sink=event_sink,
                run_id=run_id,
            )
            results = future.result(timeout=effective_timeout)
        except FuturesTimeoutError:
            # Avoid blocking the calling thread in pool.shutdown(wait=True).
            # The worker thread may still linger (same limitation as
            # _execute_with_timeout on the rule_ids path).
            pool.shutdown(wait=False, cancel_futures=True)
            # Re-raise with effective_timeout so the caller's error message
            # reflects the actual limit that was applied (may differ from the
            # raw job timeout when non-capability steps elevated the plafond).
            raise FuturesTimeoutError(
                f"pipeline execution exceeded effective timeout {effective_timeout}s"
                + (
                    f" (elevated from job timeout {timeout}s by step declarations)"
                    if effective_timeout != timeout
                    else ""
                )
            )
        finally:
            # Normal completion: shutdown is quick (worker already done).
            # Timeout path already called shutdown above; calling it again
            # with wait=False is a no-op on an already-shutdown pool.
            pool.shutdown(wait=False)

        # Return the last step's GeoDataFrame output (linear pipeline convention).
        # When all steps are non-capability kinds (external, dbt_build, …), they
        # produce no GeoDataFrame — return the input GDF unchanged so the caller
        # has a well-typed result (the meaningful output is in step artifacts).
        if not results:
            return gdf
        last_step_gdf = list(results.values())[-1]
        return last_step_gdf

    def _execute_manifest(
        self,
        job: Job,
        gdf: gpd.GeoDataFrame,
        timeout: int,
        *,
        event_sink: RunEventSink | None = None,
        run_id: str | None = None,
        run_repo: Any | None = None,
    ) -> gpd.GeoDataFrame:
        """Execute a job whose parameters contain a ``manifest`` dict (v3 ManifestV3).

        Validates the manifest dict through validate_pipeline_json (JSON Schema
        SCHEMA_V3) then through validate_manifest (refs/cycles), parses it into
        a ManifestV3 in-memory object, and delegates to run_manifest.

        The source_loader routes each declared source to the job's loaded GDF.
        For the common case (single dataset → single source), this is correct.
        Multi-source manifests where sources point to different files require
        explicit dataset_id wiring per source — a follow-up concern.

        Raises:
            ValueError: Invalid manifest (schema errors, unresolved refs, cycles,
                        incremental mode). Caught by the caller's retry/FAILED path
                        and stored as job.error_message.
        """
        from gispulse.core.manifest_v3 import (
            ManifestV3,
            _parse_sources,
            _parse_staging,
            _parse_models,
            _parse_v3_triggers,
            validate_manifest,
        )
        from gispulse.core.pipeline_schema import validate_pipeline_json

        raw = job.parameters[_MANIFEST_KEY]

        # --- Type guard ---------------------------------------------------
        if not isinstance(raw, dict):
            raise ValueError(
                f"Job {job.id}: manifest must be a dict, "
                f"got {type(raw).__name__}"
            )

        # --- Version guard ------------------------------------------------
        received_version = raw.get("version")
        if received_version != 3:
            raise ValueError(
                f"Job {job.id}: manifest must be version 3, "
                f"got version={received_version!r}. "
                'Use a v3 manifest ({"version": 3, "sources": {...}, "models": {...}}).'
            )

        # --- JSON Schema validation (SCHEMA_V3) ---------------------------
        errors = validate_pipeline_json(raw)
        if errors:
            summary = "; ".join(errors[:5])
            if len(errors) > 5:
                summary += f" … and {len(errors) - 5} more"
            raise ValueError(
                f"Job {job.id}: manifest schema validation failed — {summary}"
            )

        # --- Parse into ManifestV3 ----------------------------------------
        try:
            manifest = ManifestV3(
                name=raw.get("name", job.name),
                description=raw.get("description", ""),
                sources=_parse_sources(raw.get("sources", {})),
                staging=_parse_staging(raw.get("staging")),
                models=_parse_models(raw.get("models", {})),
                triggers=_parse_v3_triggers(raw.get("triggers", [])),
                security=dict(raw.get("security") or {}),
                runtime=dict(raw.get("runtime") or {}),
            )
        except Exception as exc:
            raise ValueError(
                f"Job {job.id}: manifest could not be parsed — {exc}"
            ) from exc

        # --- Load-time graph check ----------------------------------------
        # validate_manifest raises ManifestValidationError (subclass of ValueError)
        # on unresolved refs / cycles — propagated directly to the FAILED path.
        validate_manifest(manifest)

        # --- Incremental guard — explicit FAILED before touching the GDF --
        # run_manifest would raise NotImplementedError deep in the stack;
        # we surface it as a clear ValueError with explicit "not implemented"
        # so job.error_message is actionable.
        for model_name, model in manifest.models.items():
            if model.materialize == "incremental":
                raise ValueError(
                    f"Job {job.id}: model '{model_name}' uses materialize=incremental "
                    "which is not implemented. Use materialize=view or materialize=table."
                )

        # --- Source loader: canonical URI-based loader with job-GDF fallback ---
        # Priority: if the source URI is a real path or a remote URI that the
        # persistence.loader.load() can resolve, use it directly.
        # Fallback (memory:// scheme or any load failure): return the job's GDF
        # loaded by the worker (useful for in-memory tests and pipeline_config
        # datasets that arrive via dataset_id).
        #
        # This means manifests with real file/S3/WFS sources work WITHOUT a
        # dataset_id — the source loader fetches each declared source URI.
        # The empty-input guard (pipeline_config path) does NOT apply here.
        def source_loader(src):
            from gispulse.persistence.loader import load as _load_source

            uri = src.uri if src.uri else ""
            # memory:// URIs and blank URIs are test-only in-memory sources;
            # fall back to the job GDF (loaded from dataset_id by the worker,
            # or empty when no dataset_id is set).
            if not uri or uri.startswith("memory://"):
                return gdf
            try:
                return _load_source(uri, layer=src.layer or None, geometry=None)
            except (FileNotFoundError, ValueError) as exc:
                raise ValueError(
                    f"Job {job.id}: cannot load source '{uri}' "
                    f"(layer={src.layer!r}) — {exc}"
                ) from exc

        # --- Partial run (steps_filter) ------------------------------------
        # Validated upstream by the router (POST /manifests/run) and stored
        # in job.parameters["steps_filter"]. Passed through to run_manifest
        # which applies it per-model sub-spec.
        steps_filter_manifest: list[str] = job.parameters.get("steps_filter", [])

        # --- Resume: replay completed steps from source run ---------------
        # When resume_from_run_id is present, we need to:
        # 1. Compile the manifest to a flat PipelineSpec (same IDs as the sub-specs).
        # 2. Call _replay_resume on the flat spec to emit replay events and
        #    collect resume_markers.
        # 3. The filtered flat spec tells us which step IDs to RE-RUN; the
        #    skipped step IDs tell us which models to skip in run_manifest.
        # 4. Pass resume_markers + effective steps_filter to run_manifest.
        resume_from_run_id: str = job.parameters.get("resume_from_run_id", "")
        manifest_resume_markers: dict[str, str] = {}
        if resume_from_run_id and run_repo is not None:
            from gispulse.core.manifest_v3 import compile_to_pipeline as _compile_manifest
            flat_spec = _compile_manifest(manifest)
            # Apply existing steps_filter on the flat spec first (partial resume)
            if steps_filter_manifest:
                flat_spec = _apply_steps_filter(
                    flat_spec, steps_filter_manifest, job_id=str(job.id)
                )
            filtered_flat_spec, manifest_resume_markers = _replay_resume(
                spec=flat_spec,
                resume_from_run_id=resume_from_run_id,
                run_id=run_id or "",
                event_sink=event_sink if event_sink is not None else None,
                run_repo=run_repo,
                job_id=str(job.id),
            )
            # The filtered flat spec now contains only the steps NOT yet COMPLETED.
            # We use its step IDs as the effective steps_filter for run_manifest,
            # overriding the original steps_filter (which may be empty = all steps).
            steps_filter_manifest = [s.id for s in filtered_flat_spec.steps]
            log.info(
                "manifest_resume_steps_to_run",
                job_id=str(job.id),
                resume_from_run_id=resume_from_run_id,
                steps_to_run=steps_filter_manifest,
                resume_markers_count=len(manifest_resume_markers),
            )
            # F1 guard: when the resume has already skipped ALL steps (the
            # source run was fully COMPLETED), steps_filter_manifest is empty.
            # Do NOT convert [] → None (= "no filter" = re-run everything).
            # Short-circuit here: replay events have already been emitted by
            # _replay_resume; there is nothing left to execute.
            if not steps_filter_manifest:
                log.info(
                    "manifest_resume_nothing_to_execute",
                    job_id=str(job.id),
                    resume_from_run_id=resume_from_run_id,
                )
                return gdf
        elif resume_from_run_id and run_repo is None:
            # No run_repo available — cannot replay, raise explicit error
            # (never silent resume-from-scratch for manifest jobs).
            raise ValueError(
                f"Job {job.id}: resume_from_run_id={resume_from_run_id!r} is set "
                "but no run_repo is available in this execution context. "
                "Resume requires a run_repo to load the source run's step history. "
                "code=MANIFEST_RESUME_NO_REPO"
            )

        # --- Execute with timeout -----------------------------------------
        from gispulse.runtime.manifest_runner import run_manifest  # local to avoid circular import

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(
                run_manifest,
                manifest,
                source_loader=source_loader,
                event_sink=event_sink,
                run_id=run_id,
                # Preserve [] as [] — empty list means "nothing to run for this
                # partial resume", not "run everything". None means no filter.
                # The F1 guard above already returns early when the list is empty
                # after a resume; this line is reached only for non-resume paths
                # or resume paths with a non-empty remaining list.
                steps_filter=steps_filter_manifest or None,
                resume_markers=manifest_resume_markers if manifest_resume_markers else None,
            )
            result = future.result(timeout=timeout)
        except FuturesTimeoutError:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            pool.shutdown(wait=False)

        # Return the last materialized model's GeoDataFrame as the job result.
        if result.materialized and result.execution_order:
            last_model = result.execution_order[-1]
            return result.materialized[last_model].result
        if result.materialized:
            return list(result.materialized.values())[-1].result
        return gdf

    def _execute_with_timeout(
        self, rules: list[Rule], gdf: gpd.GeoDataFrame, timeout: int,
        layer_resolver: Any | None = None,
    ) -> gpd.GeoDataFrame:
        """Execute rule pipeline with a timeout (in seconds).

        Note on timeout behaviour:
            When ``future.result(timeout)`` raises ``TimeoutError``, we call
            ``pool.shutdown(wait=False, cancel_futures=True)`` to avoid
            blocking the calling thread in ``ThreadPoolExecutor.__exit__``
            (which calls ``shutdown(wait=True)`` and would hang until the
            stuck worker thread finally returns). The worker thread itself
            may still linger until the GIL releases — this is a known
            limitation of CPython thread cancellation and is out of scope
            for this PR.
        """
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(
                self.rule_engine.apply_all, rules, gdf,
                layer_resolver=layer_resolver,
            )
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            pool.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_rules(self, rule_ids: list[Any]) -> list[Rule]:
        """Resolve a list of rule IDs to Rule objects from the repository.

        Missing IDs are silently skipped (non-blocking by design).

        Args:
            rule_ids: List of UUID or str identifiers for Rule objects.

        Returns:
            List of resolved Rule objects (preserving input order).
        """
        rules: list[Rule] = []
        for raw_id in rule_ids:
            uid = UUID(str(raw_id)) if not isinstance(raw_id, UUID) else raw_id
            rule = self.repository.get(uid)
            if rule is not None:
                rules.append(rule)  # type: ignore[arg-type]
        return rules
