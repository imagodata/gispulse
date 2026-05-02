"""Locate the gispulse CLI from inside QGIS' embedded Python.

QGIS ships its own Python interpreter (OSGeo4W on Windows, the standalone
installer's bundle on Linux/Windows, Homebrew on macOS) which usually does
NOT have `gispulse` on its `sys.path`. The plugin therefore shells out to
the user-level CLI; this module finds it and reports a clear, OS-specific
install hint when it isn't installed or is too old.

The detection logic is intentionally Qt-free so it can be unit-tested
outside QGIS. The companion `error_dialog` module turns a `DetectorResult`
into a `QMessageBox`.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Min CLI version that exposes the runtime contract this plugin scaffold
# relies on. Bumped lockstep with `pyproject.toml` major changes.
MIN_VERSION: Final[tuple[int, int, int]] = (1, 3, 0)

# Detection result is cached for the lifetime of the QGIS process to avoid
# re-running subprocesses on every dock-widget open. Use `clear_cache()` to
# force a re-check (wired to the "Test again" button in the dialog).
_CACHE: list["DetectorResult"] = []


@dataclass(frozen=True)
class DetectorResult:
    found: bool
    path: str | None
    version: tuple[int, int, int] | None
    error: str | None

    @property
    def version_str(self) -> str:
        if self.version is None:
            return "unknown"
        return ".".join(str(p) for p in self.version)


def clear_cache() -> None:
    """Forget the last detection. The next `detect_gispulse()` call will
    re-run all subprocesses. Wired to the "Test again" button (#v1.4-3).
    """
    _CACHE.clear()


def detect_gispulse(*, use_cache: bool = True) -> DetectorResult:
    """Locate `gispulse` and verify its version >= MIN_VERSION.

    Probes (in order) until one succeeds:
        1. `shutil.which("gispulse")` — most install paths land here
        2. `~/.local/bin/gispulse` — `pip install --user` / `pipx`
        3. `<sys.executable> -m gispulse --version` — same Python QGIS uses

    If no probe finds a working CLI, the *first informative* failure is
    bubbled up (e.g. timeout, version-too-old, non-zero exit) so the user
    gets actionable diagnostics instead of a generic "not found".
    """
    if use_cache and _CACHE:
        return _CACHE[0]
    informative: DetectorResult | None = None
    for probe in (_probe_path, _probe_user_local, _probe_module):
        result = probe()
        if result.found:
            _CACHE.append(result)
            return result
        if informative is None and result.error:
            informative = result
    final = informative or DetectorResult(
        found=False,
        path=None,
        version=None,
        error="gispulse executable not found on PATH, ~/.local/bin, or as a Python module.",
    )
    _CACHE.append(final)
    return final


def install_hint(os_name: str | None = None) -> str:
    """Return an OS-specific, copy-pasteable install command."""
    name = (os_name or platform.system()).lower()
    if "windows" in name:
        return (
            "Open the OSGeo4W Shell (Start → OSGeo4W) and run:\n"
            "    pip install gispulse\n"
            "Or install the standalone CLI from https://gispulse.dev/install"
        )
    if "darwin" in name or "mac" in name:
        return (
            "Run in Terminal:\n"
            "    brew install pipx && pipx install gispulse\n"
            "Or, if you prefer pip:\n"
            "    pip3 install --user gispulse"
        )
    return "Run in your shell:\n    pipx install gispulse\nOr:\n    pip install --user gispulse"


# ──────────────────────────── probes ───────────────────────────────


def _probe_path() -> DetectorResult:
    exe = shutil.which("gispulse")
    if not exe:
        return _not_found()
    return _verify(exe, [exe, "--version"])


def _probe_user_local() -> DetectorResult:
    candidate = Path(os.path.expanduser("~/.local/bin/gispulse"))
    if not candidate.is_file():
        return _not_found()
    return _verify(str(candidate), [str(candidate), "--version"])


def _probe_module() -> DetectorResult:
    return _verify(
        f"{sys.executable} -m gispulse",
        [sys.executable, "-m", "gispulse", "--version"],
    )


# ──────────────────────────── helpers ──────────────────────────────


def _not_found() -> DetectorResult:
    return DetectorResult(found=False, path=None, version=None, error=None)


def _verify(display_path: str, cmd: list[str]) -> DetectorResult:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return DetectorResult(
            found=False, path=None, version=None, error=f"failed to invoke {cmd[0]!r}: {exc}"
        )
    if proc.returncode != 0:
        return DetectorResult(
            found=False,
            path=None,
            version=None,
            error=f"`{' '.join(cmd)}` exited with {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}",
        )
    version = _parse_version(proc.stdout) or _parse_version(proc.stderr)
    if version is None:
        return DetectorResult(
            found=False,
            path=None,
            version=None,
            error=f"could not parse version from output: {proc.stdout.strip() or proc.stderr.strip()!r}",
        )
    if version < MIN_VERSION:
        return DetectorResult(
            found=False,
            path=display_path,
            version=version,
            error=(
                f"gispulse {_v(version)} is installed at {display_path} but the plugin "
                f"requires >= {_v(MIN_VERSION)}. Upgrade with: pip install --upgrade gispulse"
            ),
        )
    return DetectorResult(found=True, path=display_path, version=version, error=None)


def _parse_version(text: str) -> tuple[int, int, int] | None:
    """Pull the first `MAJOR.MINOR.PATCH` triple from arbitrary CLI output.

    Accepts `gispulse 1.5.1` (Typer default), a lone `1.5.1`, and `v2.0.0`
    (no word boundary before the leading digit when prefixed by a letter).
    """
    import re

    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _v(parts: tuple[int, int, int]) -> str:
    return ".".join(str(p) for p in parts)
