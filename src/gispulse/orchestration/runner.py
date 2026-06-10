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

# Key injected by PipelineScheduler._enqueue_pipeline into job.parameters.
_PIPELINE_CONFIG_KEY = "pipeline_config"

log = get_logger(__name__)
_metrics = MetricsCollector.get()

# Default timeout for job execution (seconds). Override via Job.parameters["timeout"].
DEFAULT_JOB_TIMEOUT = 300
# Max retries for transient failures. Override via Job.parameters["max_retries"].
DEFAULT_MAX_RETRIES = 0


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

        # Dispatch: pipeline_config (scheduler path) takes priority over rule_ids.
        # If both are present, pipeline_config wins and a warning is emitted so the
        # discrepancy is visible in logs. This is the safer choice: the scheduler
        # never sets rule_ids, and a caller that sets both has an ambiguity that
        # must not be silently resolved in the wrong direction.
        use_pipeline_config = _PIPELINE_CONFIG_KEY in job.parameters
        if use_pipeline_config and job.parameters.get("rule_ids"):
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
                    if use_pipeline_config:
                        result_gdf = self._execute_pipeline_config(
                            job, gdf, timeout,
                            event_sink=event_sink,
                            run_id=run_id,
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
        from gispulse.core.pipeline import _parse_v2
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

        # --- Execute with timeout -----------------------------------------
        executor = PipelineExecutor()
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(
                executor.execute, spec, {"input": gdf},
                None,  # params
                event_sink=event_sink,
                run_id=run_id,
            )
            results = future.result(timeout=timeout)
        except FuturesTimeoutError:
            # Avoid blocking the calling thread in pool.shutdown(wait=True).
            # The worker thread may still linger (same limitation as
            # _execute_with_timeout on the rule_ids path).
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            # Normal completion: shutdown is quick (worker already done).
            # Timeout path already called shutdown above; calling it again
            # with wait=False is a no-op on an already-shutdown pool.
            pool.shutdown(wait=False)

        # Return the last step's output (linear pipeline convention).
        last_step_gdf = list(results.values())[-1]
        return last_step_gdf

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
