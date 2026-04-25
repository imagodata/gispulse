"""
Unit tests for WorkerPool — adapters/esb/pool.py

All tests are async (pytest-asyncio). No real PostGIS connection is required;
mock workers are used throughout.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from gispulse.adapters.esb.enums import WorkerType
from gispulse.adapters.esb.pool import WorkerInfo, WorkerPool, WorkerPoolConfig
from gispulse.adapters.esb.workers.base_worker import BaseWorker


# ---------------------------------------------------------------------------
# Helpers / mock workers
# ---------------------------------------------------------------------------


class SlowNoOpWorker(BaseWorker):
    """Worker that sleeps briefly per batch (simulates real work)."""

    worker_type = WorkerType.IDENTIFY

    def __init__(self, batch_sleep: float = 0.01, **kwargs):
        super().__init__(**kwargs)
        self._batch_sleep = batch_sleep

    async def run_batch(self) -> int:
        await asyncio.sleep(self._batch_sleep)
        return 0


class CountingWorker(BaseWorker):
    """Worker that counts processed messages (injectable counter)."""

    worker_type = WorkerType.PROCESSING

    async def run_batch(self) -> int:
        await asyncio.sleep(0.005)
        return 1


def make_config(**overrides) -> WorkerPoolConfig:
    """Build a WorkerPoolConfig with sensible test defaults."""
    defaults = dict(
        min_workers=1,
        max_workers=4,
        scale_up_threshold=100,
        scale_down_threshold=10,
        health_check_interval=9999.0,  # Disable automatic health checks in tests
    )
    defaults.update(overrides)
    return WorkerPoolConfig(**defaults)


def slow_worker_factory(worker_id: str, db_pool=None) -> SlowNoOpWorker:
    return SlowNoOpWorker(db_pool=db_pool)


def counting_worker_factory(worker_id: str, db_pool=None) -> CountingWorker:
    return CountingWorker(db_pool=db_pool)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pool():
    """Minimal pool (1 min, 4 max) with a slow no-op worker factory."""
    cfg = make_config(worker_factory=slow_worker_factory)
    p = WorkerPool(config=cfg)
    yield p
    # Ensure cleanup even if the test failed
    if p._running:
        await p.stop(graceful=False, timeout=2.0)


@pytest_asyncio.fixture
async def started_pool():
    """Pool already started with 1 worker."""
    cfg = make_config(min_workers=1, max_workers=4, worker_factory=slow_worker_factory)
    p = WorkerPool(config=cfg)
    await p.start()
    yield p
    if p._running:
        await p.stop(graceful=False, timeout=2.0)


# ---------------------------------------------------------------------------
# 1. Start / stop lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_min_workers(pool):
    """start() should spawn exactly min_workers workers."""
    pool.config.min_workers = 2
    await pool.start()

    assert pool._running is True
    assert len(pool._workers) == 2
    assert pool.active_count == 2

    await pool.stop(graceful=False, timeout=2.0)


@pytest.mark.asyncio
async def test_start_is_idempotent(pool):
    """Calling start() twice must not spawn duplicate workers."""
    await pool.start()
    initial_count = len(pool._workers)
    await pool.start()  # Second call — should be a no-op

    assert len(pool._workers) == initial_count

    await pool.stop(graceful=False, timeout=2.0)


@pytest.mark.asyncio
async def test_stop_clears_all_workers(started_pool):
    """stop() must remove all workers from internal registries."""
    await started_pool.stop(graceful=False, timeout=2.0)

    assert started_pool._running is False
    assert len(started_pool._workers) == 0
    assert len(started_pool._tasks) == 0


@pytest.mark.asyncio
async def test_stop_is_idempotent(started_pool):
    """Calling stop() twice must not raise."""
    await started_pool.stop(graceful=False, timeout=2.0)
    await started_pool.stop(graceful=False, timeout=2.0)  # Should be a no-op

    assert started_pool._running is False


@pytest.mark.asyncio
async def test_graceful_stop_waits_for_tasks(pool):
    """
    Graceful stop should wait for workers to finish their current batch
    before removing them.
    """
    pool.config.min_workers = 2
    pool.config.worker_factory = lambda wid, db_pool: SlowNoOpWorker(
        batch_sleep=0.05, db_pool=db_pool
    )
    await pool.start()

    # Let workers run briefly
    await asyncio.sleep(0.02)

    # Graceful stop — must complete without raising
    await pool.stop(graceful=True, timeout=5.0)

    assert pool._running is False
    assert len(pool._workers) == 0


# ---------------------------------------------------------------------------
# 2. scale_up / scale_down — boundary conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scale_up_adds_workers(started_pool):
    """scale_up(2) should add 2 workers to the pool."""
    initial = len(started_pool._workers)
    created = await started_pool.scale_up(2)

    assert len(created) == 2
    assert len(started_pool._workers) == initial + 2


@pytest.mark.asyncio
async def test_scale_up_respects_max_workers(started_pool):
    """scale_up() must not exceed max_workers."""
    started_pool.config.max_workers = 3
    # Pool already has 1 worker → at most 2 more allowed
    created = await started_pool.scale_up(10)

    assert len(created) == 2
    assert len(started_pool._workers) == 3


@pytest.mark.asyncio
async def test_scale_up_at_max_returns_empty(started_pool):
    """scale_up() when already at max_workers should return []."""
    # Fill pool to max
    await started_pool.scale_up(started_pool.config.max_workers)
    assert len(started_pool._workers) == started_pool.config.max_workers

    created = await started_pool.scale_up(1)

    assert created == []
    assert len(started_pool._workers) == started_pool.config.max_workers


@pytest.mark.asyncio
async def test_scale_down_removes_workers(pool):
    """scale_down(1) should remove one worker."""
    pool.config.min_workers = 1
    pool.config.max_workers = 3
    await pool.start()
    await pool.scale_up(2)  # Now 3 workers total

    removed = await pool.scale_down(1)

    assert len(removed) == 1
    assert len(pool._workers) == 2

    await pool.stop(graceful=False, timeout=2.0)


@pytest.mark.asyncio
async def test_scale_down_respects_min_workers(pool):
    """scale_down() must not go below min_workers."""
    pool.config.min_workers = 2
    pool.config.max_workers = 4
    await pool.start()
    await pool.scale_up(2)  # Now 3 workers (start spawned 2 already)
    # We actually have min_workers=2 so start spawns 2; then +2 = 4 total
    total_before = len(pool._workers)

    # Try to remove all — should leave min_workers
    removed = await pool.scale_down(total_before)

    assert len(removed) == total_before - pool.config.min_workers
    assert len(pool._workers) == pool.config.min_workers

    await pool.stop(graceful=False, timeout=2.0)


@pytest.mark.asyncio
async def test_scale_down_at_min_returns_empty(started_pool):
    """scale_down() when already at min_workers should return []."""
    # started_pool has min=1 and currently 1 worker
    assert len(started_pool._workers) == started_pool.config.min_workers

    removed = await started_pool.scale_down(1)

    assert removed == []
    assert len(started_pool._workers) == started_pool.config.min_workers


# ---------------------------------------------------------------------------
# 3. get_pool_status / get_worker_info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pool_status_structure(started_pool):
    """get_pool_status() must return the expected keys and values."""
    status = started_pool.get_pool_status()

    assert status["running"] is True
    assert status["total_workers"] == 1
    assert status["active_count"] == 1
    assert status["min_workers"] == started_pool.config.min_workers
    assert status["max_workers"] == started_pool.config.max_workers
    assert isinstance(status["workers"], dict)
    assert len(status["workers"]) == 1


@pytest.mark.asyncio
async def test_get_pool_status_worker_fields(started_pool):
    """Each worker entry in get_pool_status() must expose required fields."""
    status = started_pool.get_pool_status()
    worker_entry = next(iter(status["workers"].values()))

    for key in ("worker_id", "worker_type", "status", "started_at", "messages_processed"):
        assert key in worker_entry, f"Missing key: {key}"

    assert worker_entry["status"] == "running"
    assert isinstance(worker_entry["started_at"], str)  # ISO-8601 string


@pytest.mark.asyncio
async def test_get_worker_info_existing(started_pool):
    """get_worker_info() should return a WorkerInfo for a known ID."""
    worker_id = next(iter(started_pool._infos.keys()))
    info = started_pool.get_worker_info(worker_id)

    assert isinstance(info, WorkerInfo)
    assert info.worker_id == worker_id
    assert info.status == "running"


@pytest.mark.asyncio
async def test_get_worker_info_unknown(started_pool):
    """get_worker_info() must return None for an unknown ID."""
    result = started_pool.get_worker_info("non-existent-id")
    assert result is None


@pytest.mark.asyncio
async def test_active_count_after_scale(started_pool):
    """active_count must track the number of running workers."""
    assert started_pool.active_count == 1

    await started_pool.scale_up(2)
    assert started_pool.active_count == 3

    await started_pool.scale_down(1)
    assert started_pool.active_count == 2


# ---------------------------------------------------------------------------
# 4. auto_scale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_scale_scale_up_when_queue_above_threshold(started_pool):
    """_auto_scale should add a worker when queue_size >= scale_up_threshold."""
    started_pool.config.scale_up_threshold = 100
    before = len(started_pool._workers)

    await started_pool._auto_scale(queue_size=150)

    assert len(started_pool._workers) == before + 1


@pytest.mark.asyncio
async def test_auto_scale_scale_up_at_exact_threshold(started_pool):
    """_auto_scale should trigger scale_up at exactly scale_up_threshold."""
    started_pool.config.scale_up_threshold = 100
    before = len(started_pool._workers)

    await started_pool._auto_scale(queue_size=100)

    assert len(started_pool._workers) == before + 1


@pytest.mark.asyncio
async def test_auto_scale_scale_down_when_queue_below_threshold(pool):
    """_auto_scale should remove a worker when queue_size <= scale_down_threshold."""
    pool.config.min_workers = 1
    pool.config.max_workers = 4
    pool.config.scale_down_threshold = 10
    await pool.start()
    await pool.scale_up(2)  # 3 workers total
    before = len(pool._workers)

    await pool._auto_scale(queue_size=5)

    assert len(pool._workers) == before - 1

    await pool.stop(graceful=False, timeout=2.0)


@pytest.mark.asyncio
async def test_auto_scale_scale_down_at_exact_threshold(pool):
    """_auto_scale should trigger scale_down at exactly scale_down_threshold."""
    pool.config.min_workers = 1
    pool.config.max_workers = 4
    pool.config.scale_down_threshold = 10
    await pool.start()
    await pool.scale_up(2)  # 3 workers total
    before = len(pool._workers)

    await pool._auto_scale(queue_size=10)

    assert len(pool._workers) == before - 1

    await pool.stop(graceful=False, timeout=2.0)


@pytest.mark.asyncio
async def test_auto_scale_no_change_in_middle_range(started_pool):
    """_auto_scale should not change pool size when queue is between thresholds."""
    started_pool.config.scale_up_threshold = 100
    started_pool.config.scale_down_threshold = 10
    before = len(started_pool._workers)

    await started_pool._auto_scale(queue_size=50)

    assert len(started_pool._workers) == before


@pytest.mark.asyncio
async def test_auto_scale_respects_max_workers(pool):
    """_auto_scale scale_up must still respect max_workers."""
    pool.config.min_workers = 1
    pool.config.max_workers = 2
    pool.config.scale_up_threshold = 100
    await pool.start()
    await pool.scale_up(1)  # Now at max (2)

    await pool._auto_scale(queue_size=200)

    assert len(pool._workers) == 2  # Still capped at max

    await pool.stop(graceful=False, timeout=2.0)


@pytest.mark.asyncio
async def test_auto_scale_respects_min_workers(started_pool):
    """_auto_scale scale_down must still respect min_workers."""
    started_pool.config.scale_down_threshold = 10
    # Pool has 1 worker == min_workers → cannot go lower

    await started_pool._auto_scale(queue_size=0)

    assert len(started_pool._workers) == started_pool.config.min_workers


# ---------------------------------------------------------------------------
# 5. Graceful stop waits for tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_stop_completes_without_cancellation_error():
    """
    Graceful stop should allow workers to complete their current iteration
    and must not propagate CancelledError to the caller.
    """
    cfg = make_config(
        min_workers=2,
        max_workers=4,
        worker_factory=lambda wid, db_pool: SlowNoOpWorker(batch_sleep=0.02, db_pool=db_pool),
    )
    pool = WorkerPool(config=cfg)
    await pool.start()
    await asyncio.sleep(0.01)

    # Must not raise
    await pool.stop(graceful=True, timeout=5.0)

    assert pool._running is False
    assert len(pool._workers) == 0


@pytest.mark.asyncio
async def test_stop_timeout_cancels_stuck_worker():
    """
    If a worker exceeds the stop timeout, the pool should cancel it
    instead of hanging forever.
    """

    class StuckWorker(BaseWorker):
        worker_type = WorkerType.IDENTIFY

        async def run_batch(self) -> int:
            await asyncio.sleep(9999)
            return 0

    cfg = make_config(
        min_workers=1,
        max_workers=2,
        worker_factory=lambda wid, db_pool: StuckWorker(db_pool=db_pool),
    )
    pool = WorkerPool(config=cfg)
    await pool.start()

    # Short timeout — worker should be cancelled, not waited for
    await pool.stop(graceful=True, timeout=0.2)

    assert pool._running is False


# ---------------------------------------------------------------------------
# 6. WorkerInfo dataclass
# ---------------------------------------------------------------------------


def test_worker_info_defaults():
    """WorkerInfo should initialise with sensible defaults."""
    now = datetime.now(timezone.utc)
    info = WorkerInfo(
        worker_id="abc",
        worker_type=WorkerType.IDENTIFY.value,
        status="running",
        started_at=now,
    )

    assert info.messages_processed == 0
    assert info.last_heartbeat is None
    assert info.worker_id == "abc"
    assert info.status == "running"
