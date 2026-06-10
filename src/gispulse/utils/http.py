"""Shared HTTP helpers for public data clients.

``httpx`` is an optional dependency (included in the ``api`` and ``dev``
extras). The import is deferred to the call site so that importing this module
in a core-only environment does not fail.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    import httpx


class DataClientError(RuntimeError):
    """Raised when a public data service fails in a way the caller can recover from."""


def json_get(
    client: "httpx.Client",
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Issue a GET request and return the parsed JSON body as a dict.

    Raises :exc:`DataClientError` on non-JSON responses or when the body is
    not a JSON object.  HTTP errors are raised by httpx's ``raise_for_status``
    before parsing — callers that want to recover from 4xx/5xx should catch
    ``httpx.HTTPStatusError`` upstream.

    Args:
        client: An ``httpx.Client`` instance (or any duck-typed equivalent).
        url: Absolute URL to fetch.
        params: Optional query parameters forwarded to ``client.get``.
        timeout: Per-request timeout in seconds.

    Returns:
        Parsed JSON object.

    Raises:
        DataClientError: Response body is not valid JSON or is not a JSON object.
    """
    response = client.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        content_type = response.headers.get("content-type", "unknown").split(";")[0]
        raise DataClientError(
            f"Non-JSON response (HTTP {response.status_code}, {content_type})"
        ) from exc
    if not isinstance(payload, dict):
        raise DataClientError(f"Unexpected non-object response from {url}")
    return payload
