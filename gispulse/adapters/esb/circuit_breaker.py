"""
EXPERIMENTAL — Circuit Breaker for distributed ESB deployments.

Not imported at startup. Becomes relevant with multi-worker Redis queue
or distributed pipeline execution. Use lazy import when needed.

Circuit Breaker — Protection against cascading failures in GISPulse ESB.

Pattern: CLOSED → OPEN → HALF_OPEN → CLOSED

- CLOSED   : Normal, calls pass through.
- OPEN     : Blocked after ``failure_threshold`` consecutive failures.
             Calls fail fast (CircuitBreakerOpenError).
- HALF_OPEN: After ``timeout`` seconds in OPEN, one probe call is allowed.
             Success → CLOSED. Failure → back to OPEN.

Usage::

    cb = get_circuit_breaker("postgis", failure_threshold=5, timeout=30.0)
    try:
        result = await cb.execute(fetch_layer, layer_id=layer_id)
    except CircuitBreakerOpenError:
        log.warning("PostGIS unavailable, skipping")

Adapted from Forge ESB reference (resilience/circuit_breaker.py).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, TypeVar

from gispulse.adapters.esb.enums import CircuitBreakerState
from core.logging import get_logger

T = TypeVar("T")

log = get_logger(__name__)


class CircuitBreakerOpenError(Exception):
    """Raised when a call is attempted while the circuit breaker is open."""
    pass


class CircuitBreaker:
    """
    Circuit Breaker implementation for GISPulse ESB external service calls.

    Attributes:
        name:              Identifier used in logs/metrics.
        failure_threshold: Consecutive failures before opening.
        success_threshold: Successes in HALF_OPEN before closing.
        timeout:           Seconds in OPEN before moving to HALF_OPEN.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout

        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    @property
    def is_closed(self) -> bool:
        return self._state == CircuitBreakerState.CLOSED

    @property
    def is_open(self) -> bool:
        return self._state == CircuitBreakerState.OPEN

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    async def _check_timeout_transition(self) -> None:
        """Attempt OPEN → HALF_OPEN if the timeout has elapsed."""
        if (
            self._state == CircuitBreakerState.OPEN
            and self._last_failure_time is not None
            and time.time() - self._last_failure_time >= self.timeout
        ):
            await self._transition_to(CircuitBreakerState.HALF_OPEN)

    async def _transition_to(self, new_state: CircuitBreakerState) -> None:
        old_state = self._state
        self._state = new_state

        if new_state == CircuitBreakerState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
        elif new_state == CircuitBreakerState.OPEN:
            self._success_count = 0
            self._last_failure_time = time.time()
        elif new_state == CircuitBreakerState.HALF_OPEN:
            self._success_count = 0

        log.info("circuit_breaker_transition", name=self.name, from_state=old_state.value, to_state=new_state.value)

    # ------------------------------------------------------------------
    # Record outcomes
    # ------------------------------------------------------------------

    async def record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    await self._transition_to(CircuitBreakerState.CLOSED)
            elif self._state == CircuitBreakerState.CLOSED:
                if self._failure_count > 0:
                    self._failure_count -= 1  # Progressive decrement

    async def record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitBreakerState.HALF_OPEN:
                await self._transition_to(CircuitBreakerState.OPEN)
            elif self._state == CircuitBreakerState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    await self._transition_to(CircuitBreakerState.OPEN)

    async def can_execute(self) -> bool:
        async with self._lock:
            await self._check_timeout_transition()
            return self._state in (
                CircuitBreakerState.CLOSED,
                CircuitBreakerState.HALF_OPEN,
            )

    # ------------------------------------------------------------------
    # Execute helper
    # ------------------------------------------------------------------

    async def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute a function protected by this circuit breaker.

        Args:
            func:    Callable (sync or async) to execute.
            *args:   Positional arguments forwarded to *func*.
            **kwargs: Keyword arguments forwarded to *func*.

        Returns:
            Result of *func*.

        Raises:
            CircuitBreakerOpenError: If the circuit is currently open.
            Exception: Any exception raised by *func* (after recording failure).
        """
        if not await self.can_execute():
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.name}' is open — service unavailable."
            )
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            await self.record_success()
            return result  # type: ignore[return-value]
        except Exception:
            await self.record_failure()
            raise

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "failure_threshold": self.failure_threshold,
            "success_threshold": self.success_threshold,
            "timeout": self.timeout,
        }


# ---------------------------------------------------------------------------
# Global registry (singleton per circuit name)
# ---------------------------------------------------------------------------

_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    success_threshold: int = 2,
    timeout: float = 30.0,
) -> CircuitBreaker:
    """Return a named circuit breaker, creating it if it does not exist.

    Parameters are only applied at creation time.

    Args:
        name:              Unique circuit name (e.g. 'postgis', 'webhook').
        failure_threshold: Consecutive failures before opening.
        success_threshold: Successes in HALF_OPEN before closing.
        timeout:           Seconds in OPEN before probing.

    Returns:
        The (possibly newly created) CircuitBreaker instance.
    """
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            success_threshold=success_threshold,
            timeout=timeout,
        )
    return _circuit_breakers[name]
