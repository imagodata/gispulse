"""
Unit tests for the pipeline scheduler (orchestration/scheduler.py)
and schedule repository (persistence/schedule_repository.py).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from gispulse.orchestration.job_queue import InMemoryJobQueue
from gispulse.orchestration.scheduler import (
    PipelineScheduler,
    ScheduledPipeline,
    _compute_next_run,
    validate_cron_expression,
)
from gispulse.persistence.schedule_repository import ScheduleRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a temporary SQLite DB."""
    return tmp_path / "test_schedules.db"


@pytest.fixture
def schedule_repo(tmp_db):
    return ScheduleRepository(db_path=tmp_db)


@pytest.fixture
def job_queue():
    return InMemoryJobQueue()


@pytest.fixture
def sample_schedule():
    return ScheduledPipeline(
        name="Nightly buffer",
        cron_expression="0 2 * * *",
        pipeline_config={"rules": ["buffer_50m"], "input": "parcels", "output": "buffered"},
        enabled=True,
        created_by="test-user",
    )


# ---------------------------------------------------------------------------
# Test: cron expression validation
# ---------------------------------------------------------------------------


class TestCronValidation:
    def test_valid_cron(self):
        assert validate_cron_expression("0 */6 * * *") is True

    def test_valid_cron_every_minute(self):
        assert validate_cron_expression("* * * * *") is True

    def test_invalid_cron_raises(self):
        with pytest.raises(ValueError, match="Invalid cron expression"):
            validate_cron_expression("not a cron")

    def test_compute_next_run_returns_future(self):
        now = datetime.now(timezone.utc)
        next_run = _compute_next_run("* * * * *", base_time=now)
        assert next_run > now

    def test_compute_next_run_timezone_aware(self):
        next_run = _compute_next_run("0 12 * * *")
        assert next_run.tzinfo is not None


# ---------------------------------------------------------------------------
# Test: ScheduleRepository (SQLite CRUD)
# ---------------------------------------------------------------------------


class TestScheduleRepository:
    def test_save_and_get(self, schedule_repo, sample_schedule):
        schedule_repo.save(sample_schedule)
        retrieved = schedule_repo.get(sample_schedule.id)
        assert retrieved is not None
        assert retrieved.name == "Nightly buffer"
        assert retrieved.cron_expression == "0 2 * * *"
        assert retrieved.pipeline_config == {
            "rules": ["buffer_50m"],
            "input": "parcels",
            "output": "buffered",
        }
        assert retrieved.enabled is True
        assert retrieved.created_by == "test-user"

    def test_list_all(self, schedule_repo, sample_schedule):
        schedule_repo.save(sample_schedule)
        sp2 = ScheduledPipeline(name="Hourly check", cron_expression="0 * * * *")
        schedule_repo.save(sp2)
        all_schedules = schedule_repo.list_all()
        assert len(all_schedules) == 2

    def test_list_enabled(self, schedule_repo):
        sp1 = ScheduledPipeline(name="Active", cron_expression="0 * * * *", enabled=True)
        sp2 = ScheduledPipeline(name="Disabled", cron_expression="0 * * * *", enabled=False)
        schedule_repo.save(sp1)
        schedule_repo.save(sp2)
        enabled = schedule_repo.list_enabled()
        assert len(enabled) == 1
        assert enabled[0].name == "Active"

    def test_delete(self, schedule_repo, sample_schedule):
        schedule_repo.save(sample_schedule)
        assert schedule_repo.delete(sample_schedule.id) is True
        assert schedule_repo.get(sample_schedule.id) is None

    def test_delete_nonexistent(self, schedule_repo):
        assert schedule_repo.delete(uuid4()) is False

    def test_upsert(self, schedule_repo, sample_schedule):
        schedule_repo.save(sample_schedule)
        sample_schedule.name = "Updated name"
        schedule_repo.save(sample_schedule)
        retrieved = schedule_repo.get(sample_schedule.id)
        assert retrieved.name == "Updated name"
        assert schedule_repo.count() == 1

    def test_last_run_persisted(self, schedule_repo, sample_schedule):
        now = datetime.now(timezone.utc)
        sample_schedule.last_run = now
        schedule_repo.save(sample_schedule)
        retrieved = schedule_repo.get(sample_schedule.id)
        assert retrieved.last_run is not None
        # Compare with seconds precision (ISO serialisation may lose microseconds)
        assert abs((retrieved.last_run - now).total_seconds()) < 1

    def test_clear(self, schedule_repo, sample_schedule):
        schedule_repo.save(sample_schedule)
        schedule_repo.clear()
        assert schedule_repo.count() == 0


# ---------------------------------------------------------------------------
# Test: PipelineScheduler
# ---------------------------------------------------------------------------


class TestPipelineScheduler:
    @pytest.fixture(autouse=True)
    def _set_pro_tier(self):
        """Set the tier to pro for scheduler tests."""
        with patch.dict(os.environ, {
            "GISPULSE_TIER": "pro",
            "GISPULSE_LICENCE_SKIP_VERIFY": "true",
            "GISPULSE_LICENSE_KEY": "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        }):
            yield

    @pytest.mark.asyncio
    async def test_add_schedule(self, job_queue, schedule_repo, sample_schedule):
        scheduler = PipelineScheduler(job_queue=job_queue, schedule_repo=schedule_repo)
        await scheduler.start()
        try:
            result = await scheduler.add(sample_schedule)
            assert result.next_run is not None
            assert scheduler.get(str(sample_schedule.id)) is not None

            # Verify persisted
            persisted = schedule_repo.get(sample_schedule.id)
            assert persisted is not None
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_remove_schedule(self, job_queue, schedule_repo, sample_schedule):
        scheduler = PipelineScheduler(job_queue=job_queue, schedule_repo=schedule_repo)
        await scheduler.start()
        try:
            await scheduler.add(sample_schedule)
            removed = await scheduler.remove(str(sample_schedule.id))
            assert removed is True
            assert scheduler.get(str(sample_schedule.id)) is None
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, job_queue):
        scheduler = PipelineScheduler(job_queue=job_queue)
        await scheduler.start()
        try:
            removed = await scheduler.remove("nonexistent-id")
            assert removed is False
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_update_schedule(self, job_queue, schedule_repo, sample_schedule):
        scheduler = PipelineScheduler(job_queue=job_queue, schedule_repo=schedule_repo)
        await scheduler.start()
        try:
            await scheduler.add(sample_schedule)
            updated = await scheduler.update(
                str(sample_schedule.id),
                cron_expression="*/30 * * * *",
                name="Updated schedule",
            )
            assert updated is not None
            assert updated.cron_expression == "*/30 * * * *"
            assert updated.name == "Updated schedule"
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, job_queue):
        scheduler = PipelineScheduler(job_queue=job_queue)
        await scheduler.start()
        try:
            result = await scheduler.update("nope", name="test")
            assert result is None
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_list_schedules(self, job_queue, sample_schedule):
        scheduler = PipelineScheduler(job_queue=job_queue)
        await scheduler.start()
        try:
            await scheduler.add(sample_schedule)
            schedules = await scheduler.list_schedules()
            assert len(schedules) == 1
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_run_now_enqueues_job(self, job_queue, sample_schedule):
        scheduler = PipelineScheduler(job_queue=job_queue)
        await scheduler.start()
        try:
            await scheduler.add(sample_schedule)
            job_id = await scheduler.run_now(str(sample_schedule.id))
            assert job_id is not None

            # Verify job was enqueued
            size = await job_queue.queue_size()
            assert size == 1

            # Dequeue and check
            job = await job_queue.dequeue()
            assert job is not None
            assert job.name == f"scheduled:{sample_schedule.name}"
            assert job.parameters["schedule_id"] == str(sample_schedule.id)
            assert job.parameters["triggered_by"] == "scheduler"
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_run_now_nonexistent(self, job_queue):
        scheduler = PipelineScheduler(job_queue=job_queue)
        await scheduler.start()
        try:
            result = await scheduler.run_now("nonexistent")
            assert result is None
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_tick_fires_due_schedule(self, job_queue, sample_schedule):
        """Test that _tick enqueues a job when a schedule is due."""
        scheduler = PipelineScheduler(job_queue=job_queue)
        # Don't start the loop, just add manually and tick
        scheduler._running = True

        # Set next_run in the past so it fires immediately
        sample_schedule.next_run = datetime(2020, 1, 1, tzinfo=timezone.utc)
        scheduler._schedules[str(sample_schedule.id)] = sample_schedule

        await scheduler._tick()

        size = await job_queue.queue_size()
        assert size == 1

        # Verify last_run was updated and next_run moved forward
        updated = scheduler.get(str(sample_schedule.id))
        assert updated.last_run is not None
        assert updated.next_run > datetime(2020, 1, 1, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_tick_skips_disabled(self, job_queue, sample_schedule):
        """Disabled schedules should not fire."""
        scheduler = PipelineScheduler(job_queue=job_queue)
        scheduler._running = True

        sample_schedule.enabled = False
        sample_schedule.next_run = datetime(2020, 1, 1, tzinfo=timezone.utc)
        scheduler._schedules[str(sample_schedule.id)] = sample_schedule

        await scheduler._tick()

        size = await job_queue.queue_size()
        assert size == 0

    @pytest.mark.asyncio
    async def test_tick_skips_future_schedule(self, job_queue, sample_schedule):
        """Schedules with next_run in the future should not fire."""
        scheduler = PipelineScheduler(job_queue=job_queue)
        scheduler._running = True

        sample_schedule.next_run = datetime(2099, 1, 1, tzinfo=timezone.utc)
        scheduler._schedules[str(sample_schedule.id)] = sample_schedule

        await scheduler._tick()

        size = await job_queue.queue_size()
        assert size == 0

    @pytest.mark.asyncio
    async def test_start_loads_persisted_schedules(self, job_queue, schedule_repo, sample_schedule):
        """Scheduler should load enabled schedules from repo on start."""
        schedule_repo.save(sample_schedule)

        scheduler = PipelineScheduler(job_queue=job_queue, schedule_repo=schedule_repo)
        await scheduler.start()
        try:
            schedules = await scheduler.list_schedules()
            assert len(schedules) == 1
            assert schedules[0].name == sample_schedule.name
            # next_run should have been recomputed
            assert schedules[0].next_run is not None
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_requires_pro_tier(self, job_queue):
        """Scheduler should refuse to start on community tier."""
        with patch.dict(os.environ, {"GISPULSE_TIER": "community"}, clear=False):
            # Remove license key to ensure community tier
            env = os.environ.copy()
            env.pop("GISPULSE_LICENSE_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                scheduler = PipelineScheduler(job_queue=job_queue)
                from gispulse.persistence.tier import TierError
                with pytest.raises(TierError):
                    await scheduler.start()

    @pytest.mark.asyncio
    async def test_add_invalid_cron_raises(self, job_queue):
        scheduler = PipelineScheduler(job_queue=job_queue)
        sp = ScheduledPipeline(name="Bad", cron_expression="invalid cron")
        with pytest.raises(ValueError, match="Invalid cron expression"):
            await scheduler.add(sp)

    @pytest.mark.asyncio
    async def test_disable_via_update(self, job_queue, sample_schedule):
        scheduler = PipelineScheduler(job_queue=job_queue)
        await scheduler.start()
        try:
            await scheduler.add(sample_schedule)
            await scheduler.update(str(sample_schedule.id), enabled=False)

            # Set next_run in the past and tick -- should NOT fire
            schedule = scheduler.get(str(sample_schedule.id))
            schedule.next_run = datetime(2020, 1, 1, tzinfo=timezone.utc)

            await scheduler._tick()
            size = await job_queue.queue_size()
            assert size == 0
        finally:
            await scheduler.stop()
