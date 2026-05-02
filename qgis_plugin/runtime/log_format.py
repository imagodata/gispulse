"""Pure-Python log helpers for the Attach-trigger runner.

Lives in its own module so the parsing/colouring logic can be unit
tested without pulling Qt. The dock widget consumes these to render
streamed lines in a `QTextEdit`.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime
from pathlib import Path


class LogLevel(enum.Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


# Hex colours chosen so the same values look reasonable on QGIS' light
# AND dark themes. Not WCAG AAA but acceptable for ephemeral console
# output; see PaletteFor follow-up if a11y complaints surface.
LEVEL_COLOR: dict[LogLevel, str] = {
    LogLevel.INFO: "#1f8a3a",
    LogLevel.WARN: "#c97a00",
    LogLevel.ERROR: "#b00020",
}


# Match the structlog/json formats gispulse emits, plus plain `LEVEL:`
# prefixes from third-party libraries. The order matters: we check the
# more specific patterns first.
_PATTERNS: tuple[tuple[re.Pattern[str], LogLevel], ...] = (
    (re.compile(r"\[(error|err|critical|fatal)\]", re.IGNORECASE), LogLevel.ERROR),
    (re.compile(r"\[(warn|warning)\]", re.IGNORECASE), LogLevel.WARN),
    (re.compile(r"\b(error|critical|fatal|traceback)\b[: ]", re.IGNORECASE), LogLevel.ERROR),
    (re.compile(r"\b(warn|warning)\b[: ]", re.IGNORECASE), LogLevel.WARN),
)


def parse_log_level(line: str) -> LogLevel:
    """Best-effort detection of the level for a single stdout/stderr line.

    Defaults to INFO when no marker is present so plain progress lines
    don't drown the user in red. The runner uses `is_stderr=True` to
    upgrade unknown-level stderr lines to ERROR — that decision sits in
    the caller, not here, since stderr is sometimes used by gispulse for
    non-error structured output.
    """
    for pattern, level in _PATTERNS:
        if pattern.search(line):
            return level
    return LogLevel.INFO


def format_log_html(line: str, level: LogLevel) -> str:
    """Render a single line as colour-tagged HTML for `QTextEdit.append()`.

    HTML special chars are escaped because gispulse may surface arbitrary
    user dataset names (and `<`, `>` in WKT-ish output) which would
    otherwise be interpreted as tags by Qt's rich-text engine.
    """
    safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").rstrip("\n")
    color = LEVEL_COLOR[level]
    return f'<span style="color:{color}; white-space:pre">{safe}</span>'


def log_file_path(project_dir: str | Path, *, now: datetime | None = None) -> Path:
    """Return `<project_dir>/.gispulse/runs/<UTC-timestamp>.log`.

    The directory is *not* created here so the function stays pure and
    testable. The runner does the `mkdir(parents=True, exist_ok=True)`
    immediately before opening the handle.
    """
    stamp = (now or datetime.utcnow()).strftime("%Y%m%dT%H%M%SZ")
    return Path(project_dir) / ".gispulse" / "runs" / f"{stamp}.log"
