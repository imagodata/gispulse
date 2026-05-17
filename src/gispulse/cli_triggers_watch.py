"""Daemon loop for ``gispulse triggers run --watch``.

Extracted from ``cli_triggers.py`` so the loop is unit-testable without
spawning a subprocess. The CLI command thin-wraps :func:`run_watch_loop`
and only owns argument parsing + signal binding.

Design
------
- The loop drives :meth:`HeadlessRuntime.run_once` itself rather than
  starting :meth:`HeadlessRuntime.start` (which would spawn the watcher's
  daemon thread). Reasons:

  * The brief mandates a per-tick structured JSON log line on stderr,
    with metrics (``fired``, ``skipped_predicate``, ``errors``,
    ``duration_ms``, ``sqlite_busy_retries``) that the watcher does not
    expose today.
  * The brief mandates "10 consecutive failed ticks → exit 1". That
    policy lives at the CLI level, not in the persistence layer.
  * The loop also re-validates the YAML config on mtime change, which
    requires CLI ownership of the trigger list anyway.

- Cancellation flows through a single :class:`threading.Event` injected
  by the CLI. SIGINT / SIGTERM handlers ``set()`` it, the loop breaks
  on the next ``Event.wait`` boundary.

- ``Sleeper`` is a callable ``(timeout: float) -> bool`` (returns True
  on cancellation) so tests can mock time without freezing the event
  loop.

- The runtime is rebuilt from scratch on every successful YAML reload.
  This is heavier than mutating ``runtime.triggers`` in place, but it
  guarantees that ``poll_interval``, ``webhook_allowlist`` and other
  build-time decisions stay coherent with the on-disk config.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover
    from gispulse.runtime.config_loader import GISPulseConfig
    from gispulse.runtime.headless_runtime import HeadlessRuntime

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Brief: "Si 10 ticks consécutifs échouent → exit 1".
MAX_CONSECUTIVE_TICK_FAILURES: int = 10

# Brief: "sleep backoff exponentiel (cap 30s)".
TICK_ERROR_BACKOFF_INITIAL: float = 1.0
TICK_ERROR_BACKOFF_CAP: float = 30.0
TICK_ERROR_BACKOFF_MULTIPLIER: float = 2.0


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


@dataclass
class TickMetrics:
    """Per-tick counters surfaced in the structured JSON log."""

    fired: int = 0
    skipped_predicate: int = 0
    errors: int = 0
    duration_ms: float = 0.0
    sqlite_busy_retries: int = 0
    rows_processed: int = 0


# ---------------------------------------------------------------------------
# Sleeper protocol
# ---------------------------------------------------------------------------


class CancellableSleeper:
    """Callable wrapper around a :class:`threading.Event`.

    Called as ``sleeper(timeout) -> bool`` where the return value is
    ``True`` if the event was set (cancellation requested) and ``False``
    on natural timeout. Tests can swap the underlying implementation by
    passing a different callable to :func:`run_watch_loop`.
    """

    def __init__(self, event: threading.Event) -> None:
        self._event = event

    def __call__(self, timeout: float) -> bool:
        # ``Event.wait`` already returns True on set / False on timeout —
        # exactly the contract we need.
        return self._event.wait(timeout=timeout)


# ---------------------------------------------------------------------------
# Config reload state
# ---------------------------------------------------------------------------


@dataclass
class _ConfigSnapshot:
    """Last successfully-loaded config + the file mtime that produced it.

    Stored across ticks so we can short-circuit when the YAML hasn't
    changed and so we keep the *previous* runtime alive when a reload
    fails (broken YAML must not crash the daemon).
    """

    cfg: "GISPulseConfig"
    mtime_ns: int
    runtime: "HeadlessRuntime"
    # We close the previous runtime after the new one is built so a
    # failed reload doesn't leave us with a closed engine.
    closed: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def emit_json(event: str, **fields: Any) -> None:
    """Emit a single JSON record on stderr (one tick = one line).

    Mirrors :func:`gispulse.cli_triggers._log_event` but exposed at
    module scope so the watch loop can use it without circular imports.
    """
    record: dict[str, Any] = {"event": event, **fields}
    try:
        line = json.dumps(record, default=str, separators=(",", ":"))
    except Exception:  # pragma: no cover - extreme defensive
        line = json.dumps({"event": event, "format_error": True})
    print(line, file=sys.stderr, flush=True)


def install_signal_handlers(stop_event: threading.Event) -> Callable[[], None]:
    """Wire SIGINT / SIGTERM to set ``stop_event``.

    Returns a callable that restores the previous handlers — invoke it
    on exit so other code (pytest, embedders) sees the original
    behaviour.

    Windows only ships SIGINT (no SIGTERM). We skip SIGTERM there
    silently rather than blowing up at install time.
    """
    import signal

    previous: list[tuple[int, Any]] = []

    def _handler(signum: int, frame: Any) -> None:
        emit_json("watch_signal_received", signum=int(signum))
        stop_event.set()

    signums: list[int] = [signal.SIGINT]
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:
        signums.append(int(sigterm))

    for sig in signums:
        try:
            previous.append((sig, signal.signal(sig, _handler)))
        except (ValueError, OSError):
            # Outside main thread (signal.signal() requires main) or
            # signal not supported on this platform. Tests run inside
            # threads — we silently skip and they fall back to setting
            # ``stop_event`` directly.
            continue

    def _restore() -> None:
        for sig, prev in previous:
            try:
                signal.signal(sig, prev)
            except (ValueError, OSError):
                pass

    return _restore


def _stat_mtime_ns(path: Path) -> int:
    """Return ``path.stat().st_mtime_ns`` or 0 if the file is gone.

    Used as the "has the YAML changed?" key. We fall back to 0 on
    failure so a transiently-missing file (operator's editor swap-file
    flicker) does not crash the daemon — the next stat will pick up
    the real mtime.
    """
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _build_runtime_from_config(
    cfg: "GISPulseConfig",
    *,
    gpkg_override: Path | None,
) -> "HeadlessRuntime":
    """Wire a fresh :class:`HeadlessRuntime` from ``cfg``.

    Mirrors the logic in :func:`gispulse.cli_triggers.cmd_run`. Imported
    locally to keep ``cli_triggers_watch`` import-light.
    """
    from gispulse.runtime.config_loader import to_triggers
    from gispulse.runtime.headless_runtime import build_runtime

    triggers = to_triggers(cfg)
    return build_runtime(
        gpkg_path=Path(cfg.gpkg) if gpkg_override is None else gpkg_override,
        triggers=triggers,
        webhook_allowlist=cfg.security.webhook_allowlist or None,
        poll_interval=cfg.runtime.poll_interval_ms / 1000.0,
        batch_limit=cfg.runtime.max_batch,
        dataset_id="__cli__",
    )


def _maybe_reload(
    snapshot: _ConfigSnapshot,
    *,
    config_path: Path,
    gpkg_override: Path | None,
) -> _ConfigSnapshot:
    """If the YAML mtime changed, reload + revalidate. On failure, keep
    the previous snapshot alive and emit an ERROR event."""
    from gispulse.runtime.config_loader import (
        ConfigError,
        load_config,
        validate_against_gpkg,
    )

    new_mtime = _stat_mtime_ns(config_path)
    if new_mtime == snapshot.mtime_ns or new_mtime == 0:
        return snapshot

    emit_json("watch_config_reload_attempt", path=str(config_path))
    try:
        cfg = load_config(config_path, gpkg_override=gpkg_override)
        errors = validate_against_gpkg(cfg)
        if errors:
            for err in errors:
                emit_json("watch_config_reload_schema_error", message=err)
            return _ConfigSnapshot(
                cfg=snapshot.cfg,
                mtime_ns=new_mtime,  # don't keep retrying the same broken file
                runtime=snapshot.runtime,
                closed=snapshot.closed,
            )
    except ConfigError as exc:
        emit_json("watch_config_reload_failed", error=str(exc))
        return _ConfigSnapshot(
            cfg=snapshot.cfg,
            mtime_ns=new_mtime,
            runtime=snapshot.runtime,
            closed=snapshot.closed,
        )

    # Build the new runtime *before* closing the old one so a build
    # failure does not leave the daemon without an engine.
    try:
        new_runtime = _build_runtime_from_config(cfg, gpkg_override=gpkg_override)
    except Exception as exc:
        emit_json("watch_config_reload_build_failed", error=str(exc))
        return _ConfigSnapshot(
            cfg=snapshot.cfg,
            mtime_ns=new_mtime,
            runtime=snapshot.runtime,
            closed=snapshot.closed,
        )

    # Successful swap.
    try:
        snapshot.runtime.close()
    except Exception as exc:
        log.warning("watch_old_runtime_close_failed: %s", exc)
    emit_json("watch_config_reloaded", triggers=len(cfg.triggers))
    return _ConfigSnapshot(
        cfg=cfg, mtime_ns=new_mtime, runtime=new_runtime, closed=False
    )


def _start_source_watcher(runtime: "HeadlessRuntime") -> Any:
    """Build + start the external-source watcher for ``runtime`` (#197).

    Returns the running :class:`SourceWatcherRegistry`, or ``None`` when
    the config declares no ``source_changed`` trigger (or the build
    fails — a broken source watcher must never abort the DML daemon).
    """
    from gispulse.runtime.source_watch import build_source_watcher

    try:
        watcher = build_source_watcher(
            runtime.triggers, runtime.action_dispatcher
        )
    except Exception as exc:  # noqa: BLE001 — never abort the DML loop
        emit_json("source_watcher_build_failed", error=str(exc))
        return None
    if watcher is None:
        return None
    watcher.start()
    emit_json("source_watcher_started", watched=watcher.list_watched())
    return watcher


# ---------------------------------------------------------------------------
# The loop itself
# ---------------------------------------------------------------------------


def run_watch_loop(
    *,
    initial_runtime: "HeadlessRuntime",
    initial_cfg: "GISPulseConfig",
    config_path: Path,
    gpkg_override: Path | None,
    poll_interval: float,
    stop_event: threading.Event | None = None,
    sleeper: Callable[[float], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    max_ticks: int | None = None,
) -> int:
    """Drive ticks until ``stop_event`` is set.

    Args:
        initial_runtime:    Already-built runtime (so the caller can fail
                            loudly on the first build before entering the
                            loop). The loop owns it from this point and
                            will :meth:`HeadlessRuntime.close` on exit /
                            on every successful reload.
        initial_cfg:        Validated config that produced
                            ``initial_runtime``. Used as the snapshot
                            seed for reload-on-mtime-change.
        config_path:        Absolute path to the YAML file. Polled with
                            ``stat().st_mtime_ns`` each tick.
        gpkg_override:      ``--gpkg`` override (forwarded on reload).
        poll_interval:      Seconds between successful ticks. Independent
                            of the watcher's own ``poll_interval`` (we
                            drive ``run_once`` ourselves; the watcher's
                            daemon thread is never started).
        stop_event:         External cancellation primitive. When omitted
                            a fresh :class:`Event` is created — typical
                            CLI usage installs signal handlers that set
                            it.
        sleeper:            Cancellable sleep ``(timeout) -> bool`` (True
                            on cancel). Defaults to a wrapper around
                            ``stop_event.wait``.
        clock:              Monotonic time source for the per-tick
                            duration metric. Test injection point.
        max_ticks:          When set, exit after this many ticks
                            regardless of ``stop_event`` — handy for
                            integration tests that want a finite run.

    Returns:
        Process exit code: ``0`` on clean shutdown, ``1`` when the
        consecutive-failure budget is exhausted.
    """
    if stop_event is None:
        stop_event = threading.Event()
    if sleeper is None:
        sleeper = CancellableSleeper(stop_event)
    if poll_interval <= 0:
        raise ValueError("poll_interval must be > 0")

    snapshot = _ConfigSnapshot(
        cfg=initial_cfg,
        mtime_ns=_stat_mtime_ns(config_path),
        runtime=initial_runtime,
    )

    consecutive_failures = 0
    error_backoff = TICK_ERROR_BACKOFF_INITIAL
    tick_count = 0

    emit_json(
        "watch_started",
        config=str(config_path),
        gpkg=str(initial_runtime.gpkg_path),
        poll_interval_ms=int(poll_interval * 1000),
        triggers=len(initial_runtime.triggers),
    )

    # Source watcher (#197) — polls external data sources declared with
    # ``on: {source_changed: ...}`` on its own slow daemon thread. ``None``
    # when the config has no source trigger. Rebuilt on each runtime swap.
    source_watcher = _start_source_watcher(initial_runtime)
    watched_runtime: "HeadlessRuntime" = initial_runtime

    try:
        while not stop_event.is_set():
            tick_count += 1
            metrics = TickMetrics()
            t0 = clock()

            # Reload-on-config-change check — happens before every tick so
            # we never run a tick against a stale YAML for longer than
            # ``poll_interval`` seconds.
            try:
                snapshot = _maybe_reload(
                    snapshot,
                    config_path=config_path,
                    gpkg_override=gpkg_override,
                )
            except Exception as exc:
                # Defensive: _maybe_reload swallows known errors. An
                # unexpected exception here means a bug, not a transient
                # — log loudly and fall through.
                emit_json("watch_reload_unexpected_error", error=str(exc))

            # A successful config reload swaps the runtime; rebuild the
            # source watcher so it tracks the new trigger list / dispatcher
            # (#197). ``watched_runtime`` holds a reference so the swapped
            # runtime object identity stays stable for this comparison.
            if snapshot.runtime is not watched_runtime:
                if source_watcher is not None:
                    source_watcher.stop()
                source_watcher = _start_source_watcher(snapshot.runtime)
                watched_runtime = snapshot.runtime

            # Snapshot the busy-retry counter before the tick so we can
            # diff after.
            retries_before = (
                snapshot.runtime.retrying_sql.snapshot_retries()
                if snapshot.runtime.retrying_sql is not None
                else 0
            )

            try:
                metrics.rows_processed = snapshot.runtime.run_once()
                consecutive_failures = 0
                error_backoff = TICK_ERROR_BACKOFF_INITIAL
            except Exception as exc:
                metrics.errors += 1
                consecutive_failures += 1
                emit_json(
                    "watch_tick_failed",
                    error=str(exc),
                    consecutive_failures=consecutive_failures,
                )
                if consecutive_failures >= MAX_CONSECUTIVE_TICK_FAILURES:
                    emit_json(
                        "watch_aborted_after_failures",
                        consecutive_failures=consecutive_failures,
                        max=MAX_CONSECUTIVE_TICK_FAILURES,
                    )
                    return 1
                # Sleep on the cancellable sleeper so SIGINT during
                # backoff is honoured. Cap exponential growth.
                if sleeper(error_backoff):
                    break
                error_backoff = min(
                    error_backoff * TICK_ERROR_BACKOFF_MULTIPLIER,
                    TICK_ERROR_BACKOFF_CAP,
                )
                continue

            retries_after = (
                snapshot.runtime.retrying_sql.snapshot_retries()
                if snapshot.runtime.retrying_sql is not None
                else 0
            )
            metrics.sqlite_busy_retries = retries_after - retries_before
            metrics.duration_ms = (clock() - t0) * 1000.0

            emit_json(
                "watch_tick",
                tick=tick_count,
                rows_processed=metrics.rows_processed,
                fired=metrics.fired,
                skipped_predicate=metrics.skipped_predicate,
                errors=metrics.errors,
                duration_ms=round(metrics.duration_ms, 3),
                sqlite_busy_retries=metrics.sqlite_busy_retries,
            )

            if max_ticks is not None and tick_count >= max_ticks:
                break

            if sleeper(poll_interval):
                break
    finally:
        if source_watcher is not None:
            try:
                source_watcher.stop()
            except Exception as exc:
                log.warning("source_watcher_stop_failed: %s", exc)
        try:
            snapshot.runtime.close()
        except Exception as exc:
            log.warning("watch_runtime_close_failed: %s", exc)
        emit_json("watch_stopped", ticks=tick_count)

    return 0


__all__ = [
    "MAX_CONSECUTIVE_TICK_FAILURES",
    "TICK_ERROR_BACKOFF_CAP",
    "TICK_ERROR_BACKOFF_INITIAL",
    "TICK_ERROR_BACKOFF_MULTIPLIER",
    "CancellableSleeper",
    "TickMetrics",
    "emit_json",
    "install_signal_handlers",
    "run_watch_loop",
]
