"""Integration tests for ``gispulse triggers run --watch``.

Strategy
--------
The watch loop is split between :func:`gispulse.cli_triggers.cmd_run`
(arg parsing + signal binding) and :func:`gispulse.cli_triggers_watch
.run_watch_loop` (the actual tick → wait → tick body). We test:

1. The loop body **directly** for fast deterministic coverage of:
   - Reload-on-mtime-change (added trigger picked up without restart).
   - Broken YAML on reload kept the previous valid config.
   - ``poll_interval`` is respected — i.e. ``max_ticks=N`` produces
     N tick events.
2. The full CLI command via ``subprocess.Popen`` for the SIGINT
   round-trip (cannot use ``CliRunner`` because Typer's runner does
   not deliver real signals to the process).

All ``time.sleep`` in the loop is replaced by an injected sleeper that
either no-ops or sets the stop event after a configurable number of
calls — keeps every test under 1 s.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from gispulse.cli_triggers_watch import run_watch_loop
from gispulse.runtime.config_loader import load_config, validate_against_gpkg
from gispulse.runtime.headless_runtime import build_runtime


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def gpkg_with_parcels(tmp_path: Path) -> Path:
    from gispulse.persistence.gpkg_engine import GeoPackageEngine

    gpkg = tmp_path / "watch.gpkg"
    eng = GeoPackageEngine(path=gpkg)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001
        conn.execute(
            'CREATE TABLE "parcels" '
            '(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, status TEXT)'
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


def _insert_row(gpkg: Path, name: str = "alpha") -> None:
    conn = sqlite3.connect(str(gpkg))
    try:
        conn.execute(
            'INSERT INTO "parcels"(name, status) VALUES (?, ?)',
            (name, "pending"),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sleeper helpers
# ---------------------------------------------------------------------------


class _CountingSleeper:
    """Sleeper double that records calls and optionally cancels after N."""

    def __init__(self, stop_after: int | None = None, stop_event: threading.Event | None = None) -> None:
        self.calls: list[float] = []
        self._stop_after = stop_after
        self._stop_event = stop_event

    def __call__(self, timeout: float) -> bool:
        self.calls.append(timeout)
        if self._stop_after is not None and len(self.calls) >= self._stop_after:
            if self._stop_event is not None:
                self._stop_event.set()
            return True
        return False


# ---------------------------------------------------------------------------
# Direct loop tests
# ---------------------------------------------------------------------------


def test_watch_loop_runs_three_ticks_then_stops(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``max_ticks=3`` lets us assert deterministic tick count.

    No real sleeping happens (sleeper no-ops), so the wall time stays
    well under the brief's 5 s budget.
    """
    cfg = load_config(yaml_one_trigger)
    assert validate_against_gpkg(cfg) == []
    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],  # we only care that ticks fire, not their content
        webhook_client=lambda url, payload: None,
        sql_executor=lambda *a, **kw: None,
        poll_interval=cfg.runtime.poll_interval_ms / 1000.0,
        batch_limit=cfg.runtime.max_batch,
        dataset_id="test",
    )

    sleeper = _CountingSleeper()
    stop_event = threading.Event()

    t0 = time.monotonic()
    exit_code = run_watch_loop(
        initial_runtime=runtime,
        initial_cfg=cfg,
        config_path=yaml_one_trigger,
        gpkg_override=None,
        poll_interval=0.05,
        stop_event=stop_event,
        sleeper=sleeper,
        max_ticks=3,
    )
    elapsed = time.monotonic() - t0

    assert exit_code == 0
    assert elapsed < 1.0, f"loop took {elapsed:.2f}s — should be near-instant"

    # Inspect the JSON event stream on stderr.
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.err.splitlines() if line.strip()]
    tick_events = [e for e in events if e["event"] == "watch_tick"]
    assert len(tick_events) == 3, (
        f"expected exactly 3 tick events, got {len(tick_events)}: {tick_events!r}"
    )
    # Each tick log carries the contract fields.
    for ev in tick_events:
        assert "rows_processed" in ev
        assert "duration_ms" in ev
        assert "sqlite_busy_retries" in ev


def test_watch_loop_reloads_on_yaml_change(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Modifier le YAML pendant le run → trigger ajoute actif au tick suivant.

    We start with a 1-trigger config, run a tick, then rewrite the YAML
    with 2 triggers. The next tick must reload and the next-but-one
    ``watch_started``-or-``watch_tick`` block should reflect 2 active
    triggers.
    """
    cfg = load_config(yaml_one_trigger)
    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],
        webhook_client=lambda url, payload: None,
        sql_executor=lambda *a, **kw: None,
        poll_interval=cfg.runtime.poll_interval_ms / 1000.0,
        batch_limit=cfg.runtime.max_batch,
        dataset_id="test",
    )

    # Sleeper that, on the 2nd call, mutates the YAML so the 3rd tick
    # picks up the change.
    sleep_count = {"n": 0}

    def hook_sleeper(timeout: float) -> bool:
        sleep_count["n"] += 1
        if sleep_count["n"] == 2:
            # Bump mtime explicitly: some filesystems have second-level
            # mtime resolution and a write within the same second may
            # not change ``st_mtime_ns``.
            new_yaml = textwrap.dedent(
                f"""
                version: 1
                gpkg: {gpkg_with_parcels}
                triggers:
                  - name: log_only
                    table: parcels
                    actions:
                      - type: log_event
                  - name: extra_one
                    table: parcels
                    actions:
                      - type: log_event
                runtime:
                  poll_interval_ms: 50
                  max_batch: 10
                """,
            ).strip()
            yaml_one_trigger.write_text(new_yaml, encoding="utf-8")
            # Force mtime advance.
            future = time.time() + 2
            os.utime(yaml_one_trigger, (future, future))
        return False

    exit_code = run_watch_loop(
        initial_runtime=runtime,
        initial_cfg=cfg,
        config_path=yaml_one_trigger,
        gpkg_override=None,
        poll_interval=0.05,
        sleeper=hook_sleeper,
        max_ticks=4,
    )

    assert exit_code == 0

    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.err.splitlines() if line.strip()]
    reloaded = [e for e in events if e["event"] == "watch_config_reloaded"]
    assert reloaded, (
        "expected at least one watch_config_reloaded event — got: "
        f"{[e['event'] for e in events]}"
    )
    # The reload event reports the new trigger count (2).
    assert reloaded[0]["triggers"] == 2


def test_watch_loop_keeps_old_config_on_broken_yaml(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """YAML cassé après reload → ancien config reste actif, log ERROR émis."""
    cfg = load_config(yaml_one_trigger)
    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],
        webhook_client=lambda url, payload: None,
        sql_executor=lambda *a, **kw: None,
        poll_interval=cfg.runtime.poll_interval_ms / 1000.0,
        batch_limit=cfg.runtime.max_batch,
        dataset_id="test",
    )

    sleep_count = {"n": 0}

    def break_yaml_on_2nd_call(timeout: float) -> bool:
        sleep_count["n"] += 1
        if sleep_count["n"] == 2:
            yaml_one_trigger.write_text(
                "this is: not [valid yaml: at all\n",
                encoding="utf-8",
            )
            future = time.time() + 2
            os.utime(yaml_one_trigger, (future, future))
        return False

    exit_code = run_watch_loop(
        initial_runtime=runtime,
        initial_cfg=cfg,
        config_path=yaml_one_trigger,
        gpkg_override=None,
        poll_interval=0.05,
        sleeper=break_yaml_on_2nd_call,
        max_ticks=4,
    )

    # Daemon must NOT abort on config error — only on tick failure budget.
    assert exit_code == 0

    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.err.splitlines() if line.strip()]
    failures = [e for e in events if e["event"] == "watch_config_reload_failed"]
    reloaded = [e for e in events if e["event"] == "watch_config_reloaded"]

    assert failures, (
        "expected at least one watch_config_reload_failed event when the "
        f"YAML is mutated to garbage: events={[e['event'] for e in events]}"
    )
    assert not reloaded, (
        "broken YAML must not produce watch_config_reloaded — the previous "
        "config stays active"
    )

    # And ticks kept firing — the daemon stayed up.
    ticks = [e for e in events if e["event"] == "watch_tick"]
    assert len(ticks) >= 2


def test_watch_loop_keeps_old_config_on_schema_error(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reload où le YAML est syntaxiquement valide mais reference une
    table inexistante dans le GPKG → schema error, ancienne config
    reste active."""
    cfg = load_config(yaml_one_trigger)
    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],
        webhook_client=lambda url, payload: None,
        sql_executor=lambda *a, **kw: None,
        poll_interval=cfg.runtime.poll_interval_ms / 1000.0,
        batch_limit=cfg.runtime.max_batch,
        dataset_id="test",
    )

    sleep_count = {"n": 0}

    def hook_sleeper(timeout: float) -> bool:
        sleep_count["n"] += 1
        if sleep_count["n"] == 2:
            yaml_one_trigger.write_text(
                textwrap.dedent(
                    f"""
                    version: 1
                    gpkg: {gpkg_with_parcels}
                    triggers:
                      - name: typo
                        table: parcells
                        actions:
                          - type: log_event
                    runtime:
                      poll_interval_ms: 50
                      max_batch: 10
                    """,
                ).strip(),
                encoding="utf-8",
            )
            future = time.time() + 2
            os.utime(yaml_one_trigger, (future, future))
        return False

    exit_code = run_watch_loop(
        initial_runtime=runtime,
        initial_cfg=cfg,
        config_path=yaml_one_trigger,
        gpkg_override=None,
        poll_interval=0.05,
        sleeper=hook_sleeper,
        max_ticks=4,
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.err.splitlines() if line.strip()]
    schema_errs = [
        e for e in events if e["event"] == "watch_config_reload_schema_error"
    ]
    assert schema_errs, (
        f"expected at least one schema_error event; events={[e['event'] for e in events]}"
    )
    # Daemon kept ticking.
    assert sum(1 for e in events if e["event"] == "watch_tick") >= 2


def test_install_signal_handlers_returns_restore_callable(
    tmp_path: Path,
) -> None:
    """``install_signal_handlers`` must return a noop-safe restore
    callable even when called in a thread (where ``signal.signal``
    raises ``ValueError``)."""
    from gispulse.cli_triggers_watch import install_signal_handlers

    stop = threading.Event()
    captured: list[Callable[[], None]] = []

    def runner() -> None:
        # signal.signal() outside main thread raises ValueError on
        # CPython — install_signal_handlers swallows that and returns
        # an empty restore callable. We only assert no crash.
        captured.append(install_signal_handlers(stop))

    t = threading.Thread(target=runner)
    t.start()
    t.join(timeout=2.0)
    assert captured, "install_signal_handlers should return even from thread"
    captured[0]()  # restore — should also be a no-op, not raise


def test_poll_interval_flag_is_respected(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The sleeper receives the configured ``poll_interval`` as its
    timeout. We assert the values it was called with rather than the
    wall clock so the test is hermetic."""
    cfg = load_config(yaml_one_trigger)
    runtime = build_runtime(
        gpkg_path=gpkg_with_parcels,
        triggers=[],
        webhook_client=lambda url, payload: None,
        sql_executor=lambda *a, **kw: None,
        poll_interval=cfg.runtime.poll_interval_ms / 1000.0,
        batch_limit=cfg.runtime.max_batch,
        dataset_id="test",
    )

    sleeper = _CountingSleeper()
    run_watch_loop(
        initial_runtime=runtime,
        initial_cfg=cfg,
        config_path=yaml_one_trigger,
        gpkg_override=None,
        poll_interval=0.05,
        sleeper=sleeper,
        max_ticks=3,
    )
    # max_ticks=3 → 2 sleeps between ticks (the 3rd tick is followed by
    # a max_ticks break before any post-tick sleep). All sleeps must
    # carry the configured 50 ms timeout.
    assert all(abs(t - 0.05) < 1e-9 for t in sleeper.calls), (
        f"sleeper got unexpected timeouts: {sleeper.calls!r}"
    )


# ---------------------------------------------------------------------------
# SIGINT integration via subprocess
# ---------------------------------------------------------------------------


def _python_with_pythonpath() -> dict[str, str]:
    """Return an env dict that includes the project source in PYTHONPATH.

    When running ``python -m gispulse.cli ...`` from a subprocess,
    PYTHONPATH must include the project root for editable-install
    resolution (the test runner has it implicitly, the child does not).
    """
    env = os.environ.copy()
    project_root = Path(__file__).resolve().parents[2]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{project_root}:{existing}" if existing else str(project_root)
    )
    # Stay in the same conda env / venv as the test runner.
    return env


def test_subprocess_watch_exits_cleanly_on_sigint(
    yaml_one_trigger: Path,
    gpkg_with_parcels: Path,
    tmp_path: Path,
) -> None:
    """The brief: ``--watch`` lance la boucle, après 3 ticks et SIGINT
    exit 0 propre.

    We launch the CLI in a subprocess so signal delivery is real, wait
    for at least 3 ``watch_tick`` JSON lines on stderr, then send
    SIGINT and expect a clean exit 0 within 5 seconds.

    Skipped on Windows: SIGINT under WSL/POSIX is reliable, on Win
    Python the semantics around signal.SIGINT and CTRL_C_EVENT differ
    enough that this would need a separate test path.
    """
    if sys.platform == "win32":
        pytest.skip("SIGINT subprocess delivery is not reliable on Windows")

    cmd = [
        sys.executable,
        "-m",
        "gispulse.cli",
        "triggers",
        "run",
        "--config",
        str(yaml_one_trigger),
        "--watch",
        "--poll-interval-ms",
        "50",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_python_with_pythonpath(),
        text=True,
        bufsize=1,
    )

    # Read stderr line-by-line in a thread and accumulate parsed events
    # until we see >= 3 watch_tick events or hit the 4 s soft budget.
    events: list[dict[str, Any]] = []
    seen_ticks = threading.Event()

    def reader() -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            events.append(ev)
            if ev.get("event") == "watch_tick" and (
                sum(1 for e in events if e.get("event") == "watch_tick") >= 3
            ):
                seen_ticks.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    # Wait up to 4 s for 3 ticks to land.
    assert seen_ticks.wait(timeout=4.0), (
        f"did not observe 3 watch_tick events in 4 s — got: "
        f"{[e.get('event') for e in events]}"
    )

    # Send SIGINT and expect prompt clean exit.
    proc.send_signal(signal.SIGINT)
    try:
        rc = proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)
        pytest.fail("watch daemon did not exit within 5 s of SIGINT")

    t.join(timeout=1.0)

    assert rc == 0, (
        f"clean SIGINT must exit 0; got rc={rc}, last events: "
        f"{[e.get('event') for e in events[-5:]]}"
    )
    # And the daemon emitted its stop marker.
    assert any(e.get("event") == "watch_stopped" for e in events), (
        "expected a watch_stopped event before exit"
    )
