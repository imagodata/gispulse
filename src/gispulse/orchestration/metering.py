"""
Basic job metering for GISPulse Pro.

Tracks per-API-key usage:
- Number of jobs executed
- Total compute time (seconds)

Storage backends:
- Redis (HINCRBY / HINCRBYFLOAT) when GISPULSE_REDIS_URL is set.
- In-memory dict otherwise (dev/test).

Usage::

    meter = create_metering()
    await meter.record_job("api-key-123", duration_seconds=4.2)
    usage = await meter.get_usage("api-key-123")
    # -> {"jobs_count": 1, "compute_seconds": 4.2}
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from gispulse.core.config import settings
from gispulse.core.logging import get_logger

log = get_logger(__name__)


class Metering(ABC):
    """Abstract metering interface."""

    @abstractmethod
    async def record_job(self, api_key: str, duration_seconds: float) -> None:
        """Record a completed job for the given API key."""
        ...

    @abstractmethod
    async def get_usage(self, api_key: str) -> dict[str, Any]:
        """Return usage stats for an API key."""
        ...

    @abstractmethod
    async def get_all_usage(self) -> dict[str, dict[str, Any]]:
        """Return usage stats for all known API keys."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release resources."""
        ...


class InMemoryMetering(Metering):
    """Dict-backed metering for dev/test.  Thread-safe via asyncio.Lock."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def record_job(self, api_key: str, duration_seconds: float) -> None:
        async with self._lock:
            if api_key not in self._data:
                self._data[api_key] = {"jobs_count": 0, "compute_seconds": 0.0}
            self._data[api_key]["jobs_count"] += 1
            self._data[api_key]["compute_seconds"] += duration_seconds

    async def get_usage(self, api_key: str) -> dict[str, Any]:
        entry = self._data.get(api_key, {"jobs_count": 0, "compute_seconds": 0.0})
        return {
            "jobs_count": int(entry["jobs_count"]),
            "compute_seconds": round(entry["compute_seconds"], 3),
        }

    async def get_all_usage(self) -> dict[str, dict[str, Any]]:
        return {
            key: {
                "jobs_count": int(v["jobs_count"]),
                "compute_seconds": round(v["compute_seconds"], 3),
            }
            for key, v in self._data.items()
        }

    async def close(self) -> None:
        pass


class RedisMetering(Metering):
    """Redis-backed metering using HINCRBY/HINCRBYFLOAT.

    Key layout::

        gispulse:metering:{api_key}  -- HASH
            jobs_count      (int)
            compute_seconds (float)
        gispulse:metering:_keys      -- SET of known api_keys
    """

    def __init__(self, redis_url: str, *, prefix: str = "gispulse:metering") -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._prefix = prefix
        self._keys_set = f"{prefix}:_keys"

    def _key(self, api_key: str) -> str:
        return f"{self._prefix}:{api_key}"

    async def record_job(self, api_key: str, duration_seconds: float) -> None:
        pipe = self._redis.pipeline()
        pipe.hincrby(self._key(api_key), "jobs_count", 1)
        pipe.hincrbyfloat(self._key(api_key), "compute_seconds", duration_seconds)
        pipe.sadd(self._keys_set, api_key)
        await pipe.execute()

    async def get_usage(self, api_key: str) -> dict[str, Any]:
        data = await self._redis.hgetall(self._key(api_key))
        return {
            "jobs_count": int(data.get("jobs_count", 0)),
            "compute_seconds": round(float(data.get("compute_seconds", 0.0)), 3),
        }

    async def get_all_usage(self) -> dict[str, dict[str, Any]]:
        keys = await self._redis.smembers(self._keys_set)
        result = {}
        for api_key in keys:
            result[api_key] = await self.get_usage(api_key)
        return result

    async def close(self) -> None:
        await self._redis.aclose()


def create_metering() -> Metering:
    """Create the appropriate Metering backend based on environment."""
    redis_url = settings.redis.url.strip()
    if redis_url:
        log.info("metering_redis")
        return RedisMetering(redis_url)
    log.info("metering_in_memory")
    return InMemoryMetering()
