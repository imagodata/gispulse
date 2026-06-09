"""HTTP streaming download helpers for fetchers."""

from __future__ import annotations

import os
import time
from os import PathLike

from gispulse.core.logging import get_logger

log = get_logger(__name__)

_DEFAULT_READ_TIMEOUT_SECONDS = 120.0
_HTTP_RETRY_BACKOFFS_SECONDS = (2.0, 5.0)
_HTTP_MAX_ATTEMPTS = len(_HTTP_RETRY_BACKOFFS_SECONDS) + 1


def _read_timeout_seconds() -> float:
    raw = os.getenv("GISPULSE_HTTP_READ_TIMEOUT")
    if raw is None:
        return _DEFAULT_READ_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        log.warning(
            "http_download_invalid_read_timeout",
            value=raw,
            default_seconds=_DEFAULT_READ_TIMEOUT_SECONDS,
        )
        return _DEFAULT_READ_TIMEOUT_SECONDS
    if value <= 0:
        log.warning(
            "http_download_invalid_read_timeout",
            value=raw,
            default_seconds=_DEFAULT_READ_TIMEOUT_SECONDS,
        )
        return _DEFAULT_READ_TIMEOUT_SECONDS
    return value


def _http_timeout():
    import httpx

    return httpx.Timeout(
        connect=10.0,
        read=_read_timeout_seconds(),
        write=30.0,
        pool=10.0,
    )


def _is_retriable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _log_retry(
    event: str,
    *,
    endpoint: str,
    attempt: int,
    backoff_seconds: float,
    error: BaseException,
    status_code: int | None = None,
) -> None:
    log.warning(
        event,
        endpoint=endpoint,
        attempt=attempt,
        max_attempts=_HTTP_MAX_ATTEMPTS,
        next_attempt=attempt + 1,
        backoff_seconds=backoff_seconds,
        error_type=type(error).__name__,
        error=str(error),
        status_code=status_code,
    )


def stream_http_to_file(
    endpoint: str,
    local_path: str | PathLike[str],
    *,
    retry_log_event: str,
) -> None:
    """Stream an HTTP(S) response to disk with bounded retries."""
    import httpx

    for attempt in range(1, _HTTP_MAX_ATTEMPTS + 1):
        try:
            with httpx.stream(
                "GET",
                endpoint,
                follow_redirects=True,
                timeout=_http_timeout(),
            ) as resp:
                resp.raise_for_status()
                with open(local_path, "wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
            return
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if attempt >= _HTTP_MAX_ATTEMPTS or not _is_retriable_status(status_code):
                raise
            backoff_seconds = _HTTP_RETRY_BACKOFFS_SECONDS[attempt - 1]
            _log_retry(
                retry_log_event,
                endpoint=endpoint,
                attempt=attempt,
                backoff_seconds=backoff_seconds,
                error=exc,
                status_code=status_code,
            )
            time.sleep(backoff_seconds)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt >= _HTTP_MAX_ATTEMPTS:
                raise
            backoff_seconds = _HTTP_RETRY_BACKOFFS_SECONDS[attempt - 1]
            _log_retry(
                retry_log_event,
                endpoint=endpoint,
                attempt=attempt,
                backoff_seconds=backoff_seconds,
                error=exc,
            )
            time.sleep(backoff_seconds)
