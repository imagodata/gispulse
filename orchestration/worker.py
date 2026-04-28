"""
Async job worker for GISPulse.

Polls the JobQueue, executes jobs via JobRunner, and updates status.
Designed to run as a long-lived asyncio task alongside the FastAPI
event loop (Mode A/B) or as a standalone process (Mode C multi-worker).

Usage (within FastAPI lifespan)::

    worker = JobWorker(queue=queue, runner=runner, dataset_repo=dataset_repo)
    task = asyncio.create_task(worker.start())
    # ... on shutdown:
    worker.stop()
    await task

Usage (standalone Mode C)::

    asyncio.run(run_worker())
"""

from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

from core.logging import get_logger
from core.models import Job, JobStatus
from orchestration.job_queue import JobQueue
from orchestration.runner import JobRunner
from persistence.repository import Repository

log = get_logger(__name__)

# Default poll interval when queue is empty (seconds)
DEFAULT_POLL_INTERVAL = 1.0
# Default job execution timeout (seconds)
DEFAULT_JOB_TIMEOUT = 300


class JobWorker:
    """Async worker that consumes jobs from a JobQueue.

    The worker loop:
    1. Dequeue a job (blocking with timeout for Redis, polling for in-memory).
    2. Update status to RUNNING.
    3. Load the dataset (if ``dataset_id`` is set).
    4. Execute via ``JobRunner.run()`` in a thread pool (CPU-bound work).
    5. Persist the result and update status to COMPLETED or FAILED.
    6. Repeat.

    Thread safety:
        ``JobRunner.run()`` is synchronous and CPU-bound.  It is offloaded to
        a ``ThreadPoolExecutor`` so the asyncio event loop stays responsive.
    """

    def __init__(
        self,
        queue: JobQueue,
        runner: JobRunner,
        dataset_repo: Repository,
        job_repo: Repository,
        *,
        results_dir: Path | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        max_concurrent: int = 2,
    ) -> None:
        self._queue = queue
        self._runner = runner
        self._dataset_repo = dataset_repo
        self._job_repo = job_repo
        self._results_dir = results_dir or Path("results")
        self._poll_interval = poll_interval
        self._running = False
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self._active_tasks: set[asyncio.Task] = set()

    async def _maybe_recover_stuck_jobs(self) -> None:
        """Run stuck job recovery if the queue backend supports it."""
        if hasattr(self._queue, "recover_stuck_jobs"):
            try:
                recovered = await self._queue.recover_stuck_jobs()
                if recovered:
                    log.info("worker_recovered_stuck_jobs", count=len(recovered))
            except Exception as exc:
                log.error("worker_recovery_error", error=str(exc))

    async def start(self) -> None:
        """Run the worker loop until ``stop()`` is called."""
        self._running = True
        self._results_dir.mkdir(parents=True, exist_ok=True)
        log.info("worker_started", poll_interval=self._poll_interval)

        # Recover stuck jobs at startup
        await self._maybe_recover_stuck_jobs()

        # Schedule periodic recovery every 5 minutes
        recovery_interval = 300  # seconds
        last_recovery = asyncio.get_running_loop().time()

        while self._running:
            try:
                # Periodic stuck job recovery
                now_loop = asyncio.get_running_loop().time()
                if now_loop - last_recovery >= recovery_interval:
                    await self._maybe_recover_stuck_jobs()
                    last_recovery = now_loop

                job = await self._queue.dequeue(timeout=self._poll_interval)
                if job is None:
                    continue
                # Process job concurrently
                task = asyncio.create_task(self._process_job(job))
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("worker_dequeue_error", error=str(exc))
                await asyncio.sleep(self._poll_interval)

        # Drain active tasks on shutdown
        if self._active_tasks:
            log.info("worker_draining", active=len(self._active_tasks))
            await asyncio.gather(*self._active_tasks, return_exceptions=True)

        log.info("worker_stopped")

    def stop(self) -> None:
        """Signal the worker to stop after the current iteration."""
        self._running = False
        log.info("worker_stop_requested")

    async def _send_heartbeat(self, job_id: str) -> None:
        """Send heartbeat if the queue backend supports it."""
        if hasattr(self._queue, "heartbeat"):
            try:
                await self._queue.heartbeat(job_id)
            except Exception as exc:
                log.warning("heartbeat_failed", job_id=job_id, error=str(exc))

    async def _heartbeat_loop(self, job_id: str, interval: float = 30.0) -> None:
        """Send periodic heartbeats while a job is running."""
        while True:
            await asyncio.sleep(interval)
            await self._send_heartbeat(job_id)

    async def _process_job(self, job: Job) -> None:
        """Execute a single job: load data, run, persist result."""
        job_id = str(job.id)
        log.info("worker_processing", job_id=job_id, job_name=job.name)

        # Check if cancelled before we start
        queue_status = await self._queue.get_status(job_id)
        if queue_status and queue_status.get("status") == JobStatus.FAILED.value:
            log.info("worker_skip_cancelled", job_id=job_id)
            return

        await self._queue.update_status(job_id, JobStatus.RUNNING)
        await self._send_heartbeat(job_id)

        # Start heartbeat loop
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id))

        try:
            # Load dataset
            gdf = await self._load_dataset(job)

            # Build layer_resolver for cross-layer rules
            def _layer_resolver(name: str) -> gpd.GeoDataFrame:
                if job.dataset_id:
                    ds = self._dataset_repo.get(job.dataset_id)
                    if ds and ds.source_path:
                        from persistence.io import read_vector as _rv
                        return _rv(ds.source_path, layer=name)
                raise ValueError(f"Cannot resolve layer '{name}'")

            # Execute in thread pool (CPU-bound)
            loop = asyncio.get_running_loop()
            run_fn = functools.partial(self._runner.run, job, gdf, layer_resolver=_layer_resolver)
            updated_job, result_gdf = await loop.run_in_executor(
                self._executor, run_fn
            )

            # Check if job was cancelled while running (before persisting result)
            final_status = await self._queue.get_status(job_id)
            if final_status and final_status.get("status") == JobStatus.FAILED.value:
                log.info("worker_job_cancelled_while_running", job_id=job_id)
                return

            # Persist result (only if not cancelled)
            result_path = None
            if not result_gdf.empty:
                from persistence.io import write_vector

                result_file = self._results_dir / f"{job.id}_result.gpkg"
                await loop.run_in_executor(
                    self._executor,
                    functools.partial(write_vector, result_gdf, str(result_file)),
                )
                result_path = str(result_file)
                job.result_path = result_path

            await self._queue.update_status(
                job_id,
                JobStatus.COMPLETED,
                result=result_path,
            )

            # Also update in the job repository for GET /jobs/{id}
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            self._job_repo.save(job)

            # Record metering for all job sources (HTTP, scheduler, triggers)
            metering = getattr(self, "_metering", None)
            if metering is not None:
                api_key = job.parameters.get("api_key", job.parameters.get("triggered_by", "internal"))
                duration = (job.completed_at - job.started_at).total_seconds() if job.started_at and job.completed_at else 0
                try:
                    await metering.record_job(api_key, duration)
                except Exception as meter_exc:
                    log.warning("worker_metering_failed", job_id=job_id, error=str(meter_exc))

            log.info("worker_job_completed", job_id=job_id)

        except Exception as exc:
            error_msg = str(exc)
            try:
                await self._queue.update_status(
                    job_id,
                    JobStatus.FAILED,
                    error=error_msg,
                )
            except Exception as redis_exc:
                log.error(
                    "worker_update_status_failed_during_error",
                    job_id=job_id,
                    original_error=error_msg,
                    redis_error=str(redis_exc),
                )
            job.status = JobStatus.FAILED
            job.error_message = error_msg
            job.completed_at = datetime.now(timezone.utc)
            try:
                self._job_repo.save(job)
            except Exception as repo_exc:
                log.error("worker_repo_save_failed", job_id=job_id, error=str(repo_exc))
            log.error("worker_job_failed", job_id=job_id, error=error_msg)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _load_dataset(self, job: Job, timeout: float = 300) -> gpd.GeoDataFrame:
        """Load the dataset GeoDataFrame for a job.

        Args:
            timeout: Maximum seconds to wait for dataset load (default 300s).
        """
        if not job.dataset_id:
            return gpd.GeoDataFrame()

        dataset = self._dataset_repo.get(job.dataset_id)
        if dataset is None:
            raise ValueError(f"Dataset '{job.dataset_id}' not found")

        from persistence.io import read_vector

        loop = asyncio.get_running_loop()
        try:
            gdf = await asyncio.wait_for(
                loop.run_in_executor(
                    self._executor,
                    functools.partial(read_vector, dataset.source_path, layer=job.parameters.get("layer")),
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise ValueError(
                f"Dataset '{job.dataset_id}' load timed out after {timeout}s"
            )
        return gdf
