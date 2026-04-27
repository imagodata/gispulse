"""Tests for the SQLITE_BUSY retry wrapper used by the headless runtime.

Strategy
--------
Drive :class:`RetryingSqlExecutor` directly with a stub ``inner`` that we
can program to raise / succeed on demand. Time is mocked via the
``sleeper`` injection point so the suite stays sub-second.

The wrapper sits between :class:`ActionDispatcher` and the actual GPKG
``execute`` callable. We exercise three flows that the brief calls out:

1. First attempt fails with ``database is locked``, second succeeds →
   action returns the inner's result, retry counter == 1.
2. Five attempts in a row fail with ``database is locked`` → wrapper
   re-raises ``OperationalError``, dispatcher's outer try/except logs,
   change-log row is NOT acked (verified at the watcher layer in the
   CLI integration suite).
3. Non-busy ``OperationalError`` (``no such table``) → fail immediately
   with **zero** retries (we must not mask real bugs by spinning).

The fourth axiom — that the dispatcher itself wires the wrapper
transparently when ``build_runtime`` is called without a custom
``sql_executor`` — is checked via the public
``HeadlessRuntime.retrying_sql`` handle in the existing
``test_headless_runtime`` suite.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from gispulse.runtime.sqlite_retry import (
    DEFAULT_BACKOFF_SCHEDULE,
    RetryingSqlExecutor,
    is_busy_error,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ProgrammableInner:
    """Stub SQL executor whose behaviour is driven by a queue of outcomes.

    Each call pops one item from ``self.outcomes``:

    * an ``Exception`` instance → re-raised
    * anything else → returned as-is

    Tracks every ``(args, kwargs)`` tuple in ``self.calls`` for assertions.
    """

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        if not self.outcomes:
            raise RuntimeError("test outcome queue exhausted")
        result = self.outcomes.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _busy() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("database is locked")


def _busy_other_phrasing() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("database is busy")


def _no_such_table() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("no such table: parcels")


# ---------------------------------------------------------------------------
# is_busy_error
# ---------------------------------------------------------------------------


def test_is_busy_error_recognises_both_phrasings() -> None:
    assert is_busy_error(_busy()) is True
    assert is_busy_error(_busy_other_phrasing()) is True


def test_is_busy_error_rejects_other_operational_errors() -> None:
    assert is_busy_error(_no_such_table()) is False
    assert is_busy_error(sqlite3.OperationalError("syntax error")) is False


def test_is_busy_error_rejects_non_sqlite_exceptions() -> None:
    assert is_busy_error(RuntimeError("database is locked")) is False
    assert is_busy_error(ValueError()) is False


# ---------------------------------------------------------------------------
# RetryingSqlExecutor — happy path / retry / exhaustion
# ---------------------------------------------------------------------------


def test_first_attempt_succeeds_no_retry() -> None:
    inner = _ProgrammableInner([42])
    sleeps: list[float] = []
    wrapper = RetryingSqlExecutor(inner, sleeper=sleeps.append)

    result = wrapper("SELECT 1", [])

    assert result == 42
    assert wrapper.snapshot_retries() == 0
    assert sleeps == [], "no retries → no sleep"
    assert len(inner.calls) == 1


def test_one_busy_then_success_reports_one_retry() -> None:
    """First call raises 'database is locked', second returns OK."""
    inner = _ProgrammableInner([_busy(), "ok"])
    sleeps: list[float] = []
    wrapper = RetryingSqlExecutor(inner, sleeper=sleeps.append)

    result = wrapper("UPDATE parcels SET x=1 WHERE fid=?", [1])

    assert result == "ok"
    assert wrapper.snapshot_retries() == 1
    assert len(sleeps) == 1
    # Jitter is ±20 %: the slot is 0.1 s so we land in [0.08, 0.12].
    assert 0.08 - 1e-9 <= sleeps[0] <= 0.12 + 1e-9
    assert len(inner.calls) == 2


def test_busy_alternative_phrasing_also_retried() -> None:
    inner = _ProgrammableInner([_busy_other_phrasing(), "ok"])
    wrapper = RetryingSqlExecutor(inner, sleeper=lambda _t: None)

    assert wrapper("x") == "ok"
    assert wrapper.snapshot_retries() == 1


def test_five_busy_in_a_row_exhausts_and_reraises() -> None:
    """Default schedule has 5 slots: 6 calls (1 initial + 5 retries) all
    fail busy → the final one re-raises ``OperationalError``."""
    schedule = DEFAULT_BACKOFF_SCHEDULE  # 5 slots
    n_attempts = len(schedule) + 1  # 6 total
    inner = _ProgrammableInner([_busy() for _ in range(n_attempts)])
    sleeps: list[float] = []
    wrapper = RetryingSqlExecutor(inner, sleeper=sleeps.append)

    with pytest.raises(sqlite3.OperationalError) as ei:
        wrapper("UPDATE parcels SET x=1")

    assert is_busy_error(ei.value)
    # Counter increments only for retries that actually fired (not the
    # final exhaustion → it's the 5th retry that raised after sleeping).
    # Specifically: attempts 0..4 sleep + retry, attempt 5 raises.
    assert wrapper.snapshot_retries() == len(schedule)
    assert len(sleeps) == len(schedule)
    assert len(inner.calls) == n_attempts


def test_non_busy_operational_error_fails_immediately() -> None:
    """Any non-busy ``OperationalError`` (no such table, syntax error,
    …) must NOT be retried. Spinning here would mask real bugs."""
    inner = _ProgrammableInner([_no_such_table(), "would_succeed"])
    sleeps: list[float] = []
    wrapper = RetryingSqlExecutor(inner, sleeper=sleeps.append)

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        wrapper("SELECT * FROM parcels")

    assert wrapper.snapshot_retries() == 0
    assert sleeps == [], "no retry on permanent error"
    assert len(inner.calls) == 1


def test_non_operational_exception_propagates_unchanged() -> None:
    """``IntegrityError``, ``ValueError`` etc. should bypass the retry."""
    err = sqlite3.IntegrityError("UNIQUE constraint failed")
    inner = _ProgrammableInner([err])
    wrapper = RetryingSqlExecutor(inner, sleeper=lambda _t: None)

    with pytest.raises(sqlite3.IntegrityError):
        wrapper("INSERT ...")

    assert wrapper.snapshot_retries() == 0


# ---------------------------------------------------------------------------
# Counter / reset semantics
# ---------------------------------------------------------------------------


def test_snapshot_retries_is_cumulative_across_calls() -> None:
    """The CLI tick log relies on ``snapshot_retries()`` returning the
    running total so it can diff per tick."""
    inner = _ProgrammableInner([
        _busy(), "ok",          # call 1: 1 retry
        _busy(), _busy(), "ok"  # call 2: 2 retries
    ])
    wrapper = RetryingSqlExecutor(inner, sleeper=lambda _t: None)

    wrapper("a")
    assert wrapper.snapshot_retries() == 1
    wrapper("b")
    assert wrapper.snapshot_retries() == 3


def test_reset_retries_zeroes_the_counter() -> None:
    inner = _ProgrammableInner([_busy(), "ok"])
    wrapper = RetryingSqlExecutor(inner, sleeper=lambda _t: None)
    wrapper("x")
    assert wrapper.snapshot_retries() == 1
    wrapper.reset_retries()
    assert wrapper.snapshot_retries() == 0


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_constructor_rejects_none_inner() -> None:
    with pytest.raises(ValueError, match="inner"):
        RetryingSqlExecutor(None)  # type: ignore[arg-type]


def test_constructor_rejects_empty_backoff() -> None:
    with pytest.raises(ValueError, match="backoff"):
        RetryingSqlExecutor(lambda *a, **k: None, backoff=())


def test_constructor_rejects_invalid_jitter() -> None:
    with pytest.raises(ValueError, match="jitter"):
        RetryingSqlExecutor(lambda *a, **k: None, jitter_pct=1.5)
    with pytest.raises(ValueError, match="jitter"):
        RetryingSqlExecutor(lambda *a, **k: None, jitter_pct=-0.1)


# ---------------------------------------------------------------------------
# Custom backoff schedule
# ---------------------------------------------------------------------------


def test_custom_short_schedule_respected() -> None:
    """A 2-slot schedule means at most 3 attempts."""
    inner = _ProgrammableInner([_busy(), _busy(), _busy()])
    sleeps: list[float] = []
    wrapper = RetryingSqlExecutor(
        inner, backoff=(0.001, 0.002), jitter_pct=0.0, sleeper=sleeps.append
    )

    with pytest.raises(sqlite3.OperationalError):
        wrapper("x")
    assert len(inner.calls) == 3
    assert sleeps == [0.001, 0.002]
    assert wrapper.snapshot_retries() == 2


# ---------------------------------------------------------------------------
# Dispatcher integration via build_runtime: the wrapper is wired by
# default and surfaces on the runtime handle.
# ---------------------------------------------------------------------------


def test_build_runtime_wraps_engine_execute_by_default(
    tmp_path: Any,
) -> None:
    """S6: :meth:`GeoPackageEngine.execute` now exists (sandbox'd DML
    path with guardrails). When no ``sql_executor=`` is injected,
    ``build_runtime`` picks up ``engine.execute`` and wraps it in
    :class:`RetryingSqlExecutor`."""
    from persistence.gpkg_engine import GeoPackageEngine

    from gispulse.runtime import build_runtime

    gpkg = tmp_path / "fixture.gpkg"
    eng = GeoPackageEngine(path=gpkg)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001
        conn.execute('CREATE TABLE "x"(id INTEGER PRIMARY KEY)')
        conn.commit()
    finally:
        eng.close()

    runtime = build_runtime(
        gpkg_path=gpkg,
        triggers=[],
        webhook_client=lambda url, payload: None,
        dataset_id="test",
    )
    try:
        # S6: engine.execute is the default; the retry wrapper is now
        # always installed when no custom executor was injected.
        assert runtime.retrying_sql is not None
        # Sanity-check it actually delegates to engine.execute through
        # the guardrail path: an INSERT into a user table works…
        rc = runtime.retrying_sql('INSERT INTO "x"(id) VALUES (1)', [])
        assert rc == 1
    finally:
        runtime.close()


def test_build_runtime_also_wraps_user_supplied_executor(
    tmp_path: Any,
) -> None:
    """When the caller injects a stub, we still wrap it — so a user's
    integration test that simulates SQLITE_BUSY benefits from the same
    retry policy as production."""
    from persistence.gpkg_engine import GeoPackageEngine

    from gispulse.runtime import build_runtime

    gpkg = tmp_path / "fixture.gpkg"
    eng = GeoPackageEngine(path=gpkg)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001
        conn.execute('CREATE TABLE "x"(id INTEGER PRIMARY KEY)')
        conn.commit()
    finally:
        eng.close()

    captured: list[tuple[str, Any]] = []

    def stub(sql: str, params: Any = None) -> None:
        captured.append((sql, params))

    runtime = build_runtime(
        gpkg_path=gpkg,
        triggers=[],
        sql_executor=stub,
        webhook_client=lambda url, payload: None,
        dataset_id="test",
    )
    try:
        assert runtime.retrying_sql is not None
        # Passing through the wrapper should still reach the stub.
        runtime.retrying_sql("SELECT 1", [])
        assert captured == [("SELECT 1", [])]
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# S6: SecurityError must NOT be retried
# ---------------------------------------------------------------------------


def test_security_error_is_not_retried() -> None:
    """A guardrail violation surfaces immediately — not after 5
    pointless retry sleeps. The retry wrapper only catches
    :class:`sqlite3.OperationalError` (and only when the message
    matches ``database is locked`` / ``database is busy``); a
    :class:`SecurityError` bypasses the catch entirely.
    """
    from persistence.sql_guardrails import SecurityError

    inner = _ProgrammableInner([SecurityError("forbidden table")])
    sleeps: list[float] = []
    wrapper = RetryingSqlExecutor(inner, sleeper=sleeps.append)

    with pytest.raises(SecurityError):
        wrapper("DELETE FROM gpkg_contents", [])

    assert len(inner.calls) == 1, "SecurityError must not be retried"
    assert sleeps == [], "no backoff sleeps allowed for SecurityError"
    assert wrapper.snapshot_retries() == 0


def test_security_error_through_engine_via_wrapper(tmp_path: Any) -> None:
    """End-to-end on the real engine: the wrapper surfaces
    SecurityError without retrying."""
    from persistence.gpkg_engine import GeoPackageEngine
    from persistence.sql_guardrails import SecurityError

    gpkg = tmp_path / "secfast.gpkg"
    eng = GeoPackageEngine(path=gpkg)
    eng.open()
    try:
        eng.execute(
            'CREATE TABLE "items" (id INTEGER PRIMARY KEY)', allow_ddl=True
        )

        sleeps: list[float] = []
        wrapper = RetryingSqlExecutor(eng.execute, sleeper=sleeps.append)

        with pytest.raises(SecurityError):
            wrapper("DROP TABLE items", [])
        # No retries occurred.
        assert sleeps == []
        assert wrapper.snapshot_retries() == 0
    finally:
        eng.close()
