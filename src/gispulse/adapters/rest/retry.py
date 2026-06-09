"""Shared retry/backoff helpers for REST adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import time
from typing import Any

from gispulse.core.logging import get_logger

log = get_logger(__name__)

_DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetrySpec:
    """Retry policy compiled from ``AccessSpec.params["retry"]``."""

    max_attempts: int = 1
    backoff_seconds: float = 0.0
    backoff_factor: float = 2.0
    statuses: frozenset[int] = field(default_factory=lambda: _DEFAULT_RETRY_STATUSES)

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> RetrySpec:
        retry = dict(params.get("retry") or {})
        if not retry:
            return cls()
        max_attempts = retry.get("max_attempts")
        if max_attempts is None and retry.get("max_retries") is not None:
            max_attempts = int(retry["max_retries"]) + 1
        if max_attempts is None:
            max_attempts = 1
        statuses = retry.get("statuses", _DEFAULT_RETRY_STATUSES)
        return cls(
            max_attempts=max(1, int(max_attempts)),
            backoff_seconds=max(0.0, float(retry.get("backoff_seconds", 0.0))),
            backoff_factor=max(1.0, float(retry.get("backoff_factor", 2.0))),
            statuses=frozenset(int(status) for status in statuses),
        )


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def _retry_after_seconds(headers: Any) -> float | None:
    value = headers.get("Retry-After") if hasattr(headers, "get") else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        retry_at = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - datetime.now(tz=UTC)).total_seconds())


def _retry_delay_seconds(
    exc: BaseException,
    retry: RetrySpec,
    *,
    attempt: int,
) -> float:
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = _retry_after_seconds(exc.response.headers)
        if retry_after is not None:
            return retry_after
    return retry.backoff_seconds * (retry.backoff_factor ** max(0, attempt - 1))


def get_json_with_retry(
    fetch_json: Callable[[str, float], dict[str, Any]],
    url: str,
    timeout: float,
    retry: RetrySpec,
    *,
    empty_statuses: frozenset[Any] = frozenset(),
    retry_event: str = "rest_fetch_retry",
    sleep_fn: Callable[[float], None] = sleep,
) -> dict[str, Any]:
    """GET JSON through ``fetch_json`` with retry on transient HTTP/transport failures."""
    import httpx

    attempt = 1
    while True:
        try:
            return fetch_json(url, timeout)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if (
                status_code in empty_statuses
                or status_code not in retry.statuses
                or attempt >= retry.max_attempts
            ):
                raise
            delay = _retry_delay_seconds(exc, retry, attempt=attempt)
        except httpx.TransportError as exc:
            if attempt >= retry.max_attempts:
                raise
            delay = _retry_delay_seconds(exc, retry, attempt=attempt)
        log.warning(
            retry_event,
            url=url,
            attempt=attempt,
            max_attempts=retry.max_attempts,
            sleep_seconds=delay,
        )
        sleep_fn(delay)
        attempt += 1
