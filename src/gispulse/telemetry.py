"""
GISPulse opt-in telemetry — anonymous usage statistics.

Privacy:
    - NO file paths, data content, IP addresses, API keys, dataset names, or rule details
    - Only anonymous metadata: version, OS, engine mode, command name, duration
    - Opt-in: explicit consent required at first launch
    - Override: GISPULSE_TELEMETRY=0 disables unconditionally
    - Manage: `gispulse telemetry --status / --enable / --disable`

Architecture:
    - Standalone module, zero external dependencies (uses urllib.request)
    - Non-blocking: daemon thread for batch sends
    - Fail-silent: telemetry never crashes the host process
"""

from __future__ import annotations

import atexit
import json
import os
import platform
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path("~/.gispulse").expanduser()
_CONFIG_FILE = _CONFIG_DIR / "telemetry.json"
_DEFAULT_ENDPOINT = "https://telemetry.gispulse.dev/v1/events"
_FLUSH_INTERVAL_SECONDS = 300  # 5 minutes
_SEND_TIMEOUT_SECONDS = 5

# ---------------------------------------------------------------------------
# Consent management
# ---------------------------------------------------------------------------


def _read_config() -> dict[str, Any]:
    """Read telemetry config from disk. Returns empty dict on any error."""
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_config(config: dict[str, Any]) -> None:
    """Persist telemetry config to disk."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def is_enabled() -> bool:
    """Return True if telemetry is currently enabled.

    Priority:
        1. GISPULSE_TELEMETRY env var (0 = disabled, 1 = enabled)
        2. ~/.gispulse/telemetry.json {"enabled": bool}
        3. False (no config = not yet opted in)
    """
    env = os.environ.get("GISPULSE_TELEMETRY")
    if env is not None:
        return env.strip() not in ("0", "false", "no", "off", "")
    config = _read_config()
    return bool(config.get("enabled", False))


def has_been_asked() -> bool:
    """Return True if the user has already been prompted for consent."""
    return _CONFIG_FILE.exists()


def set_enabled(enabled: bool) -> None:
    """Persist telemetry preference."""
    config = _read_config()
    config["enabled"] = enabled
    config["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_config(config)


def get_status() -> str:
    """Human-readable telemetry status string."""
    env = os.environ.get("GISPULSE_TELEMETRY")
    if env is not None:
        state = "enabled" if env.strip() not in ("0", "false", "no", "off", "") else "disabled"
        return f"Telemetry is {state} (via GISPULSE_TELEMETRY env var)"

    config = _read_config()
    if "enabled" not in config:
        return "Telemetry has not been configured yet"
    state = "enabled" if config["enabled"] else "disabled"
    updated = config.get("updated_at", "unknown")
    return f"Telemetry is {state} (configured at {updated})"


# ---------------------------------------------------------------------------
# Consent prompt (first launch)
# ---------------------------------------------------------------------------

_CONSENT_MESSAGE = """\
GISPulse collects anonymous usage statistics to improve the product.

What is collected:
  - GISPulse version, Python version (major.minor), OS name/arch
  - Engine mode (duckdb/postgis/hybrid)
  - Capability names used (no data content)
  - Dataset size bucket (small/medium/large/xlarge)
  - CLI command name and duration (rounded to the second)

What is NEVER collected:
  - File paths, data content, IP addresses, API keys
  - Dataset names, rule details, or any personal information

You can change this anytime with:
  gispulse telemetry --disable
  gispulse telemetry --enable
  GISPULSE_TELEMETRY=0  (env var override)
"""


def prompt_consent() -> bool:
    """Prompt the user for telemetry consent. Returns the choice.

    If stdin is not a TTY (CI, pipes), defaults to disabled without prompting.
    """
    if not sys.stdin.isatty():
        set_enabled(False)
        return False

    print(_CONSENT_MESSAGE)
    try:
        answer = input("Enable anonymous telemetry? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        answer = "n"

    enabled = answer in ("y", "yes")
    set_enabled(enabled)
    if enabled:
        print("Telemetry enabled. Thank you!")
    else:
        print("Telemetry disabled.")
    return enabled


def ensure_consent() -> None:
    """Check consent on first launch. No-op if already asked or env var set."""
    if os.environ.get("GISPULSE_TELEMETRY") is not None:
        return
    if not has_been_asked():
        prompt_consent()


# ---------------------------------------------------------------------------
# Event construction helpers
# ---------------------------------------------------------------------------


def _get_gispulse_version() -> str:
    try:
        from importlib.metadata import version as pkg_version
        return pkg_version("gispulse")
    except Exception:
        return "0.1.0-dev"


def _get_python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _get_os_name() -> str:
    name = platform.system().lower()
    return {"linux": "linux", "darwin": "macos", "windows": "windows"}.get(name, name)


def _get_os_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return machine


def _size_bucket(size_bytes: int | None) -> str:
    """Classify file size into an anonymous bucket."""
    if size_bytes is None:
        return "unknown"
    mb = size_bytes / (1024 * 1024)
    if mb < 1:
        return "small"
    if mb < 100:
        return "medium"
    if mb < 1024:
        return "large"
    return "xlarge"


def build_event(
    command: str,
    duration_seconds: float | None = None,
    engine_mode: str | None = None,
    capabilities_used: list[str] | None = None,
    dataset_size_bytes: int | None = None,
) -> dict[str, Any]:
    """Build a single telemetry event dict."""
    event: dict[str, Any] = {
        "gispulse_version": _get_gispulse_version(),
        "python_version": _get_python_version(),
        "os_name": _get_os_name(),
        "os_arch": _get_os_arch(),
        "command": command,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if duration_seconds is not None:
        event["duration_seconds"] = round(duration_seconds)
    if engine_mode is not None:
        event["engine_mode"] = engine_mode
    if capabilities_used is not None:
        event["capabilities_used"] = list(capabilities_used)
    if dataset_size_bytes is not None:
        event["dataset_size_bucket"] = _size_bucket(dataset_size_bytes)
    return event


# ---------------------------------------------------------------------------
# Event collector & sender (singleton)
# ---------------------------------------------------------------------------


class _TelemetryCollector:
    """Accumulates events and sends them in batches.

    Thread-safe. Sends are done on a daemon thread so they never block
    the main process. If sending fails, events are silently dropped.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._timer: threading.Timer | None = None
        self._endpoint = os.environ.get("GISPULSE_TELEMETRY_URL", _DEFAULT_ENDPOINT)
        self._registered_atexit = False

    def record(self, event: dict[str, Any]) -> None:
        """Add an event to the batch. Starts the flush timer if needed."""
        if not is_enabled():
            return
        with self._lock:
            self._events.append(event)
            if not self._registered_atexit:
                atexit.register(self._flush_sync)
                self._registered_atexit = True
            if self._timer is None:
                self._timer = threading.Timer(_FLUSH_INTERVAL_SECONDS, self._flush_async)
                self._timer.daemon = True
                self._timer.start()

    def _flush_async(self) -> None:
        """Flush on a daemon thread (non-blocking)."""
        self._do_flush()

    def _flush_sync(self) -> None:
        """Flush at process exit (best-effort)."""
        self._do_flush()

    def _do_flush(self) -> None:
        with self._lock:
            if not self._events:
                return
            batch = self._events[:]
            self._events.clear()
            self._timer = None

        self._send(batch)

    def _send(self, events: list[dict[str, Any]]) -> None:
        """POST events to the telemetry endpoint. Fail silently."""
        try:
            payload = json.dumps(events).encode("utf-8")
            req = urllib.request.Request(
                self._endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=_SEND_TIMEOUT_SECONDS)
        except Exception:
            pass  # Fail silently — no retry, no queue


# Module-level singleton
_collector = _TelemetryCollector()


def record(event: dict[str, Any]) -> None:
    """Record a telemetry event (no-op if telemetry is disabled)."""
    try:
        _collector.record(event)
    except Exception:
        pass  # Never crash


def flush() -> None:
    """Force-flush pending events. Called at shutdown."""
    try:
        _collector._do_flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI command decorator
# ---------------------------------------------------------------------------


def track_command(command_name: str):
    """Decorator that records telemetry for a CLI command.

    Usage::

        @track_command("run")
        def run_command(...):
            ...

    Never slows down or crashes the wrapped function.
    """
    import functools

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                return func(*args, **kwargs)
            finally:
                try:
                    duration = time.monotonic() - start
                    event = build_event(
                        command=command_name,
                        duration_seconds=duration,
                    )
                    record(event)
                except Exception:
                    pass
        return wrapper
    return decorator
