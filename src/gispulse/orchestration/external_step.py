"""Built-in ``external`` step kind for GISPulse.

Runs an external command (argv list, NO shell interpolation) as a subprocess
inside a dedicated process group, with:

- **Per-step timeout** — ``params["timeout_seconds"]`` overrides the job-level
  ``DEFAULT_JOB_TIMEOUT``.  When the timeout fires, the process group is sent
  SIGTERM then SIGKILL after a 5-second grace period.
- **Heartbeat** — ``context.heartbeat()`` is called every
  ``_HEARTBEAT_INTERVAL_S`` seconds while the process is alive, which updates
  the ``last_heartbeat`` timestamp that ``recover_stuck_jobs`` reads from Redis.
  This guarantees that a multi-hour dbt run is never killed by stuck-job
  recovery as long as the process is alive.
- **Cancel check** — ``context.cancel_check()`` is polled every
  ``_POLL_INTERVAL_S`` seconds; when it returns ``True``, the process group is
  terminated cooperatively.
- **Log capture** — stdout and stderr are merged and streamed via a daemon
  reader thread.  The last ``log_tail_lines`` lines are stored in
  ``StepResult.artifacts["log_tail"]``.  Progress events with ``{"lines_seen":
  N}`` are emitted every ``_PROGRESS_INTERVAL_LINES`` lines.
- **skip_marker** — if the process writes a line matching
  ``GISPULSE_SKIP_MARKER=<opaque>`` on stdout, the opaque token is captured in
  ``StepResult.artifacts["skip_marker"]`` for a future resume feature.

Semantic — per-step timeout vs. job timeout
-------------------------------------------
A step with ``timeout_seconds`` set will fail after that many seconds regardless
of the job-level ``DEFAULT_JOB_TIMEOUT``.  The job's global timeout (enforced by
the thread-pool future in ``runner.py``) remains the upper bound for the entire
job, so the cumulative duration of all steps still applies.  In other words:

    per-step timeout ≤ effective timeout ≤ job timeout

If no ``timeout_seconds`` is specified, the step will run until cancelled by the
job-level timeout or a cancel signal.

Usage::

    # pipeline_config step
    {
        "id": "run_dbt",
        "kind": "external",
        "params": {
            "argv": ["dbt", "build", "--select", "my_model+"],
            "cwd": "/opt/dbt_project",
            "env": {"DBT_PROFILES_DIR": "/etc/dbt"},
            "timeout_seconds": 14400,
            "log_tail_lines": 200
        }
    }
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from typing import Any

from gispulse.core.logging import get_logger
from gispulse.orchestration.step_kinds import StepContext, StepResult, register_step_kind

log = get_logger(__name__)

# How often to send heartbeat while process is alive (seconds)
_HEARTBEAT_INTERVAL_S: float = 25.0
# How often to poll cancel_check and process liveness (seconds)
_POLL_INTERVAL_S: float = 1.0
# Grace period between SIGTERM and SIGKILL (seconds)
_KILL_GRACE_S: float = 5.0
# How often to emit progress events (every N lines seen)
_PROGRESS_INTERVAL_LINES: int = 50
# Sentinel written by process to capture skip marker
_SKIP_MARKER_PREFIX = "GISPULSE_SKIP_MARKER="
# Maximum bytes retained in StepResult.artifacts["log_tail"].
_TAIL_MAX_BYTES: int = 64 * 1024


def _terminate_group(pid: int) -> None:
    """Send SIGTERM to the process group, then SIGKILL after _KILL_GRACE_S."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + _KILL_GRACE_S
    while time.monotonic() < deadline:
        time.sleep(0.2)
        try:
            # Check if process group still exists (signal 0 = existence probe)
            os.killpg(pid, 0)
        except (ProcessLookupError, PermissionError):
            # ProcessLookupError: group is gone (expected — SIGTERM worked).
            # PermissionError: PID reuse by an unrelated process (macOS) or
            #   the group has been reaped — treat as gone.
            return
    # Grace elapsed — hard kill
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _drain_line(
    item: str,
    tail: deque[str],
    skip_marker: list[str],
    line_count_holder: list[int],
    context: StepContext,
) -> None:
    """Capture one process output line into metrics, marker state, and tail."""
    if item.startswith(_SKIP_MARKER_PREFIX):
        marker = item[len(_SKIP_MARKER_PREFIX):]
        if marker:
            if skip_marker:
                skip_marker[0] = marker
            else:
                skip_marker.append(marker)
        line_count_holder[0] += 1
        n = line_count_holder[0]
        if n % _PROGRESS_INTERVAL_LINES == 0:
            context.emit_progress({"lines_seen": n})
        return

    tail.append(item)
    line_count_holder[0] += 1
    n = line_count_holder[0]
    if n % _PROGRESS_INTERVAL_LINES == 0:
        context.emit_progress({"lines_seen": n})


def _format_log_tail(tail: deque[str]) -> str:
    """Build log_tail with a hard byte cap while preserving newest lines."""
    tail_lines = list(tail)
    total_bytes = 0
    kept: list[str] = []
    for line in reversed(tail_lines):
        line_bytes = len(line.encode("utf-8")) + 1
        if total_bytes + line_bytes > _TAIL_MAX_BYTES:
            break
        kept.append(line)
        total_bytes += line_bytes

    kept.reverse()
    if len(kept) < len(tail_lines):
        truncated_bytes = sum(len(line.encode("utf-8")) + 1 for line in tail_lines)
        truncated_bytes -= total_bytes
        return f"[truncated {truncated_bytes} bytes]\n" + "\n".join(kept)
    return "\n".join(kept)


def _run_external(params: dict, context: StepContext) -> StepResult:
    """Execute an external command and return a StepResult.

    Args:
        params:  Step params from the PipelineSpec.
        context: Runtime context with cancel_check, heartbeat, emit_progress.

    Returns:
        StepResult with status, artifacts (log_tail, optional skip_marker),
        and metrics (exit_code, lines_seen).

    Raises:
        ValueError: If ``argv`` is missing or empty (fast-fail).
    """
    argv: list[str] = params.get("argv", [])
    if not argv:
        return StepResult(
            status="failed",
            error="external step: 'argv' param is required and must be non-empty",
        )
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return StepResult(
            status="failed",
            error="external step: 'argv' must be a list of strings (no shell interpolation)",
        )

    cwd: str | None = params.get("cwd")
    env_override: dict[str, str] = params.get("env", {}) or {}
    timeout_seconds: float | None = params.get("timeout_seconds")
    log_tail_lines: int = int(params.get("log_tail_lines", 100))

    # Merge env: os.environ base + overrides, all values as str
    merged_env = {k: str(v) for k, v in os.environ.items()}
    for k, v in env_override.items():
        merged_env[k] = str(v)

    # Inject resume marker if present — subprocess can restart from checkpoint.
    # The marker is the opaque token emitted by a prior run via
    # GISPULSE_SKIP_MARKER=<token>; on resume it is passed back as
    # GISPULSE_RESUME_MARKER so the script can skip already-processed work.
    if context.resume_marker:
        merged_env["GISPULSE_RESUME_MARKER"] = context.resume_marker

    # Shared state between threads
    tail: deque[str] = deque(maxlen=log_tail_lines)
    skip_marker: list[str] = []  # at most 1 element
    line_count_holder: list[int] = [0]
    output_queue: queue.Queue[str | None] = queue.Queue()
    last_heartbeat_time: list[float] = [time.monotonic()]

    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=merged_env,
            start_new_session=True,  # dedicated process group
            text=True,
        )
    except (OSError, FileNotFoundError) as exc:
        return StepResult(
            status="failed",
            error=f"external step: cannot start process {argv[0]!r}: {exc}",
        )

    pgid = os.getpgid(proc.pid)

    def _reader() -> None:
        """Daemon thread: read stdout/stderr lines, push to queue."""
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                output_queue.put(line.rstrip("\n"))
        finally:
            output_queue.put(None)  # sentinel

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    start_time = time.monotonic()
    terminated = False
    terminate_reason: str = ""

    try:
        while True:
            # Drain available lines (non-blocking)
            while True:
                try:
                    item = output_queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    # stdout closed — process finished (or stderr EOF)
                    # We still fall through to the proc.poll() check below
                    break
                _drain_line(item, tail, skip_marker, line_count_holder, context)
            else:
                pass  # inner while exhausted normally

            # Check if process has exited
            rc = proc.poll()
            if rc is not None:
                break

            # Per-step timeout
            elapsed = time.monotonic() - start_time
            if timeout_seconds is not None and elapsed >= timeout_seconds:
                log.warning(
                    "external_step_timeout",
                    step_id=context.step_id,
                    timeout_seconds=timeout_seconds,
                )
                _terminate_group(pgid)
                terminated = True
                terminate_reason = f"timeout after {timeout_seconds:.0f}s"
                break

            # Cancel check
            if context.cancel_check():
                log.info("external_step_cancelled", step_id=context.step_id)
                _terminate_group(pgid)
                terminated = True
                terminate_reason = "cancelled"
                break

            # Heartbeat
            now = time.monotonic()
            if now - last_heartbeat_time[0] >= _HEARTBEAT_INTERVAL_S:
                context.heartbeat()
                last_heartbeat_time[0] = now

            time.sleep(_POLL_INTERVAL_S)

    finally:
        # Ensure process is reaped
        if not terminated:
            try:
                proc.wait(timeout=_KILL_GRACE_S + 2)
            except subprocess.TimeoutExpired:
                _terminate_group(pgid)
                proc.wait()
        else:
            proc.wait()

        reader_thread.join(timeout=5.0)

        # Drain remaining lines after process exit
        while True:
            try:
                item = output_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                break
            _drain_line(item, tail, skip_marker, line_count_holder, context)

    rc = proc.returncode
    log_tail_str = _format_log_tail(tail)
    artifacts: dict[str, Any] = {"log_tail": log_tail_str}
    if skip_marker:
        artifacts["skip_marker"] = skip_marker[0]

    metrics: dict[str, Any] = {
        "exit_code": rc if rc is not None else -1,
        "lines_seen": line_count_holder[0],
        "duration_seconds": round(time.monotonic() - start_time, 2),
    }

    if terminated:
        return StepResult(
            status="failed",
            artifacts=artifacts,
            metrics=metrics,
            error=terminate_reason,
        )

    if rc != 0:
        tail_excerpt = "\n".join(list(tail)[-20:]) if tail else ""
        return StepResult(
            status="failed",
            artifacts=artifacts,
            metrics=metrics,
            error=f"process exited with code {rc}\n{tail_excerpt}".strip(),
        )

    return StepResult(
        status="completed",
        artifacts=artifacts,
        metrics=metrics,
    )


# Register the built-in kind at import time
register_step_kind("external", _run_external)
