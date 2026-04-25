"""
Job queue abstraction and implementations for GISPulse.

Provides:
- ``JobQueue``          -- abstract interface for enqueue/dequeue/status operations.
- ``InMemoryJobQueue``  -- default for Mode A/B (single-process, asyncio.Queue-backed).
- ``RedisJobQueue``     -- production Mode C (multi-worker, Redis-backed, persistent).

The queue stores serialised Job metadata (not the GeoDataFrame).  Workers
re-hydrate jobs from the queue and delegate execution to ``JobRunner``.

Redis key layout (Mode C)::

    gispulse:jobs:pending        -- LIST  (FIFO via LPUSH / BRPOPLPUSH)
    gispulse:jobs:processing     -- LIST  (in-flight, for reliability)
    gispulse:jobs:{id}           -- HASH  (status, result, timestamps, error)
    gispulse:jobs:{id}:events    -- LIST  (status change events, capped)
"""

from __future__ import annotations

import asyncio
import json

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from core.logging import get_logger
from core.models import Job, JobStatus

log = get_logger(__name__)

# Maximum events kept per job in Redis
_MAX_EVENTS_PER_JOB = 50

# Default timeout for stuck job recovery (seconds)
from core.config import settings as _cfg

DEFAULT_JOB_TIMEOUT = _cfg.jobs.job_timeout


def _serialize_job(job: Job) -> str:
    """Serialise a Job to JSON for queue transport."""
    return json.dumps({
        "id": str(job.id),
        "name": job.name,
        "status": job.status.value,
        "dataset_id": str(job.dataset_id) if job.dataset_id else None,
        "parameters": job.parameters,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "result_path": job.result_path,
        "error_message": job.error_message,
        "attempts": job.attempts,
        "max_retries": job.max_retries,
    })


def _deserialize_job(data: str) -> Job:
    """Deserialise a JSON string back to a Job domain object."""
    d = json.loads(data)
    return Job(
        id=UUID(d["id"]),
        name=d["name"],
        status=JobStatus(d["status"]),
        dataset_id=UUID(d["dataset_id"]) if d.get("dataset_id") else None,
        parameters=d.get("parameters", {}),
        created_at=datetime.fromisoformat(d["created_at"]),
        started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None,
        completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
        result_path=d.get("result_path"),
        error_message=d.get("error_message"),
        attempts=d.get("attempts", 0),
        max_retries=d.get("max_retries", 3),
    )


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class JobQueue(ABC):
    """Abstract job queue contract.

    Implementations must support async enqueue, dequeue, status query,
    and status updates.  The queue is the single source of truth for job
    lifecycle in multi-worker deployments.
    """

    @abstractmethod
    async def enqueue(self, job: Job) -> str:
        """Add a job to the pending queue.

        Returns:
            The job ID as a string.
        """
        ...

    @abstractmethod
    async def dequeue(self, timeout: float = 0) -> Job | None:
        """Remove and return the next pending job.

        Args:
            timeout: Seconds to block waiting for a job.
                     0 = non-blocking (return None immediately if empty).

        Returns:
            A Job instance, or None if the queue is empty (non-blocking)
            or timed out (blocking).
        """
        ...

    @abstractmethod
    async def get_status(self, job_id: str) -> dict[str, Any] | None:
        """Return current status metadata for a job.

        Returns:
            Dict with at least ``{"status": "<JobStatus.value>"}``
            or None if the job ID is unknown.
        """
        ...

    @abstractmethod
    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        """Update the status of a job in the queue backend.

        Args:
            job_id:  Job identifier.
            status:  New status.
            result:  Optional result metadata (e.g. result_path).
            error:   Optional error message (for FAILED status).
        """
        ...

    @abstractmethod
    async def get_events(self, job_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Return status-change events for a job (for SSE streaming).

        Args:
            job_id: Job identifier.
            since:  Return events with index >= since (0 = all).

        Returns:
            List of event dicts, each with ``status``, ``timestamp``, etc.
        """
        ...

    @abstractmethod
    async def cancel(self, job_id: str) -> bool:
        """Mark a job as cancelled (FAILED with cancel reason).

        Returns:
            True if the job was found and cancelled, False otherwise.
        """
        ...

    @abstractmethod
    async def queue_size(self) -> int:
        """Return the number of pending jobs in the queue."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release resources (connections, pools)."""
        ...


# ---------------------------------------------------------------------------
# InMemoryJobQueue -- Mode A/B (single-process)
# ---------------------------------------------------------------------------


class InMemoryJobQueue(JobQueue):
    """Asyncio-based in-memory job queue.

    Suitable for single-process deployments where jobs are executed
    within the same Python process (Mode A portable, Mode B dev).

    Thread-safe for producers via ``asyncio.Queue``.  Status is tracked
    in a plain dict -- no persistence across restarts.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}

    async def enqueue(self, job: Job) -> str:
        job_id = str(job.id)
        now = datetime.now(timezone.utc).isoformat()
        self._jobs[job_id] = {
            "status": JobStatus.PENDING.value,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
        }
        self._events[job_id] = [{"status": JobStatus.PENDING.value, "timestamp": now}]
        await self._queue.put(job)
        log.info("job_enqueued", job_id=job_id, queue_size=self._queue.qsize())
        return job_id

    async def dequeue(self, timeout: float = 0) -> Job | None:
        if timeout <= 0:
            try:
                return self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def get_status(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.get(job_id)

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        if job_id not in self._jobs:
            log.warning("update_status_unknown_job", job_id=job_id)
            return
        now = datetime.now(timezone.utc).isoformat()
        entry = self._jobs[job_id]
        entry["status"] = status.value
        if status == JobStatus.RUNNING:
            entry["started_at"] = now
        if status in (JobStatus.COMPLETED, JobStatus.FAILED):
            entry["completed_at"] = now
        if result is not None:
            entry["result"] = result
        if error is not None:
            entry["error"] = error

        event = {"status": status.value, "timestamp": now}
        if error:
            event["error"] = error
        self._events.setdefault(job_id, []).append(event)

    async def get_events(self, job_id: str, since: int = 0) -> list[dict[str, Any]]:
        events = self._events.get(job_id, [])
        return events[since:]

    async def cancel(self, job_id: str) -> bool:
        if job_id not in self._jobs:
            return False
        status = self._jobs[job_id]["status"]
        if status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
            return False
        await self.update_status(
            job_id, JobStatus.FAILED, error="Cancelled by user"
        )
        return True

    async def queue_size(self) -> int:
        return self._queue.qsize()

    async def close(self) -> None:
        pass  # Nothing to release


# ---------------------------------------------------------------------------
# RedisJobQueue -- Mode C (multi-worker, persistent)
# ---------------------------------------------------------------------------


class RedisJobQueue(JobQueue):
    """Redis-backed persistent job queue for production multi-worker mode.

    Uses ``redis.asyncio`` for non-blocking I/O.  Key layout:

    - ``gispulse:jobs:pending``     -- FIFO list (LPUSH to enqueue)
    - ``gispulse:jobs:processing``  -- in-flight list (BRPOPLPUSH target)
    - ``gispulse:jobs:{id}``        -- HASH with status metadata
    - ``gispulse:jobs:{id}:events`` -- LIST of JSON status-change events

    Dequeue uses BRPOPLPUSH for at-least-once delivery: the job moves
    atomically from ``pending`` to ``processing``.  On completion (or
    failure), the worker removes it from ``processing``.
    """

    def __init__(self, redis_url: str, *, prefix: str = "gispulse:jobs") -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._prefix = prefix
        self._pending_key = f"{prefix}:pending"
        self._processing_key = f"{prefix}:processing"

    def _job_key(self, job_id: str) -> str:
        return f"{self._prefix}:{job_id}"

    def _events_key(self, job_id: str) -> str:
        return f"{self._prefix}:{job_id}:events"

    async def enqueue(self, job: Job) -> str:
        job_id = str(job.id)
        now = datetime.now(timezone.utc).isoformat()
        pipe = self._redis.pipeline()
        # Store job metadata hash
        pipe.hset(self._job_key(job_id), mapping={
            "status": JobStatus.PENDING.value,
            "created_at": now,
            "started_at": "",
            "completed_at": "",
            "result": "",
            "error": "",
            "job_data": _serialize_job(job),
        })
        # Push serialised job to pending list
        pipe.lpush(self._pending_key, _serialize_job(job))
        # Record event
        pipe.lpush(
            self._events_key(job_id),
            json.dumps({"status": JobStatus.PENDING.value, "timestamp": now}),
        )
        pipe.ltrim(self._events_key(job_id), 0, _MAX_EVENTS_PER_JOB - 1)
        await pipe.execute()
        log.info("job_enqueued_redis", job_id=job_id)
        return job_id

    async def dequeue(self, timeout: float = 0) -> Job | None:
        if timeout <= 0:
            # Non-blocking: RPOPLPUSH
            raw = await self._redis.rpoplpush(self._pending_key, self._processing_key)
        else:
            # Blocking: BRPOPLPUSH
            raw = await self._redis.brpoplpush(
                self._pending_key,
                self._processing_key,
                timeout=int(max(timeout, 1)),
            )
        if raw is None:
            return None
        return _deserialize_job(raw)

    async def get_status(self, job_id: str) -> dict[str, Any] | None:
        data = await self._redis.hgetall(self._job_key(job_id))
        if not data:
            return None
        return {
            "status": data.get("status", ""),
            "created_at": data.get("created_at", ""),
            "started_at": data.get("started_at", "") or None,
            "completed_at": data.get("completed_at", "") or None,
            "result": data.get("result", "") or None,
            "error": data.get("error", "") or None,
        }

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        updates: dict[str, str] = {"status": status.value}
        if status == JobStatus.RUNNING:
            updates["started_at"] = now
        if status in (JobStatus.COMPLETED, JobStatus.FAILED):
            updates["completed_at"] = now
        if result is not None:
            updates["result"] = json.dumps(result) if not isinstance(result, str) else result
        if error is not None:
            updates["error"] = error

        pipe = self._redis.pipeline()
        pipe.hset(self._job_key(job_id), mapping=updates)
        # Record event
        event = {"status": status.value, "timestamp": now}
        if error:
            event["error"] = error
        pipe.lpush(self._events_key(job_id), json.dumps(event))
        pipe.ltrim(self._events_key(job_id), 0, _MAX_EVENTS_PER_JOB - 1)
        await pipe.execute()

        # Bug fix #1: Remove job from processing list when terminal
        if status in (JobStatus.COMPLETED, JobStatus.FAILED):
            job_data = await self._redis.hget(self._job_key(job_id), "job_data")
            if job_data:
                await self._redis.lrem(self._processing_key, 0, job_data)
                log.debug(
                    "job_removed_from_processing",
                    job_id=job_id,
                    status=status.value,
                )

        log.info("job_status_updated", job_id=job_id, status=status.value)

    async def heartbeat(self, job_id: str) -> None:
        """Update the heartbeat timestamp for a running job.

        Called periodically by the worker to indicate the job is still alive.
        Used by ``recover_stuck_jobs()`` to detect stuck jobs.
        """
        now = datetime.now(timezone.utc).isoformat()
        await self._redis.hset(self._job_key(job_id), mapping={"last_heartbeat": now})

    async def get_events(self, job_id: str, since: int = 0) -> list[dict[str, Any]]:
        # Events are stored newest-first (LPUSH).  Reverse to get chronological order.
        raw_events = await self._redis.lrange(self._events_key(job_id), 0, -1)
        events = [json.loads(e) for e in reversed(raw_events)]
        return events[since:]

    async def cancel(self, job_id: str) -> bool:
        data = await self._redis.hgetall(self._job_key(job_id))
        if not data:
            return False
        status = data.get("status", "")
        if status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
            return False

        # Bug fix #2: Remove from pending list so worker won't dequeue it
        job_data = data.get("job_data", "")
        if job_data:
            await self._redis.lrem(self._pending_key, 0, job_data)

        await self.update_status(
            job_id, JobStatus.FAILED, error="Cancelled by user"
        )
        return True

    async def recover_stuck_jobs(
        self, timeout_seconds: int = DEFAULT_JOB_TIMEOUT
    ) -> list[str]:
        """Scan the processing list and recover jobs that exceeded the timeout.

        A job is considered stuck if its ``last_heartbeat`` (or ``started_at``
        as fallback) is older than ``timeout_seconds`` ago, or if it has
        neither timestamp (worker crashed before setting RUNNING).

        Recovery strategy:
        - If ``attempts < max_retries``: increment attempts and re-enqueue.
        - Otherwise: mark as FAILED.

        Returns:
            List of job IDs that were recovered.
        """
        processing_items = await self._redis.lrange(self._processing_key, 0, -1)
        recovered: list[str] = []
        now_ts = time.time()

        for raw in processing_items:
            try:
                job = _deserialize_job(raw)
            except Exception:
                log.warning("recover_stuck_invalid_entry", raw=raw[:80])
                continue

            job_id = str(job.id)
            data = await self._redis.hgetall(self._job_key(job_id))
            if not data:
                # Orphan entry in processing list -- remove it
                await self._redis.lrem(self._processing_key, 1, raw)
                continue

            status = data.get("status", "")
            # Skip already-terminal jobs (cleanup residue)
            if status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
                await self._redis.lrem(self._processing_key, 1, raw)
                recovered.append(job_id)
                continue

            # Use heartbeat if available, fall back to started_at
            heartbeat_str = data.get("last_heartbeat", "")
            started_at_str = data.get("started_at", "")
            ref_str = heartbeat_str or started_at_str

            if ref_str:
                ref_dt = datetime.fromisoformat(ref_str)
                elapsed = now_ts - ref_dt.timestamp()
            else:
                # No timestamps means worker never got to RUNNING -- treat as stuck
                elapsed = timeout_seconds + 1

            if elapsed > timeout_seconds:
                # Remove from processing first
                await self._redis.lrem(self._processing_key, 1, raw)

                # Retry logic: re-enqueue if under max_retries
                attempts = job.attempts + 1
                max_retries = job.max_retries

                if attempts < max_retries:
                    # Re-enqueue with incremented attempts
                    job.attempts = attempts
                    job.status = JobStatus.PENDING
                    job.started_at = None
                    await self.enqueue(job)
                    log.warning(
                        "stuck_job_requeued",
                        job_id=job_id,
                        attempt=attempts,
                        max_retries=max_retries,
                        elapsed_seconds=round(elapsed, 1),
                    )
                else:
                    await self.update_status(
                        job_id,
                        JobStatus.FAILED,
                        error=f"Job timed out after {timeout_seconds}s "
                        f"(attempt {attempts}/{max_retries}, no retries left)",
                    )
                    log.warning(
                        "stuck_job_failed_no_retries",
                        job_id=job_id,
                        attempts=attempts,
                        elapsed_seconds=round(elapsed, 1),
                    )
                recovered.append(job_id)

        if recovered:
            log.info("stuck_jobs_recovery_complete", recovered_count=len(recovered))
        return recovered

    async def queue_size(self) -> int:
        return await self._redis.llen(self._pending_key)

    async def close(self) -> None:
        await self._redis.aclose()
