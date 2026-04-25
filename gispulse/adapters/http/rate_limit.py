"""
Rate limiting configuration for the GISPulse HTTP API.

Uses slowapi with configurable storage backend.  By default, in-memory
storage is used (suitable for single-worker deployments).  For multi-worker
setups (uvicorn --workers=N), set ``GISPULSE_RATE_LIMIT_STORAGE`` to a Redis
URI (e.g. ``redis://localhost:6379``) so all workers share the same buckets.

Authenticated clients (API key) get their own per-key bucket; anonymous
clients are bucketed by IP.
"""

from __future__ import annotations

from starlette.requests import Request

from slowapi import Limiter

from core.config import settings
from core.logging import get_logger

log = get_logger(__name__)


def _key_func(request: Request) -> str:
    """Use API key as rate-limit key when present, otherwise fall back to IP."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"key:{api_key}"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _default_limit() -> str:
    """Default rate limit — applied per key_func bucket.

    Authenticated clients (key:xxx) share this limit per-key.
    Anonymous clients share this limit per-IP.
    300/min is generous enough for desktop clients doing burst operations.
    """
    return "300/minute"


_storage_uri = settings.redis.effective_rate_limit_uri

if _storage_uri == "memory://":
    log.warning(
        "rate_limit_memory_backend",
        detail="Using in-memory rate limiting. "
        "Limits are PER-WORKER — set GISPULSE_RATE_LIMIT_STORAGE=redis://... "
        "for multi-worker deployments.",
    )

limiter = Limiter(
    key_func=_key_func,
    default_limits=[_default_limit()],
    storage_uri=_storage_uri,
)
