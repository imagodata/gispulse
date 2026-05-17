"""Exponential-backoff retry wrapper for SQLite executors.

Why
---
When a GPKG is opened in WAL mode (``journal_mode=WAL`` + a generous
``busy_timeout``), reads do not block writes and vice versa. But SET_FIELD
/ RUN_SQL actions still issue write transactions, and a concurrent QGIS
save (or another GISPulse tick on the same file) can hold the writer
lock long enough to push us past the engine-level ``busy_timeout``.
``sqlite3`` then raises ``OperationalError("database is locked")`` and
the action fails — even though a 100 ms retry would have succeeded.

Scope
-----
The wrapper retries only on transient lock errors (``database is locked``
/ ``database is busy``). Any other ``OperationalError`` (typo in SQL,
missing table, syntax error, …) is re-raised on the first attempt so we
do not mask real bugs by spinning.

Counters
--------
The CLI ``--watch`` mode wants to expose ``sqlite_busy_retries`` per tick
in its structured log. The wrapper keeps a process-local counter
accessible via :meth:`RetryingSqlExecutor.snapshot_retries` so the
caller can ``before/after`` diff per tick without a global.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import time
from typing import Any, Callable

log = logging.getLogger(__name__)


# Default backoff schedule (seconds). 5 attempts total: 100 ms → 250 ms
# → 500 ms → 1 s → 2 s. Cumulative ~3.85 s before final failure, which
# is the right order of magnitude for a QGIS save under typical load.
DEFAULT_BACKOFF_SCHEDULE: tuple[float, ...] = (0.1, 0.25, 0.5, 1.0, 2.0)
DEFAULT_JITTER_PCT: float = 0.2  # ±20 %

# Lower-cased substrings that mark a recoverable lock error. SQLite's
# message text is the public contract here — there is no error code on
# the Python side until ``sqlite3.OperationalError.sqlite_errorcode``
# (3.11+) is universally available, and we ship 3.10 still.
_BUSY_MARKERS: tuple[str, ...] = (
    "database is locked",
    "database is busy",
)


def is_busy_error(exc: BaseException) -> bool:
    """Return True when ``exc`` is a SQLite busy/locked error.

    Centralised so test code and the wrapper agree on the predicate.
    """
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return any(marker in msg for marker in _BUSY_MARKERS)


class RetryingSqlExecutor:
    """Callable wrapper that retries the inner executor on SQLITE_BUSY.

    Wraps any ``(sql, params) -> Any`` callable (typically
    :meth:`GeoPackageEngine.execute` or a user-supplied stub) and
    re-raises immediately on non-busy errors.

    The wrapper is callable so it can be dropped in wherever the
    dispatcher expects ``sql_executor``::

        retrying = RetryingSqlExecutor(engine.execute)
        dispatcher = ActionDispatcher(sql_executor=retrying, ...)

    Args:
        inner:        The wrapped executor.
        backoff:      Tuple of sleep durations (seconds) between attempts.
                      The number of attempts equals ``len(backoff) + 1``
                      (one initial try, then one retry per slot).
        jitter_pct:   Fractional jitter applied to each sleep (uniform
                      distribution in ``[1-pct, 1+pct]``).
        sleeper:      Injection point for tests — defaults to
                      :func:`time.sleep`.
    """

    def __init__(
        self,
        inner: Callable[..., Any],
        *,
        backoff: tuple[float, ...] = DEFAULT_BACKOFF_SCHEDULE,
        jitter_pct: float = DEFAULT_JITTER_PCT,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if inner is None:
            raise ValueError("inner executor cannot be None")
        if not backoff:
            raise ValueError("backoff schedule must not be empty")
        if jitter_pct < 0 or jitter_pct > 1:
            raise ValueError("jitter_pct must be between 0 and 1")

        self._inner = inner
        self._backoff = tuple(backoff)
        self._jitter = float(jitter_pct)
        self._sleep: Callable[[float], None] = sleeper or time.sleep
        # Total number of busy retries that actually fired (i.e. excludes
        # the first successful attempt). Read via :meth:`snapshot_retries`.
        self._retries = 0

    # ------------------------------------------------------------------
    # Counter API
    # ------------------------------------------------------------------

    def snapshot_retries(self) -> int:
        """Return the cumulative retry count since wrapper creation."""
        return self._retries

    def reset_retries(self) -> None:
        """Reset the counter (used by tests; the CLI prefers diff'ing)."""
        self._retries = 0

    # ------------------------------------------------------------------
    # Call site
    # ------------------------------------------------------------------

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Invoke the wrapped executor with retry-on-busy semantics.

        Signature matches ``Callable[..., Any]`` so we transparently
        forward whatever shape the dispatcher uses (positional ``(sql,
        params)`` or keyword variants).
        """
        last_exc: BaseException | None = None
        attempts = len(self._backoff) + 1
        for attempt in range(attempts):
            try:
                return self._inner(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if not is_busy_error(exc):
                    # Permanent error (no such table, syntax, …): fail fast.
                    raise
                last_exc = exc
                if attempt >= len(self._backoff):
                    # Exhausted retry budget — re-raise so the dispatcher's
                    # try/except logs and the change-log row is NOT acked
                    # (handled at the watcher tick level).
                    log.error(
                        "sqlite_busy_retry_exhausted attempts=%d err=%s",
                        attempts,
                        exc,
                    )
                    raise
                # Compute jittered sleep for this slot.
                base = self._backoff[attempt]
                jitter_factor = 1.0 + random.uniform(-self._jitter, self._jitter)
                sleep_for = max(0.0, base * jitter_factor)
                self._retries += 1
                log.warning(
                    "sqlite_busy_retry attempt=%d/%d sleep=%.3fs err=%s",
                    attempt + 1,
                    attempts,
                    sleep_for,
                    exc,
                )
                self._sleep(sleep_for)
        # Defensive: the loop always either returns, raises busy after
        # exhaustion, or raises a non-busy OperationalError. Reaching
        # here would mean a logic error in the loop above.
        if last_exc is not None:  # pragma: no cover - unreachable
            raise last_exc
        raise RuntimeError("RetryingSqlExecutor: unreachable")  # pragma: no cover


__all__ = [
    "DEFAULT_BACKOFF_SCHEDULE",
    "DEFAULT_JITTER_PCT",
    "RetryingSqlExecutor",
    "is_busy_error",
]
