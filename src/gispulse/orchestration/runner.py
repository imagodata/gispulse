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
from gispulse.persistence.repository import Repository
from gispulse.rules.engine import RuleEngine

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

        while attempt <= max_retries:
            attempt += 1
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(timezone.utc)
            log.info("job_started", job_id=str(job.id), job_name=job.name, attempt=attempt)

            try:
                with _metrics.timer("job_duration_seconds"):
                    requested_ids = job.parameters.get("rule_ids", [])
                    rules = self._resolve_rules(requested_ids)

                    if requested_ids and not rules:
                        raise ValueError(
                            f"Job {job.id}: none of the {len(requested_ids)} "
                            f"requested rule(s) could be resolved"
                        )

                    result_gdf = self._execute_with_timeout(rules, gdf, timeout, layer_resolver=layer_resolver)
                job.status = JobStatus.COMPLETED
                job.completed_at = datetime.now(timezone.utc)
                _metrics.inc("jobs_total")
                if len(rules) < len(requested_ids):
                    log.warning(
                        "job_rules_missing",
                        job_id=str(job.id),
                        requested=len(requested_ids),
                        resolved=len(rules),
                    )
                log.info(
                    "job_completed",
                    job_id=str(job.id),
                    job_name=job.name,
                    rules_applied=len(rules),
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

    def _execute_with_timeout(
        self, rules: list[Rule], gdf: gpd.GeoDataFrame, timeout: int,
        layer_resolver: Any | None = None,
    ) -> gpd.GeoDataFrame:
        """Execute rule pipeline with a timeout (in seconds)."""
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                self.rule_engine.apply_all, rules, gdf,
                layer_resolver=layer_resolver,
            )
            return future.result(timeout=timeout)

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
