"""Bounded HTTP download helper shared by file fetchers."""

from __future__ import annotations

from os import PathLike
import time
from typing import Any

DEFAULT_DOWNLOAD_TIMEOUT_S = 120.0
DEFAULT_DOWNLOAD_MAX_RETRIES = 2
DEFAULT_DOWNLOAD_RETRY_BACKOFF_S = 0.5

__all__ = [
    "DEFAULT_DOWNLOAD_MAX_RETRIES",
    "DEFAULT_DOWNLOAD_RETRY_BACKOFF_S",
    "DEFAULT_DOWNLOAD_TIMEOUT_S",
    "stream_http_download",
]


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code <= 599


def _download_error(endpoint: str, attempts: int, exc: BaseException) -> RuntimeError:
    return RuntimeError(
        f"HTTP download failed after {attempts} attempts for {endpoint!r}: {exc}"
    )


def stream_http_download(
    endpoint: str,
    local_path: str | PathLike[str],
    *,
    timeout: float = DEFAULT_DOWNLOAD_TIMEOUT_S,
    max_retries: int = DEFAULT_DOWNLOAD_MAX_RETRIES,
    retry_backoff: float = DEFAULT_DOWNLOAD_RETRY_BACKOFF_S,
) -> None:
    """Stream ``endpoint`` to ``local_path`` with a bounded retry policy."""
    import httpx

    if timeout <= 0:
        raise ValueError("download timeout must be > 0")
    if max_retries < 0:
        raise ValueError("download max_retries must be >= 0")
    if retry_backoff < 0:
        raise ValueError("download retry_backoff must be >= 0")

    attempts = max_retries + 1
    for attempt in range(attempts):
        try:
            with httpx.stream(
                "GET",
                endpoint,
                follow_redirects=True,
                timeout=timeout,
            ) as resp:
                resp.raise_for_status()
                with open(local_path, "wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
            return
        except httpx.HTTPStatusError as exc:
            if not _is_retryable_status(exc.response.status_code):
                raise
            if attempt >= max_retries:
                raise _download_error(endpoint, attempts, exc) from exc
        except httpx.TransportError as exc:
            if attempt >= max_retries:
                raise _download_error(endpoint, attempts, exc) from exc

        if retry_backoff:
            time.sleep(retry_backoff * (attempt + 1))

    raise AssertionError("unreachable HTTP download retry state")


def download_options(params: dict[str, Any]) -> dict[str, float | int]:
    """Read common download controls from an ``AccessSpec.params`` mapping."""
    return {
        "timeout": float(params.get("timeout", DEFAULT_DOWNLOAD_TIMEOUT_S)),
        "max_retries": int(
            params.get("max_retries", DEFAULT_DOWNLOAD_MAX_RETRIES)
        ),
        "retry_backoff": float(
            params.get("retry_backoff", DEFAULT_DOWNLOAD_RETRY_BACKOFF_S)
        ),
    }
