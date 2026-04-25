"""
Factory for creating the appropriate JobQueue implementation.

Selects RedisJobQueue when ``GISPULSE_REDIS_URL`` is set, otherwise
falls back to InMemoryJobQueue (retro-compatible with Mode A/B).

Usage::

    queue = create_job_queue()
    # -> RedisJobQueue if GISPULSE_REDIS_URL is set
    # -> InMemoryJobQueue otherwise
"""

from __future__ import annotations

from core.config import settings
from core.logging import get_logger
from orchestration.job_queue import InMemoryJobQueue, JobQueue, RedisJobQueue

log = get_logger(__name__)


def create_job_queue() -> JobQueue:
    """Instantiate the appropriate JobQueue based on environment.

    Returns:
        ``RedisJobQueue`` when ``GISPULSE_REDIS_URL`` is set and non-empty,
        ``InMemoryJobQueue`` otherwise.
    """
    redis_url = settings.redis.url.strip()
    if redis_url:
        log.info("job_queue_redis", redis_url=redis_url.split("@")[-1])  # mask credentials
        return RedisJobQueue(redis_url)
    log.info("job_queue_in_memory")
    return InMemoryJobQueue()
