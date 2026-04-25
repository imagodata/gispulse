"""
Scenario runner for GISPulse orchestration layer.

Executes Scenario objects by resolving their job_ids from the repository,
delegating each job to the JobRunner, and chaining (or isolating) results
depending on the execution mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import geopandas as gpd

from core.logging import get_logger
from core.models import EdgeDef, Job, NodeDef, Scenario
from orchestration.runner import JobRunner
from persistence.repository import Repository

log = get_logger(__name__)


@dataclass
class ScenarioResult:
    """Result of a Scenario execution.

    Attributes:
        scenario:      The Scenario that was executed.
        job_results:   Ordered list of (Job, result_gdf | None) tuples.
                       result_gdf is None for jobs that raised an exception
                       in ``run_independent`` mode.
        status:        "COMPLETED" | "FAILED" | "PARTIAL"
        failed_job_id: UUID (as str) of the first job that failed, or None.
        error:         String representation of the exception, or None.
    """

    scenario: Scenario
    job_results: list[tuple[Job, gpd.GeoDataFrame | None]] = field(
        default_factory=list
    )
    status: Literal["COMPLETED", "FAILED", "PARTIAL"] = "COMPLETED"
    failed_job_id: str | None = None
    error: str | None = None


class ScenarioRunner:
    """
    Exécuteur de Scenarios GISPulse.

    Two execution modes are provided:

    * ``run``              — sequential pipeline: each job receives the output
                            of the previous one.  Stops on first failure.
    * ``run_independent``  — parallel-like: every job receives the original
                            input GeoDataFrame.  Continues on failure and
                            returns status PARTIAL.

    Usage::

        scenario_runner = ScenarioRunner(
            job_runner=job_runner,
            rule_repo=rule_repo,
            job_repo=job_repo,
        )
        result = scenario_runner.run(scenario, gdf)
    """

    def __init__(
        self,
        job_runner: JobRunner,
        rule_repo: Repository,
        job_repo: Repository,
        graph_executor: Any | None = None,
        *,
        checkpoint_dir: str | None = None,
    ) -> None:
        """
        Args:
            job_runner: JobRunner instance used to execute individual Jobs.
            rule_repo:  Repository holding Rule objects (passed through to
                        JobRunner; already wired, kept for future lookups).
            job_repo:   Repository holding Job objects to resolve from UUIDs
                        stored in ``Scenario.jobs``.
            graph_executor: Optional GraphExecutor for DAG-based scenarios.
            checkpoint_dir: Directory for intermediate GeoParquet checkpoints.
                            When set, intermediate results between pipeline
                            jobs are spilled to GeoParquet files for memory
                            efficiency and crash recovery.  If None,
                            checkpointing is disabled (all in-memory).
        """
        self.job_runner = job_runner
        self.rule_repo = rule_repo
        self.job_repo = job_repo
        self.graph_executor = graph_executor
        self._checkpoint_dir = checkpoint_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        scenario: Scenario,
        gdf: gpd.GeoDataFrame,
    ) -> ScenarioResult:
        """Execute a scenario as a sequential pipeline.

        If the scenario contains a populated ``graph`` dict with ``nodes``
        and ``edges``, execution is delegated to the :class:`GraphExecutor`.
        Otherwise, the classic job-chaining pipeline is used.

        Args:
            scenario: Scenario domain object whose ``jobs`` list holds UUIDs.
            gdf:      Initial input GeoDataFrame.

        Returns:
            ScenarioResult with status:
            - "COMPLETED" if all jobs succeeded (or the scenario has 0 jobs).
            - "FAILED"    if any job raised an exception (execution halted).
        """
        # Phase 3A: delegate to GraphExecutor if scenario has a graph
        if scenario.graph and scenario.graph.get("nodes") and self.graph_executor:
            return self._run_graph(scenario, gdf)

        jobs = self._resolve_jobs(scenario.jobs)
        job_results: list[tuple[Job, gpd.GeoDataFrame | None]] = []
        current_gdf = gdf

        for step, job in enumerate(jobs):
            try:
                updated_job, current_gdf = self.job_runner.run(job, current_gdf)
                job_results.append((updated_job, current_gdf))
                # Checkpoint intermediate results as GeoParquet (not final step)
                if step < len(jobs) - 1:
                    self._write_checkpoint(current_gdf, scenario.id, step)
            except Exception as exc:
                job_results.append((job, None))
                return ScenarioResult(
                    scenario=scenario,
                    job_results=job_results,
                    status="FAILED",
                    failed_job_id=str(job.id),
                    error=str(exc),
                )

        return ScenarioResult(
            scenario=scenario,
            job_results=job_results,
            status="COMPLETED",
        )

    def run_independent(
        self,
        scenario: Scenario,
        gdf: gpd.GeoDataFrame,
    ) -> ScenarioResult:
        """Execute each job independently against the original GeoDataFrame.

        No output chaining: every job always receives ``gdf`` as-is.
        Execution continues even when a job fails.

        Args:
            scenario: Scenario domain object whose ``jobs`` list holds UUIDs.
            gdf:      Input GeoDataFrame passed to every job.

        Returns:
            ScenarioResult with status:
            - "COMPLETED" if all jobs succeeded (or the scenario has 0 jobs).
            - "PARTIAL"   if at least one job failed but others ran.
              ``failed_job_id`` holds the first failing job's id,
              ``error`` holds its exception message.
        """
        jobs = self._resolve_jobs(scenario.jobs)
        job_results: list[tuple[Job, gpd.GeoDataFrame | None]] = []
        first_failed_id: str | None = None
        first_error: str | None = None

        for job in jobs:
            try:
                updated_job, result_gdf = self.job_runner.run(job, gdf)
                job_results.append((updated_job, result_gdf))
            except Exception as exc:
                job_results.append((job, None))
                if first_failed_id is None:
                    first_failed_id = str(job.id)
                    first_error = str(exc)

        has_failure = first_failed_id is not None
        status: Literal["COMPLETED", "FAILED", "PARTIAL"] = (
            "PARTIAL" if has_failure else "COMPLETED"
        )

        return ScenarioResult(
            scenario=scenario,
            job_results=job_results,
            status=status,
            failed_job_id=first_failed_id,
            error=first_error,
        )

    # ------------------------------------------------------------------
    # Graph execution (Phase 3A)
    # ------------------------------------------------------------------

    def _run_graph(
        self, scenario: Scenario, gdf: gpd.GeoDataFrame
    ) -> ScenarioResult:
        """Execute a scenario via the GraphExecutor."""
        try:
            raw_nodes = scenario.graph.get("nodes", [])
            raw_edges = scenario.graph.get("edges", [])
            params = scenario.graph.get("parameters", {})

            nodes = [
                n if isinstance(n, NodeDef) else NodeDef(**n)
                for n in raw_nodes
            ]
            edges = [
                e if isinstance(e, EdgeDef) else EdgeDef(**e)
                for e in raw_edges
            ]

            # Feed all dataset nodes with the input GDF
            inputs: dict[str, gpd.GeoDataFrame] = {}
            for n in nodes:
                if n.node_type == "dataset" or (hasattr(n.node_type, 'value') and n.node_type.value == "dataset"):
                    inputs[n.id] = gdf

            results = self.graph_executor.execute(nodes, edges, inputs, params)

            # Convert graph results to job_results format for reporting
            graph_job_results = [
                (Job(name=f"graph_node_{nid}"), gdf_result)
                for nid, gdf_result in results.items()
                if isinstance(gdf_result, gpd.GeoDataFrame)
            ]

            log.info(
                "scenario_graph_completed",
                scenario_id=str(scenario.id),
                nodes_executed=len(results),
            )
            return ScenarioResult(
                scenario=scenario,
                job_results=graph_job_results,
                status="COMPLETED",
            )
        except Exception as exc:
            log.error(
                "scenario_graph_failed",
                scenario_id=str(scenario.id),
                error=str(exc),
            )
            return ScenarioResult(
                scenario=scenario,
                status="FAILED",
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # GeoParquet checkpointing
    # ------------------------------------------------------------------

    def _checkpoint_path(self, scenario_id: UUID, step: int) -> Path | None:
        """Return the GeoParquet path for an intermediate checkpoint.

        Returns None if checkpointing is disabled.
        """
        if self._checkpoint_dir is None:
            return None
        base = Path(self._checkpoint_dir)
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{scenario_id}_step{step:03d}.parquet"

    def _write_checkpoint(
        self, gdf: gpd.GeoDataFrame, scenario_id: UUID, step: int
    ) -> None:
        """Spill an intermediate result to GeoParquet.

        Uses GeoParquet (not GPKG) for intermediate results because:
        - Columnar format => faster read/write (2-5x vs GPKG)
        - Native geometry encoding (no WKB serialization overhead)
        - Better compression (Snappy by default)
        - Supports bbox filtering on re-read
        """
        path = self._checkpoint_path(scenario_id, step)
        if path is None:
            return
        from persistence.io import write_geoparquet

        write_geoparquet(gdf, str(path))
        log.debug(
            "checkpoint_written",
            path=str(path),
            features=len(gdf),
            format="geoparquet",
        )

    def _read_checkpoint(self, scenario_id: UUID, step: int) -> gpd.GeoDataFrame | None:
        """Read an intermediate GeoParquet checkpoint, if it exists."""
        path = self._checkpoint_path(scenario_id, step)
        if path is None or not path.exists():
            return None
        from persistence.io import read_geoparquet

        gdf = read_geoparquet(str(path))
        log.debug(
            "checkpoint_restored",
            path=str(path),
            features=len(gdf),
            format="geoparquet",
        )
        return gdf

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_jobs(self, job_ids: list[UUID]) -> list[Job]:
        """Resolve a list of Job UUIDs to Job objects from the repository.

        Missing IDs are silently skipped (non-blocking, consistent with
        JobRunner._resolve_rules behaviour).

        Args:
            job_ids: List of UUID identifiers for Job objects.

        Returns:
            List of resolved Job objects (preserving input order).
        """
        jobs: list[Job] = []
        for uid in job_ids:
            job = self.job_repo.get(uid)
            if job is not None:
                jobs.append(job)  # type: ignore[arg-type]
        return jobs
