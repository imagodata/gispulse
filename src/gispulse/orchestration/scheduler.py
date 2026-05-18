"""
Cron-based pipeline scheduler for GISPulse Pro.

Executes pipelines at regular intervals defined by cron expressions.
Integrates with the existing JobQueue to submit scheduled jobs.

Usage (within FastAPI lifespan)::

    scheduler = PipelineScheduler(job_queue=queue, schedule_repo=repo)
    await scheduler.start()
    # ... on shutdown:
    await scheduler.stop()

Requires ``croniter>=1.3`` (optional dependency: ``pip install gispulse[scheduling]``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from gispulse.core.logging import get_logger
from gispulse.core.models import Job
from gispulse.orchestration.job_queue import JobQueue

log = get_logger(__name__)

# Tick interval: how often the scheduler checks for due pipelines (seconds).
_TICK_INTERVAL = 60


@dataclass
class ScheduledPipeline:
    """A pipeline scheduled to run on a cron expression.

    Attributes:
        id:              Unique identifier.
        name:            Human-readable schedule name.
        cron_expression: Standard cron expression (e.g. ``"0 */6 * * *"``).
        pipeline_config: Dict describing the pipeline (rules, input, output).
        enabled:         Whether this schedule is active.
        last_run:        Timestamp of the last execution (None if never run).
        next_run:        Computed next execution time.
        created_by:      Optional user/API key that created this schedule.
    """

    id: UUID = field(default_factory=uuid4)
    name: str = ""
    cron_expression: str = "0 * * * *"
    pipeline_config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None
    created_by: str | None = None


def _compute_next_run(cron_expression: str, base_time: datetime | None = None) -> datetime:
    """Compute the next run time from a cron expression.

    Raises:
        ValueError: If croniter is not installed or expression is invalid.
    """
    try:
        from croniter import croniter
    except ImportError:
        raise ImportError(
            "croniter is required for scheduled pipelines. "
            "Install with: pip install 'croniter>=1.3,<3.0'"
        ) from None

    if base_time is None:
        base_time = datetime.now(timezone.utc)

    cron = croniter(cron_expression, base_time)
    next_dt = cron.get_next(datetime)

    # Ensure timezone-aware
    if next_dt.tzinfo is None:
        next_dt = next_dt.replace(tzinfo=timezone.utc)

    return next_dt


def validate_cron_expression(cron_expression: str) -> bool:
    """Check whether a cron expression is syntactically valid.

    Returns:
        True if valid.

    Raises:
        ValueError: If the expression is invalid.
    """
    try:
        from croniter import croniter
    except ImportError:
        raise ImportError(
            "croniter is required for scheduled pipelines. "
            "Install with: pip install 'croniter>=1.3,<3.0'"
        ) from None

    if not croniter.is_valid(cron_expression):
        raise ValueError(f"Invalid cron expression: {cron_expression!r}")
    return True


class PipelineScheduler:
    """Cron-based pipeline scheduler.

    Maintains an in-memory set of active schedules, ticks every 60 seconds,
    and enqueues jobs into the JobQueue when a schedule is due.

    The scheduler is tier-gated: it requires GISPulse Pro.
    """

    def __init__(
        self,
        job_queue: JobQueue,
        schedule_repo: Any | None = None,  # ScheduleRepository
    ) -> None:
        self._schedules: dict[str, ScheduledPipeline] = {}
        self._job_queue = job_queue
        self._schedule_repo = schedule_repo
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the scheduler tick loop.

        Loads persisted schedules from the repository (if available)
        and begins polling every ``_TICK_INTERVAL`` seconds.
        """
        if self._running:
            log.warning("scheduler_already_running")
            return

        # Tier gate
        from gispulse.persistence.tier import check_tier
        check_tier("pro")

        # Load persisted schedules
        if self._schedule_repo is not None:
            persisted = self._schedule_repo.list_all()
            for sp in persisted:
                if sp.enabled:
                    # Recompute next_run from now
                    sp.next_run = _compute_next_run(sp.cron_expression)
                    self._schedules[str(sp.id)] = sp
            log.info("scheduler_loaded_schedules", count=len(self._schedules))

        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("scheduler_started", tick_interval=_TICK_INTERVAL)

    async def stop(self) -> None:
        """Gracefully stop the scheduler."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("scheduler_stopped")

    async def add(self, schedule: ScheduledPipeline) -> ScheduledPipeline:
        """Register a new scheduled pipeline.

        Validates the cron expression, computes next_run, persists to the
        repository, and adds to the in-memory set.

        Returns:
            The schedule with ``next_run`` populated.
        """
        validate_cron_expression(schedule.cron_expression)

        if schedule.next_run is None:
            schedule.next_run = _compute_next_run(schedule.cron_expression)

        self._schedules[str(schedule.id)] = schedule

        # Persist
        if self._schedule_repo is not None:
            self._schedule_repo.save(schedule)

        log.info(
            "schedule_added",
            schedule_id=str(schedule.id),
            name=schedule.name,
            cron=schedule.cron_expression,
            next_run=schedule.next_run.isoformat() if schedule.next_run else None,
        )
        return schedule

    async def remove(self, schedule_id: str) -> bool:
        """Remove a scheduled pipeline by ID.

        Returns:
            True if the schedule was found and removed.
        """
        removed = self._schedules.pop(schedule_id, None)
        if removed is None:
            return False

        if self._schedule_repo is not None:
            self._schedule_repo.delete(removed.id)

        log.info("schedule_removed", schedule_id=schedule_id)
        return True

    async def update(
        self,
        schedule_id: str,
        *,
        cron_expression: str | None = None,
        enabled: bool | None = None,
        pipeline_config: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> ScheduledPipeline | None:
        """Update an existing schedule.

        Returns:
            Updated schedule, or None if not found.
        """
        schedule = self._schedules.get(schedule_id)
        if schedule is None:
            return None

        if cron_expression is not None:
            validate_cron_expression(cron_expression)
            schedule.cron_expression = cron_expression
            schedule.next_run = _compute_next_run(cron_expression)

        if enabled is not None:
            schedule.enabled = enabled
            if enabled and schedule.next_run is None:
                schedule.next_run = _compute_next_run(schedule.cron_expression)

        if pipeline_config is not None:
            schedule.pipeline_config = pipeline_config

        if name is not None:
            schedule.name = name

        # Persist
        if self._schedule_repo is not None:
            self._schedule_repo.save(schedule)

        log.info("schedule_updated", schedule_id=schedule_id)
        return schedule

    def get(self, schedule_id: str) -> ScheduledPipeline | None:
        """Return a schedule by ID, or None."""
        return self._schedules.get(schedule_id)

    async def list_schedules(self) -> list[ScheduledPipeline]:
        """Return all registered schedules."""
        return list(self._schedules.values())

    async def run_now(self, schedule_id: str) -> str | None:
        """Immediately enqueue the pipeline for a schedule, bypassing cron.

        Returns:
            The job ID, or None if the schedule was not found.
        """
        schedule = self._schedules.get(schedule_id)
        if schedule is None:
            return None

        job_id = await self._enqueue_pipeline(schedule)
        return job_id

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Main tick loop: checks every _TICK_INTERVAL seconds."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("scheduler_tick_error", error=str(exc))

            try:
                await asyncio.sleep(_TICK_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        """Check all schedules and enqueue any that are due."""
        now = datetime.now(timezone.utc)

        for schedule_id, schedule in list(self._schedules.items()):
            if not schedule.enabled:
                continue
            if schedule.next_run is None:
                continue
            if now >= schedule.next_run:
                try:
                    await self._enqueue_pipeline(schedule)
                    schedule.last_run = now
                    schedule.next_run = _compute_next_run(
                        schedule.cron_expression, base_time=now
                    )
                    # Persist updated timestamps
                    if self._schedule_repo is not None:
                        self._schedule_repo.save(schedule)

                    log.info(
                        "schedule_fired",
                        schedule_id=schedule_id,
                        name=schedule.name,
                        next_run=schedule.next_run.isoformat(),
                    )
                except Exception as exc:
                    log.error(
                        "schedule_fire_error",
                        schedule_id=schedule_id,
                        error=str(exc),
                    )

    async def _enqueue_pipeline(self, schedule: ScheduledPipeline) -> str:
        """Create a Job from the schedule's pipeline_config and enqueue it."""
        job = Job(
            name=f"scheduled:{schedule.name}",
            parameters={
                "schedule_id": str(schedule.id),
                "pipeline_config": schedule.pipeline_config,
                "triggered_by": "scheduler",
            },
        )
        job_id = await self._job_queue.enqueue(job)
        log.info(
            "schedule_job_enqueued",
            schedule_id=str(schedule.id),
            job_id=job_id,
        )
        return job_id
