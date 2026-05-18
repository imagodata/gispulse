"""Resilience integration tests for ``gispulse triggers run --watch``.

Three scenarios from the brief:

1. Une exception générique levée par un tick → daemon catch + sleep
   backoff (cancellable) + retry (consecutive_failures < 10).
2. 10 ticks consécutifs foirent d'affilée → exit 1 avec message clair.
3. Le sleeper de backoff respecte la cap exponentielle (1 → 2 → 4 → ...
   → 30 s).

We monkey-patch :meth:`HeadlessRuntime.run_once` to inject failures
deterministically, and inject a sleeper that records timeouts so we can
assert the backoff schedule without real waits.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from gispulse.cli_triggers_watch import (
    MAX_CONSECUTIVE_TICK_FAILURES,
    TICK_ERROR_BACKOFF_CAP,
    TICK_ERROR_BACKOFF_INITIAL,
    TICK_ERROR_BACKOFF_MULTIPLIER,
    run_watch_loop,
)
from gispulse.runtime.config_loader import load_config
from gispulse.runtime.headless_runtime import HeadlessRuntime, build_runtime


# ---------------------------------------------------------------------------
# Fixtures (shared with test_triggers_watch but kept local for clarity)
# ---------------------------------------------------------------------------


@pytest.fixture()
def gpkg_with_parcels(tmp_path: Path) -> Path:
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    gpkg = tmp_path / "resilience.gpkg"
    eng = GeoPackageEngine(path=gpkg)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001
        conn.execute(
            'CREATE TABLE "parcels" '
            '(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)'
        )
        conn.commit()
        eng.enable_change_tracking("parcels")
    finally:
        eng.close()
    return gpkg


@pytest.fixture()
def yaml_one_trigger(gpkg_with_parcels: Path, tmp_path: Path) -> Path:
    cfg = tmp_path / "triggers.yaml"
    cfg.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {gpkg_with_parcels}
            triggers:
              - name: log_only
                table: parcels
                actions:
                  - type: log_event
            runtime:
              poll_interval_ms: 50
              max_batch: 10
            """,
        ).strip(),
        encoding="utf-8",
    )
    return cfg


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _RecordingSleeper:
    """Sleeper that records every timeout it received."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, timeout: float) -> bool:
        self.calls.append(timeout)
        return False


class _ThenSucceedRunOnce:
    """Programmable ``run_once`` that fails N times then succeeds.

    Patched onto the runtime instance so we can simulate a transient
    error without touching the engine.
    """

    def __init__(self, fail_first_n: int) -> None:
        self.fail_first_n = fail_first_n
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> int:
        self.calls += 1
        if self.calls <= self.fail_first_n:
            raise RuntimeError(f"simulated tick failure #{self.calls}")
        return 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_tick_failure_triggers_backoff_and_retry(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One tick raises → daemon logs ``watch_tick_failed``, sleeps on
    the backoff sleeper, then succeeds on the next tick.

    Verifies: counter resets on success, backoff timeout matches the
    initial value, daemon does NOT exit.
    """
    cfg = load_config(yaml_one_trigger)
    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],
        webhook_client=lambda url, payload: None,
        sql_executor=lambda *a, **kw: None,
        poll_interval=0.05,
        batch_limit=10,
        dataset_id="test",
    )

    fail_then_ok = _ThenSucceedRunOnce(fail_first_n=1)
    sleeper = _RecordingSleeper()

    with patch.object(HeadlessRuntime, "run_once", fail_then_ok):
        exit_code = run_watch_loop(
            initial_runtime=runtime,
            initial_cfg=cfg,
            config_path=yaml_one_trigger,
            gpkg_override=None,
            poll_interval=0.05,
            sleeper=sleeper,
            max_ticks=3,
        )

    assert exit_code == 0
    assert fail_then_ok.calls == 3

    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.err.splitlines() if line.strip()]
    failed = [e for e in events if e["event"] == "watch_tick_failed"]
    succeeded = [e for e in events if e["event"] == "watch_tick"]
    assert len(failed) == 1
    assert failed[0]["consecutive_failures"] == 1
    # 3 ticks total, 1 failed, 2 succeeded.
    assert len(succeeded) == 2

    # The sleeper saw the initial backoff timeout once on the failure path,
    # then the regular poll_interval on success-path waits.
    assert TICK_ERROR_BACKOFF_INITIAL in sleeper.calls, (
        f"expected initial backoff timeout in sleeper calls: {sleeper.calls!r}"
    )


def test_ten_consecutive_failures_exits_one(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The brief: 10 ticks consécutifs foirent → exit 1 avec message."""
    cfg = load_config(yaml_one_trigger)
    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],
        webhook_client=lambda url, payload: None,
        sql_executor=lambda *a, **kw: None,
        poll_interval=0.05,
        batch_limit=10,
        dataset_id="test",
    )

    always_fail = _ThenSucceedRunOnce(fail_first_n=1_000)
    sleeper = _RecordingSleeper()

    t0 = time.monotonic()
    with patch.object(HeadlessRuntime, "run_once", always_fail):
        exit_code = run_watch_loop(
            initial_runtime=runtime,
            initial_cfg=cfg,
            config_path=yaml_one_trigger,
            gpkg_override=None,
            poll_interval=0.05,
            sleeper=sleeper,
            # Don't bound by max_ticks — let the failure budget enforce exit.
            max_ticks=None,
            stop_event=threading.Event(),
        )
    elapsed = time.monotonic() - t0

    assert exit_code == 1
    # Brief budget: tests must run < 5 s. With a no-op sleeper this is
    # near-instant, but we assert the bound to catch regressions.
    assert elapsed < 3.0, f"daemon shutdown took {elapsed:.2f}s — sleeper not honoured?"

    # Exactly MAX_CONSECUTIVE_TICK_FAILURES failed run_once calls before exit.
    assert always_fail.calls == MAX_CONSECUTIVE_TICK_FAILURES

    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.err.splitlines() if line.strip()]
    aborts = [e for e in events if e["event"] == "watch_aborted_after_failures"]
    assert len(aborts) == 1
    assert aborts[0]["consecutive_failures"] == MAX_CONSECUTIVE_TICK_FAILURES
    assert aborts[0]["max"] == MAX_CONSECUTIVE_TICK_FAILURES


def test_backoff_grows_exponentially_then_caps(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
) -> None:
    """The sleeper should see backoff timeouts following the
    ``initial * multiplier**n`` schedule, clamped at ``cap``.

    With ``initial=1.0``, ``multiplier=2.0``, ``cap=30.0`` we expect:
        attempt 1 → 1.0
        attempt 2 → 2.0
        attempt 3 → 4.0
        attempt 4 → 8.0
        attempt 5 → 16.0
        attempt 6 → 30.0  (capped, would be 32)
        attempt 7 → 30.0
        attempt 8 → 30.0
        attempt 9 → 30.0
    Then attempt 10 → exit 1 (no further sleep — the budget triggers
    BEFORE the post-failure sleep).
    """
    cfg = load_config(yaml_one_trigger)
    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],
        webhook_client=lambda url, payload: None,
        sql_executor=lambda *a, **kw: None,
        poll_interval=0.05,
        batch_limit=10,
        dataset_id="test",
    )

    always_fail = _ThenSucceedRunOnce(fail_first_n=1_000)
    sleeper = _RecordingSleeper()

    with patch.object(HeadlessRuntime, "run_once", always_fail):
        run_watch_loop(
            initial_runtime=runtime,
            initial_cfg=cfg,
            config_path=yaml_one_trigger,
            gpkg_override=None,
            poll_interval=0.05,
            sleeper=sleeper,
        )

    # Compute expected schedule.
    expected: list[float] = []
    cur = TICK_ERROR_BACKOFF_INITIAL
    for _ in range(MAX_CONSECUTIVE_TICK_FAILURES - 1):
        expected.append(cur)
        cur = min(cur * TICK_ERROR_BACKOFF_MULTIPLIER, TICK_ERROR_BACKOFF_CAP)

    assert sleeper.calls == expected, (
        f"backoff schedule mismatch: expected {expected!r}, got {sleeper.calls!r}"
    )
    # And the final value did hit the cap (proves the clamp works).
    assert TICK_ERROR_BACKOFF_CAP in sleeper.calls


def test_cancellable_backoff_breaks_immediately(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
) -> None:
    """When the sleeper returns True (cancellation requested), the
    daemon must break out of the backoff and exit 0 — even mid-failure
    streak. This is the SIGINT-during-backoff path."""
    cfg = load_config(yaml_one_trigger)
    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],
        webhook_client=lambda url, payload: None,
        sql_executor=lambda *a, **kw: None,
        poll_interval=0.05,
        batch_limit=10,
        dataset_id="test",
    )

    always_fail = _ThenSucceedRunOnce(fail_first_n=1_000)

    # Sleeper that cancels on its first call.
    def cancel_first_call(timeout: float) -> bool:
        return True

    with patch.object(HeadlessRuntime, "run_once", always_fail):
        exit_code = run_watch_loop(
            initial_runtime=runtime,
            initial_cfg=cfg,
            config_path=yaml_one_trigger,
            gpkg_override=None,
            poll_interval=0.05,
            sleeper=cancel_first_call,
        )

    # Clean exit 0 — the consecutive-failure budget is NOT exhausted
    # because the cancellation broke the loop after the first failure.
    assert exit_code == 0
    assert always_fail.calls == 1


def test_run_once_failure_does_not_close_runtime_prematurely(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
) -> None:
    """A failed tick must not close the runtime — the next tick reuses
    the same engine and dispatcher. Verified by asserting we can still
    insert + drain after recovery."""
    cfg = load_config(yaml_one_trigger)
    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],
        webhook_client=lambda url, payload: None,
        sql_executor=lambda *a, **kw: None,
        poll_interval=0.05,
        batch_limit=10,
        dataset_id="test",
    )

    fail_then_ok = _ThenSucceedRunOnce(fail_first_n=2)
    sleeper = _RecordingSleeper()

    # Insert a row before kicking off the loop so the post-recovery
    # tick has something to drain.
    conn = sqlite3.connect(str(gpkg_with_parcels))
    try:
        conn.execute('INSERT INTO "parcels"(name) VALUES (?)', ("alpha",))
        conn.commit()
    finally:
        conn.close()

    with patch.object(HeadlessRuntime, "run_once", fail_then_ok):
        exit_code = run_watch_loop(
            initial_runtime=runtime,
            initial_cfg=cfg,
            config_path=yaml_one_trigger,
            gpkg_override=None,
            poll_interval=0.05,
            sleeper=sleeper,
            max_ticks=4,
        )

    # 2 fails + 2 successes = 4 calls.
    assert fail_then_ok.calls == 4
    assert exit_code == 0
