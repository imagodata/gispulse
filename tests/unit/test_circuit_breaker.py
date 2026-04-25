"""Tests for adapters.esb.circuit_breaker — state machine, execute, registry.

Pattern covered: CLOSED → OPEN → HALF_OPEN → CLOSED, plus the global
singleton registry and the sync/async execute helper.
"""
from __future__ import annotations

import asyncio

import pytest

from gispulse.adapters.esb.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    _circuit_breakers,
    get_circuit_breaker,
)
from gispulse.adapters.esb.enums import CircuitBreakerState


@pytest.fixture(autouse=True)
def _reset_registry():
    """Clear the global singleton registry between tests."""
    _circuit_breakers.clear()
    yield
    _circuit_breakers.clear()


@pytest.mark.asyncio
class TestInitialState:
    async def test_starts_closed(self):
        cb = CircuitBreaker(name="x")
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.is_closed is True
        assert cb.is_open is False

    async def test_can_execute_when_closed(self):
        cb = CircuitBreaker(name="x")
        assert await cb.can_execute() is True


@pytest.mark.asyncio
class TestTransitionsClosedToOpen:
    async def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(name="x", failure_threshold=3)
        for _ in range(3):
            await cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        assert cb.is_open is True

    async def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker(name="x", failure_threshold=3)
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == CircuitBreakerState.CLOSED

    async def test_success_decrements_failure_count_in_closed(self):
        cb = CircuitBreaker(name="x", failure_threshold=3)
        await cb.record_failure()
        await cb.record_failure()
        await cb.record_success()  # progressive recovery
        assert cb._failure_count == 1
        await cb.record_failure()
        assert cb.state == CircuitBreakerState.CLOSED  # still not at threshold


@pytest.mark.asyncio
class TestTransitionsOpenToHalfOpen:
    async def test_open_rejects_calls(self):
        cb = CircuitBreaker(name="x", failure_threshold=1)
        await cb.record_failure()
        assert await cb.can_execute() is False

    async def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(name="x", failure_threshold=1, timeout=0.05)
        await cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        await asyncio.sleep(0.06)
        assert await cb.can_execute() is True
        assert cb.state == CircuitBreakerState.HALF_OPEN

    async def test_half_open_success_closes_after_threshold(self):
        cb = CircuitBreaker(name="x", failure_threshold=1, success_threshold=2, timeout=0.01)
        await cb.record_failure()
        await asyncio.sleep(0.02)
        await cb.can_execute()  # trigger OPEN → HALF_OPEN
        assert cb.state == CircuitBreakerState.HALF_OPEN
        await cb.record_success()
        assert cb.state == CircuitBreakerState.HALF_OPEN  # still one below threshold
        await cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED

    async def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(name="x", failure_threshold=1, timeout=0.01)
        await cb.record_failure()
        await asyncio.sleep(0.02)
        await cb.can_execute()  # → HALF_OPEN
        assert cb.state == CircuitBreakerState.HALF_OPEN
        await cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN


@pytest.mark.asyncio
class TestExecuteHelper:
    async def test_execute_sync_callable(self):
        cb = CircuitBreaker(name="x")
        result = await cb.execute(lambda a, b: a + b, 2, 3)
        assert result == 5
        assert cb.state == CircuitBreakerState.CLOSED

    async def test_execute_async_callable(self):
        cb = CircuitBreaker(name="x")

        async def work(x):
            await asyncio.sleep(0)
            return x * 2

        result = await cb.execute(work, 7)
        assert result == 14

    async def test_execute_raises_when_open(self):
        cb = CircuitBreaker(name="x", failure_threshold=1)
        await cb.record_failure()
        with pytest.raises(CircuitBreakerOpenError, match="is open"):
            await cb.execute(lambda: 42)

    async def test_execute_records_failure_and_reraises(self):
        cb = CircuitBreaker(name="x", failure_threshold=2)

        def boom():
            raise RuntimeError("downstream failure")

        with pytest.raises(RuntimeError, match="downstream failure"):
            await cb.execute(boom)
        assert cb._failure_count == 1
        assert cb.state == CircuitBreakerState.CLOSED  # below threshold

        with pytest.raises(RuntimeError):
            await cb.execute(boom)
        assert cb.state == CircuitBreakerState.OPEN


@pytest.mark.asyncio
class TestObservability:
    async def test_get_stats_reflects_state(self):
        cb = CircuitBreaker(
            name="postgis",
            failure_threshold=4,
            success_threshold=2,
            timeout=15.0,
        )
        stats = cb.get_stats()
        assert stats["name"] == "postgis"
        assert stats["state"] == "CLOSED"
        assert stats["failure_threshold"] == 4
        assert stats["success_threshold"] == 2
        assert stats["timeout"] == 15.0
        assert stats["failure_count"] == 0

        await cb.record_failure()
        stats = cb.get_stats()
        assert stats["failure_count"] == 1


class TestGlobalRegistry:
    def test_get_or_create_returns_same_instance(self):
        cb1 = get_circuit_breaker("redis", failure_threshold=10)
        cb2 = get_circuit_breaker("redis")
        assert cb1 is cb2
        # Thresholds from the initial call are preserved
        assert cb1.failure_threshold == 10

    def test_different_names_get_independent_instances(self):
        cb_a = get_circuit_breaker("webhook-a")
        cb_b = get_circuit_breaker("webhook-b")
        assert cb_a is not cb_b
        assert cb_a.name == "webhook-a"
        assert cb_b.name == "webhook-b"

    def test_constructor_params_ignored_after_creation(self):
        # First call sets threshold=3
        cb1 = get_circuit_breaker("api", failure_threshold=3)
        # Second call attempts to change it — ignored because the breaker already exists
        cb2 = get_circuit_breaker("api", failure_threshold=99)
        assert cb1 is cb2
        assert cb2.failure_threshold == 3
