"""Tests for orchestration.metering — InMemoryMetering + factory.

Redis backend is exercised via test_job_queue.py when available; here we
focus on the in-memory path (dev/test default) + the ``create_metering``
factory dispatch.
"""
from __future__ import annotations

import pytest

from orchestration.metering import InMemoryMetering, create_metering


@pytest.mark.asyncio
class TestInMemoryMeteringRecord:
    async def test_record_single_job(self):
        m = InMemoryMetering()
        await m.record_job("key-a", duration_seconds=2.5)
        usage = await m.get_usage("key-a")
        assert usage == {"jobs_count": 1, "compute_seconds": 2.5}

    async def test_record_multiple_jobs_accumulates(self):
        m = InMemoryMetering()
        await m.record_job("key-a", 1.5)
        await m.record_job("key-a", 2.5)
        await m.record_job("key-a", 0.5)
        usage = await m.get_usage("key-a")
        assert usage["jobs_count"] == 3
        assert usage["compute_seconds"] == 4.5

    async def test_record_different_keys_isolated(self):
        m = InMemoryMetering()
        await m.record_job("key-a", 1.0)
        await m.record_job("key-b", 2.0)
        await m.record_job("key-b", 3.0)
        a = await m.get_usage("key-a")
        b = await m.get_usage("key-b")
        assert a == {"jobs_count": 1, "compute_seconds": 1.0}
        assert b == {"jobs_count": 2, "compute_seconds": 5.0}


@pytest.mark.asyncio
class TestInMemoryMeteringRead:
    async def test_get_usage_unknown_key_returns_zero(self):
        m = InMemoryMetering()
        usage = await m.get_usage("never-seen")
        assert usage == {"jobs_count": 0, "compute_seconds": 0.0}

    async def test_get_all_usage_empty(self):
        m = InMemoryMetering()
        assert await m.get_all_usage() == {}

    async def test_get_all_usage_returns_every_key(self):
        m = InMemoryMetering()
        await m.record_job("k1", 1.0)
        await m.record_job("k2", 2.0)
        await m.record_job("k3", 3.0)
        all_usage = await m.get_all_usage()
        assert set(all_usage.keys()) == {"k1", "k2", "k3"}
        assert all_usage["k2"]["compute_seconds"] == 2.0

    async def test_compute_seconds_rounded_to_3_decimals(self):
        m = InMemoryMetering()
        await m.record_job("k", 1.2345678)
        usage = await m.get_usage("k")
        assert usage["compute_seconds"] == 1.235

    async def test_close_is_noop(self):
        m = InMemoryMetering()
        await m.record_job("k", 1.0)
        await m.close()
        # Data survives close() for in-memory backend (close just releases resources)
        usage = await m.get_usage("k")
        assert usage["jobs_count"] == 1


@pytest.mark.asyncio
class TestInMemoryMeteringConcurrency:
    async def test_concurrent_writes_do_not_lose_counts(self):
        """asyncio.Lock guarantees record_job is atomic under concurrent tasks."""
        import asyncio

        m = InMemoryMetering()

        async def hit_n(n: int) -> None:
            for _ in range(n):
                await m.record_job("concurrent", 0.01)

        # 10 tasks × 20 records each = 200 records expected
        await asyncio.gather(*(hit_n(20) for _ in range(10)))
        usage = await m.get_usage("concurrent")
        assert usage["jobs_count"] == 200
        assert round(usage["compute_seconds"], 2) == 2.0


class TestFactory:
    def test_create_metering_returns_in_memory_without_redis(self, monkeypatch):
        from core.config import settings

        monkeypatch.setattr(settings.redis, "url", "")
        m = create_metering()
        assert isinstance(m, InMemoryMetering)

    def test_redis_metering_can_be_instantiated(self):
        """Direct construction smoke test — avoids the factory + Pydantic
        settings mutation issue. RedisMetering.from_url() is lazy so no
        connection is opened synchronously."""
        try:
            from orchestration.metering import RedisMetering
            m = RedisMetering(redis_url="redis://localhost:6379")
        except ImportError:
            pytest.skip("redis package not installed")
        assert m._prefix == "gispulse:metering"
        assert m._keys_set == "gispulse:metering:_keys"
