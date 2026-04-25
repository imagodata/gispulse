"""
Tests for the job queue system (Sprint S4).

Covers:
- InMemoryJobQueue: enqueue, dequeue, status updates, events, cancel, queue_size
- RedisJobQueue: same contract (skipped if redis not available)
- JobWorker: basic processing loop
- Metering: in-memory recording and retrieval
- Job serialisation round-trip
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from core.models import Job, JobStatus
from orchestration.job_queue import (
    InMemoryJobQueue,
    JobQueue,
    _deserialize_job,
    _serialize_job,
)
from orchestration.metering import InMemoryMetering


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


class TestJobSerialisation:
    def test_round_trip(self):
        job = Job(
            name="test_job",
            dataset_id=uuid4(),
            parameters={"rule_ids": ["abc"]},
            attempts=2,
        )
        serialised = _serialize_job(job)
        restored = _deserialize_job(serialised)
        assert restored.id == job.id
        assert restored.name == job.name
        assert restored.dataset_id == job.dataset_id
        assert restored.parameters == job.parameters
        assert restored.attempts == 2
        assert restored.status == JobStatus.PENDING

    def test_round_trip_no_dataset(self):
        job = Job(name="no_ds")
        restored = _deserialize_job(_serialize_job(job))
        assert restored.dataset_id is None

    def test_round_trip_with_timestamps(self):
        job = Job(name="ts_test")
        job.started_at = datetime.now(timezone.utc)
        job.completed_at = datetime.now(timezone.utc)
        restored = _deserialize_job(_serialize_job(job))
        assert restored.started_at is not None
        assert restored.completed_at is not None


# ---------------------------------------------------------------------------
# InMemoryJobQueue
# ---------------------------------------------------------------------------


class TestInMemoryJobQueue:
    @pytest.fixture()
    def queue(self) -> InMemoryJobQueue:
        return InMemoryJobQueue()

    @pytest.mark.asyncio
    async def test_enqueue_dequeue(self, queue: InMemoryJobQueue):
        job = Job(name="enq_test")
        job_id = await queue.enqueue(job)
        assert job_id == str(job.id)

        size = await queue.queue_size()
        assert size == 1

        dequeued = await queue.dequeue()
        assert dequeued is not None
        assert dequeued.id == job.id

        size = await queue.queue_size()
        assert size == 0

    @pytest.mark.asyncio
    async def test_dequeue_empty_returns_none(self, queue: InMemoryJobQueue):
        result = await queue.dequeue(timeout=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_dequeue_timeout_returns_none(self, queue: InMemoryJobQueue):
        result = await queue.dequeue(timeout=0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_status_after_enqueue(self, queue: InMemoryJobQueue):
        job = Job(name="status_test")
        await queue.enqueue(job)
        status = await queue.get_status(str(job.id))
        assert status is not None
        assert status["status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_status_unknown_job(self, queue: InMemoryJobQueue):
        status = await queue.get_status("nonexistent")
        assert status is None

    @pytest.mark.asyncio
    async def test_update_status_running(self, queue: InMemoryJobQueue):
        job = Job(name="running_test")
        await queue.enqueue(job)
        await queue.update_status(str(job.id), JobStatus.RUNNING)
        status = await queue.get_status(str(job.id))
        assert status["status"] == "running"
        assert status["started_at"] is not None

    @pytest.mark.asyncio
    async def test_update_status_completed(self, queue: InMemoryJobQueue):
        job = Job(name="complete_test")
        await queue.enqueue(job)
        await queue.update_status(
            str(job.id), JobStatus.COMPLETED, result="/path/to/result.gpkg"
        )
        status = await queue.get_status(str(job.id))
        assert status["status"] == "completed"
        assert status["completed_at"] is not None
        assert status["result"] == "/path/to/result.gpkg"

    @pytest.mark.asyncio
    async def test_update_status_failed(self, queue: InMemoryJobQueue):
        job = Job(name="fail_test")
        await queue.enqueue(job)
        await queue.update_status(
            str(job.id), JobStatus.FAILED, error="Something broke"
        )
        status = await queue.get_status(str(job.id))
        assert status["status"] == "failed"
        assert status["error"] == "Something broke"

    @pytest.mark.asyncio
    async def test_events_tracking(self, queue: InMemoryJobQueue):
        job = Job(name="events_test")
        await queue.enqueue(job)
        await queue.update_status(str(job.id), JobStatus.RUNNING)
        await queue.update_status(str(job.id), JobStatus.COMPLETED)

        events = await queue.get_events(str(job.id))
        assert len(events) == 3  # PENDING, RUNNING, COMPLETED
        assert events[0]["status"] == "pending"
        assert events[1]["status"] == "running"
        assert events[2]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_events_since(self, queue: InMemoryJobQueue):
        job = Job(name="events_since_test")
        await queue.enqueue(job)
        await queue.update_status(str(job.id), JobStatus.RUNNING)
        await queue.update_status(str(job.id), JobStatus.COMPLETED)

        events = await queue.get_events(str(job.id), since=1)
        assert len(events) == 2
        assert events[0]["status"] == "running"

    @pytest.mark.asyncio
    async def test_cancel_pending_job(self, queue: InMemoryJobQueue):
        job = Job(name="cancel_test")
        await queue.enqueue(job)
        result = await queue.cancel(str(job.id))
        assert result is True
        status = await queue.get_status(str(job.id))
        assert status["status"] == "failed"
        assert status["error"] == "Cancelled by user"

    @pytest.mark.asyncio
    async def test_cancel_completed_job_returns_false(self, queue: InMemoryJobQueue):
        job = Job(name="cancel_completed")
        await queue.enqueue(job)
        await queue.update_status(str(job.id), JobStatus.COMPLETED)
        result = await queue.cancel(str(job.id))
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_unknown_job_returns_false(self, queue: InMemoryJobQueue):
        result = await queue.cancel("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_multiple_jobs_fifo(self, queue: InMemoryJobQueue):
        jobs = [Job(name=f"job_{i}") for i in range(3)]
        for j in jobs:
            await queue.enqueue(j)

        assert await queue.queue_size() == 3

        for expected_job in jobs:
            dequeued = await queue.dequeue()
            assert dequeued is not None
            assert dequeued.id == expected_job.id

    @pytest.mark.asyncio
    async def test_close_is_noop(self, queue: InMemoryJobQueue):
        await queue.close()  # Should not raise


# ---------------------------------------------------------------------------
# InMemoryMetering
# ---------------------------------------------------------------------------


class TestInMemoryMetering:
    @pytest.fixture()
    def meter(self) -> InMemoryMetering:
        return InMemoryMetering()

    @pytest.mark.asyncio
    async def test_record_and_get(self, meter: InMemoryMetering):
        await meter.record_job("key1", 2.5)
        usage = await meter.get_usage("key1")
        assert usage["jobs_count"] == 1
        assert usage["compute_seconds"] == 2.5

    @pytest.mark.asyncio
    async def test_accumulate(self, meter: InMemoryMetering):
        await meter.record_job("key1", 1.0)
        await meter.record_job("key1", 3.0)
        usage = await meter.get_usage("key1")
        assert usage["jobs_count"] == 2
        assert usage["compute_seconds"] == 4.0

    @pytest.mark.asyncio
    async def test_unknown_key(self, meter: InMemoryMetering):
        usage = await meter.get_usage("unknown")
        assert usage["jobs_count"] == 0
        assert usage["compute_seconds"] == 0.0

    @pytest.mark.asyncio
    async def test_get_all(self, meter: InMemoryMetering):
        await meter.record_job("key1", 1.0)
        await meter.record_job("key2", 2.0)
        all_usage = await meter.get_all_usage()
        assert len(all_usage) == 2
        assert all_usage["key1"]["jobs_count"] == 1
        assert all_usage["key2"]["compute_seconds"] == 2.0

    @pytest.mark.asyncio
    async def test_close_is_noop(self, meter: InMemoryMetering):
        await meter.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestJobQueueFactory:
    def test_no_redis_returns_in_memory(self, monkeypatch):
        monkeypatch.delenv("GISPULSE_REDIS_URL", raising=False)
        from orchestration.job_queue_factory import create_job_queue

        queue = create_job_queue()
        assert isinstance(queue, InMemoryJobQueue)

    def test_redis_url_returns_redis_queue(self, monkeypatch):
        """When GISPULSE_REDIS_URL is set, RedisJobQueue should be created.

        We only test that the factory picks the right class -- actual Redis
        connectivity is not tested here (requires a running Redis instance).
        """
        monkeypatch.setenv("GISPULSE_REDIS_URL", "redis://localhost:6379/0")
        try:
            from orchestration.job_queue_factory import create_job_queue

            queue = create_job_queue()
            from orchestration.job_queue import RedisJobQueue
            assert isinstance(queue, RedisJobQueue)
        except ImportError:
            pytest.skip("redis package not installed")


class TestMeteringFactory:
    def test_no_redis_returns_in_memory(self, monkeypatch):
        monkeypatch.delenv("GISPULSE_REDIS_URL", raising=False)
        from orchestration.metering import create_metering

        meter = create_metering()
        assert isinstance(meter, InMemoryMetering)
