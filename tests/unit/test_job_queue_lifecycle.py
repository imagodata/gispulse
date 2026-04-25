"""
Tests for job queue lifecycle fixes (Sprint S7 -- Redis reliability).

Covers:
- Bug #1: Jobs removed from processing list on COMPLETED/FAILED
- Bug #2: Cancel removes job from pending list
- Bug #3: Stuck job recovery with timeout
- Bug #4: InMemoryJobQueue dequeue does not block indefinitely
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core.models import Job, JobStatus
from orchestration.job_queue import (
    DEFAULT_JOB_TIMEOUT,
    InMemoryJobQueue,
    RedisJobQueue,
    _serialize_job,
)


# ---------------------------------------------------------------------------
# Helpers: Fake Redis for unit testing
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal async Redis mock that implements the subset used by RedisJobQueue.

    Stores data in plain Python dicts/lists so we can assert on internal state
    without a running Redis server.
    """

    def __init__(self):
        self._hashes: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}

    async def hset(self, key: str, mapping: dict[str, str] | None = None, **kw):
        if key not in self._hashes:
            self._hashes[key] = {}
        if mapping:
            self._hashes[key].update(mapping)

    async def hget(self, key: str, field: str) -> str | None:
        return self._hashes.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return self._hashes.get(key, {})

    async def lpush(self, key: str, *values: str):
        if key not in self._lists:
            self._lists[key] = []
        for v in values:
            self._lists[key].insert(0, v)

    async def ltrim(self, key: str, start: int, stop: int):
        if key in self._lists:
            self._lists[key] = self._lists[key][start : stop + 1]

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        lst = self._lists.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start : stop + 1]

    async def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    async def lrem(self, key: str, count: int, value: str) -> int:
        lst = self._lists.get(key, [])
        removed = 0
        if count == 0:
            # Remove all occurrences
            new_lst = [x for x in lst if x != value]
            removed = len(lst) - len(new_lst)
            self._lists[key] = new_lst
        else:
            n = abs(count)
            new_lst = []
            for item in lst:
                if item == value and removed < n:
                    removed += 1
                else:
                    new_lst.append(item)
            self._lists[key] = new_lst
        return removed

    async def rpoplpush(self, src: str, dst: str) -> str | None:
        lst = self._lists.get(src, [])
        if not lst:
            return None
        val = lst.pop()
        if dst not in self._lists:
            self._lists[dst] = []
        self._lists[dst].insert(0, val)
        return val

    async def brpoplpush(self, src: str, dst: str, timeout: int = 0) -> str | None:
        return await self.rpoplpush(src, dst)

    def pipeline(self):
        return FakePipeline(self)

    async def aclose(self):
        pass


class FakePipeline:
    """Batches commands and executes them sequentially."""

    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._cmds: list[tuple[str, tuple, dict]] = []

    def hset(self, key, mapping=None, **kw):
        self._cmds.append(("hset", (key,), {"mapping": mapping}))
        return self

    def lpush(self, key, *values):
        self._cmds.append(("lpush", (key, *values), {}))
        return self

    def ltrim(self, key, start, stop):
        self._cmds.append(("ltrim", (key, start, stop), {}))
        return self

    async def execute(self):
        for cmd, args, kwargs in self._cmds:
            method = getattr(self._redis, cmd)
            await method(*args, **kwargs)
        self._cmds.clear()


# ---------------------------------------------------------------------------
# Fixture: RedisJobQueue with FakeRedis
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_redis():
    return FakeRedis()


@pytest.fixture()
def redis_queue(fake_redis) -> RedisJobQueue:
    """Create a RedisJobQueue wired to FakeRedis (no real connection)."""
    queue = RedisJobQueue.__new__(RedisJobQueue)
    queue._redis = fake_redis
    queue._prefix = "gispulse:jobs"
    queue._pending_key = "gispulse:jobs:pending"
    queue._processing_key = "gispulse:jobs:processing"
    return queue


# ---------------------------------------------------------------------------
# Bug #1: Processing list cleanup on COMPLETED / FAILED
# ---------------------------------------------------------------------------


class TestBug1ProcessingCleanup:
    """update_status() must LREM job from processing list on terminal status."""

    @pytest.mark.asyncio
    async def test_completed_removes_from_processing(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        job = Job(name="test_completed")
        await redis_queue.enqueue(job)
        job_id = str(job.id)

        # Simulate dequeue (moves from pending to processing)
        await redis_queue.dequeue()
        assert await fake_redis.llen("gispulse:jobs:processing") == 1

        # Mark RUNNING then COMPLETED
        await redis_queue.update_status(job_id, JobStatus.RUNNING)
        await redis_queue.update_status(job_id, JobStatus.COMPLETED, result="/out.gpkg")

        # Processing list should be empty
        assert await fake_redis.llen("gispulse:jobs:processing") == 0

    @pytest.mark.asyncio
    async def test_failed_removes_from_processing(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        job = Job(name="test_failed")
        await redis_queue.enqueue(job)
        job_id = str(job.id)

        await redis_queue.dequeue()
        assert await fake_redis.llen("gispulse:jobs:processing") == 1

        await redis_queue.update_status(job_id, JobStatus.RUNNING)
        await redis_queue.update_status(
            job_id, JobStatus.FAILED, error="crash"
        )

        assert await fake_redis.llen("gispulse:jobs:processing") == 0

    @pytest.mark.asyncio
    async def test_running_does_not_remove_from_processing(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        job = Job(name="test_running_stays")
        await redis_queue.enqueue(job)
        job_id = str(job.id)

        await redis_queue.dequeue()
        await redis_queue.update_status(job_id, JobStatus.RUNNING)

        # Should still be in processing
        assert await fake_redis.llen("gispulse:jobs:processing") == 1


# ---------------------------------------------------------------------------
# Bug #2: Cancel removes from pending list
# ---------------------------------------------------------------------------


class TestBug2CancelRemovesPending:
    """cancel() must LREM job from pending list."""

    @pytest.mark.asyncio
    async def test_cancel_removes_from_pending(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        job = Job(name="test_cancel_pending")
        await redis_queue.enqueue(job)
        job_id = str(job.id)

        # Job is in pending list
        assert await fake_redis.llen("gispulse:jobs:pending") == 1

        result = await redis_queue.cancel(job_id)
        assert result is True

        # Pending list should be empty -- worker won't dequeue it
        assert await fake_redis.llen("gispulse:jobs:pending") == 0

        # Status should be FAILED with cancel reason
        status = await redis_queue.get_status(job_id)
        assert status["status"] == "failed"
        assert status["error"] == "Cancelled by user"

    @pytest.mark.asyncio
    async def test_cancel_already_completed_returns_false(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        job = Job(name="test_cancel_completed")
        await redis_queue.enqueue(job)
        job_id = str(job.id)
        await redis_queue.dequeue()
        await redis_queue.update_status(job_id, JobStatus.COMPLETED)

        result = await redis_queue.cancel(job_id)
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_unknown_job_returns_false(
        self, redis_queue: RedisJobQueue
    ):
        result = await redis_queue.cancel("nonexistent-id")
        assert result is False


# ---------------------------------------------------------------------------
# Bug #3: Stuck job recovery
# ---------------------------------------------------------------------------


class TestBug3StuckJobRecovery:
    """recover_stuck_jobs() must timeout and fail stuck jobs."""

    @pytest.mark.asyncio
    async def test_recover_stuck_job_requeues_under_max_retries(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        """Stuck job with attempts < max_retries should be re-enqueued."""
        job = Job(name="stuck_job", max_retries=3)
        await redis_queue.enqueue(job)
        job_id = str(job.id)

        # Simulate dequeue + RUNNING with a very old started_at
        await redis_queue.dequeue()
        await redis_queue.update_status(job_id, JobStatus.RUNNING)

        # Manually backdate started_at to 2 hours ago
        old_time = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
        fake_redis._hashes[f"gispulse:jobs:{job_id}"]["started_at"] = old_time

        recovered = await redis_queue.recover_stuck_jobs(timeout_seconds=60)
        assert job_id in recovered

        # Should be re-enqueued (back in pending), not failed
        pending_len = await fake_redis.llen("gispulse:jobs:pending")
        assert pending_len >= 1

    @pytest.mark.asyncio
    async def test_recover_stuck_job_fails_after_max_retries(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        """Stuck job at max_retries should be marked FAILED."""
        job = Job(name="exhausted_job", max_retries=1, attempts=0)
        await redis_queue.enqueue(job)
        job_id = str(job.id)

        await redis_queue.dequeue()
        await redis_queue.update_status(job_id, JobStatus.RUNNING)

        old_time = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
        fake_redis._hashes[f"gispulse:jobs:{job_id}"]["started_at"] = old_time

        recovered = await redis_queue.recover_stuck_jobs(timeout_seconds=60)
        assert job_id in recovered

        status = await redis_queue.get_status(job_id)
        assert status["status"] == "failed"
        assert "no retries left" in status["error"]

    @pytest.mark.asyncio
    async def test_recover_no_started_at_treated_as_stuck(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        """Job in processing with no started_at (worker crashed before RUNNING)."""
        job = Job(name="no_started", max_retries=3)
        await redis_queue.enqueue(job)
        job_id = str(job.id)

        # Dequeue moves to processing, but we never call update_status(RUNNING)
        await redis_queue.dequeue()

        recovered = await redis_queue.recover_stuck_jobs(timeout_seconds=60)
        assert job_id in recovered

        # Should be re-enqueued (attempts=0 < max_retries=3)
        pending_len = await fake_redis.llen("gispulse:jobs:pending")
        assert pending_len >= 1

    @pytest.mark.asyncio
    async def test_recover_skips_recent_jobs(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        """Jobs with recent started_at should not be recovered."""
        job = Job(name="recent_job")
        await redis_queue.enqueue(job)
        job_id = str(job.id)

        await redis_queue.dequeue()
        await redis_queue.update_status(job_id, JobStatus.RUNNING)
        # started_at is now -- should NOT be recovered with 3600s timeout

        recovered = await redis_queue.recover_stuck_jobs(timeout_seconds=3600)
        assert job_id not in recovered

        status = await redis_queue.get_status(job_id)
        assert status["status"] == "running"

    @pytest.mark.asyncio
    async def test_recover_cleans_up_terminal_residue(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        """Already completed/failed jobs lingering in processing should be cleaned."""
        job = Job(name="residue_job")
        await redis_queue.enqueue(job)
        job_id = str(job.id)
        await redis_queue.dequeue()

        # Simulate: status is COMPLETED in hash but entry still in processing
        # (e.g. LREM failed due to transient error)
        await redis_queue.update_status(job_id, JobStatus.COMPLETED)
        # Re-add to processing to simulate residue
        job_data = _serialize_job(job)
        await fake_redis.lpush("gispulse:jobs:processing", job_data)

        processing_before = await fake_redis.llen("gispulse:jobs:processing")
        recovered = await redis_queue.recover_stuck_jobs()
        processing_after = await fake_redis.llen("gispulse:jobs:processing")

        assert job_id in recovered
        assert processing_after < processing_before

    @pytest.mark.asyncio
    async def test_recover_empty_processing_list(
        self, redis_queue: RedisJobQueue
    ):
        """No crash when processing list is empty."""
        recovered = await redis_queue.recover_stuck_jobs()
        assert recovered == []

    @pytest.mark.asyncio
    async def test_default_timeout_from_env(self):
        """DEFAULT_JOB_TIMEOUT reads from GISPULSE_JOB_TIMEOUT env var."""
        # The constant is evaluated at import time, but we test the default
        assert DEFAULT_JOB_TIMEOUT == 3600  # default when env not set


# ---------------------------------------------------------------------------
# Bug #4: InMemoryJobQueue dequeue non-blocking
# ---------------------------------------------------------------------------


class TestBug4InMemoryDequeueTimeout:
    """InMemoryJobQueue.dequeue() must not block indefinitely."""

    @pytest.mark.asyncio
    async def test_dequeue_empty_nonblocking(self):
        queue = InMemoryJobQueue()
        result = await queue.dequeue(timeout=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_dequeue_empty_with_timeout_returns_none(self):
        queue = InMemoryJobQueue()
        start = time.monotonic()
        result = await queue.dequeue(timeout=0.2)
        elapsed = time.monotonic() - start
        assert result is None
        # Should have waited approximately 0.2s, not forever
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_dequeue_negative_timeout_nonblocking(self):
        queue = InMemoryJobQueue()
        result = await queue.dequeue(timeout=-1)
        assert result is None

    @pytest.mark.asyncio
    async def test_dequeue_with_timeout_returns_job_when_available(self):
        queue = InMemoryJobQueue()
        job = Job(name="timeout_test")

        # Enqueue after a short delay
        async def delayed_enqueue():
            await asyncio.sleep(0.05)
            await queue.enqueue(job)

        task = asyncio.create_task(delayed_enqueue())
        result = await queue.dequeue(timeout=2.0)
        await task

        assert result is not None
        assert result.id == job.id


# ---------------------------------------------------------------------------
# InMemoryJobQueue -- cancel does not leave ghost in queue (regression)
# ---------------------------------------------------------------------------


class TestInMemoryCancelRegression:
    """Verify InMemoryJobQueue cancel behavior is consistent."""

    @pytest.mark.asyncio
    async def test_cancel_marks_as_failed(self):
        queue = InMemoryJobQueue()
        job = Job(name="cancel_test")
        await queue.enqueue(job)
        job_id = str(job.id)

        result = await queue.cancel(job_id)
        assert result is True

        status = await queue.get_status(job_id)
        assert status["status"] == "failed"
        assert status["error"] == "Cancelled by user"

    @pytest.mark.asyncio
    async def test_cancel_completed_returns_false(self):
        queue = InMemoryJobQueue()
        job = Job(name="cancel_done")
        await queue.enqueue(job)
        await queue.update_status(str(job.id), JobStatus.COMPLETED)

        result = await queue.cancel(str(job.id))
        assert result is False


# ---------------------------------------------------------------------------
# Heartbeat + recovery uses heartbeat timestamp
# ---------------------------------------------------------------------------


class TestHeartbeat:
    """Heartbeat updates last_heartbeat, recovery uses it over started_at."""

    @pytest.mark.asyncio
    async def test_heartbeat_sets_timestamp(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        job = Job(name="heartbeat_test")
        await redis_queue.enqueue(job)
        job_id = str(job.id)
        await redis_queue.dequeue()

        await redis_queue.heartbeat(job_id)

        hb = fake_redis._hashes.get(f"gispulse:jobs:{job_id}", {}).get("last_heartbeat")
        assert hb is not None
        # Should be a valid ISO timestamp
        datetime.fromisoformat(hb)

    @pytest.mark.asyncio
    async def test_recovery_uses_heartbeat_over_started_at(
        self, redis_queue: RedisJobQueue, fake_redis: FakeRedis
    ):
        """If heartbeat is recent but started_at is old, job should NOT be recovered."""
        job = Job(name="recent_hb", max_retries=3)
        await redis_queue.enqueue(job)
        job_id = str(job.id)
        await redis_queue.dequeue()
        await redis_queue.update_status(job_id, JobStatus.RUNNING)

        # Backdate started_at but set recent heartbeat
        old_time = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
        fake_redis._hashes[f"gispulse:jobs:{job_id}"]["started_at"] = old_time
        await redis_queue.heartbeat(job_id)  # Recent heartbeat

        recovered = await redis_queue.recover_stuck_jobs(timeout_seconds=60)
        assert job_id not in recovered  # Heartbeat is recent — not stuck


# ---------------------------------------------------------------------------
# max_retries serialization
# ---------------------------------------------------------------------------


class TestMaxRetriesSerialization:
    """Job max_retries must survive serialize/deserialize."""

    def test_max_retries_serialized(self):
        from orchestration.job_queue import _deserialize_job, _serialize_job
        job = Job(name="retry_test", max_retries=5, attempts=2)
        raw = _serialize_job(job)
        restored = _deserialize_job(raw)
        assert restored.max_retries == 5
        assert restored.attempts == 2
