"""Exception classification helpers for public data source calls.

:data:`ErrorContext` labels the nature of a failure so that upstream
error-handling logic can decide whether to retry, degrade gracefully,
or surface the gap to the operator.

:func:`classify_exception` is the canonical mapping used at every call site
that wraps an external HTTP or data-parsing operation.
"""

from __future__ import annotations

import json
from typing import Literal

#: Category of a data-source failure.
#:
#: * ``network`` — transport-level failures: timeouts, DNS, 5xx, connection reset.
#: * ``parse`` — payload received but not parseable or of unexpected shape.
#: * ``config`` — 4xx (auth, malformed request) or initialisation gaps
#:   (missing env var, unknown identifier, …).
#: * ``missing_data`` — 200 OK but the source returned nothing actionable.
#: * ``truncation`` — caller signalled that the maximum feature count was
#:   reached and the result set is incomplete.
ErrorContext = Literal["network", "parse", "missing_data", "config", "truncation"]

_MAX_CAUSE_DEPTH = 5


def _classify_single(exc: BaseException) -> ErrorContext | None:
    """Classify one exception without walking the cause chain.

    Returns ``None`` when the exception cannot be confidently categorised,
    which lets the caller fall through to the next cause in the chain.
    """
    # Import lazily so the module loads in environments without httpx.
    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return "network"
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if 500 <= code < 600:
                return "network"
            if 400 <= code < 500:
                return "config"
            return "network"
        if isinstance(exc, httpx.HTTPError):
            # ConnectError, ReadError, RemoteProtocolError, etc.
            return "network"
    except ImportError:
        pass

    # Import here to avoid a circular dependency: http.py → errors.py only
    # if errors.py does not import http.py at module level.
    from gispulse.utils.http import DataClientError

    if isinstance(exc, DataClientError):
        # DataClientError wraps HTTP-level failures that reached the parsing
        # stage — always a parse signal, regardless of the original cause.
        return "parse"
    if isinstance(exc, json.JSONDecodeError):
        return "parse"
    if isinstance(exc, RuntimeError):
        # Misconfigured catalogue entries or missing environment variables
        # raise RuntimeError — treat as a config gap, not a network outage.
        return "config"
    if isinstance(exc, (KeyError, TypeError, ValueError)):
        # Payload arrived but we could not extract the expected field.
        # Surfaces as parse rather than missing_data so it appears in
        # operator dashboards.
        return "parse"
    return None


def classify_exception(exc: BaseException) -> ErrorContext:
    """Map a Python exception to its :data:`ErrorContext` category.

    The mapping walks the ``__cause__`` / ``__context__`` chain (up to
    :data:`_MAX_CAUSE_DEPTH` levels) and returns the classification of the
    *most specific* exception found, preferring causes closer to the root.
    This ensures that a :class:`~gispulse.utils.http.DataClientError` raised
    *from* a ``json.JSONDecodeError`` is correctly classified as ``"parse"``
    rather than falling back to ``"config"`` (the ``RuntimeError`` branch).

    The mapping is intentionally conservative: anything that cannot be
    confidently categorised falls back to ``missing_data``, the softest
    signal.

    Args:
        exc: The exception to classify.

    Returns:
        An :data:`ErrorContext` literal.
    """
    # Collect the chain: exc → __cause__ → __cause__.__cause__ → …
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and len(chain) < _MAX_CAUSE_DEPTH:
        eid = id(current)
        if eid in seen:
            break
        seen.add(eid)
        chain.append(current)
        # Prefer explicit chaining (__cause__) over implicit (__context__).
        current = current.__cause__ or current.__context__

    # Classify from the outermost exception inward; the first confident hit
    # wins.  This means a specific cause overrides a generic wrapper only
    # when the wrapper itself has no confident classification (returns None).
    for candidate in chain:
        result = _classify_single(candidate)
        if result is not None:
            return result

    return "missing_data"
