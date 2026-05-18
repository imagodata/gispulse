"""Filesystem scoping for the MCP server (issue #204).

Every MCP tool that takes a path argument — ``inspect_dataset``,
``inspect_changelog``, ``load_triggers``, ``validate_triggers``,
``list_triggers``, ``dryrun_trigger``, ``watch_status`` — reads a path
the model picked. An unbounded ``open(path)`` is a path-traversal sink:
a prompt-injected agent could read ``/etc/passwd`` or exfiltrate any
GeoPackage on the host.

This module bounds every such read to a single **MCP workdir**:

* ``GISPULSE_MCP_WORKDIR`` env var when set;
* otherwise the process current working directory.

:func:`resolve_in_workdir` canonicalises a caller-supplied path with
``Path.resolve()`` and rejects it when it escapes the workdir. The check
reuses :func:`gispulse.runtime.config_loader._check_within_anchors` — the
same path-traversal guard the ``gispulse triggers`` CLI already trusts —
so there is one canonical implementation of "is this path inside an
allowed root".

Note this is intentionally *stricter* than the CLI's
:func:`config_loader._safe_anchors` (which accepts cwd **and** ``$HOME``
**and** ``tempfile.gettempdir()``): an MCP server is driven by an
untrusted LLM, so it gets a single, explicit root.
"""

from __future__ import annotations

import os
from pathlib import Path

from gispulse.runtime.config_loader import ConfigError, _check_within_anchors

__all__ = ["WorkdirError", "get_workdir", "resolve_in_workdir"]


class WorkdirError(ValueError):
    """Raised when a caller-supplied path escapes the MCP workdir."""


def get_workdir() -> Path:
    """Return the directory MCP path arguments are bounded to.

    ``GISPULSE_MCP_WORKDIR`` when set (``~`` expanded), otherwise the
    process current working directory. The returned path is resolved so
    later containment checks compare canonical paths.
    """
    raw = os.environ.get("GISPULSE_MCP_WORKDIR", "").strip()
    base = Path(raw).expanduser() if raw else Path.cwd()
    try:
        return base.resolve()
    except OSError:  # pragma: no cover - defensive
        return base


def resolve_in_workdir(path: str | os.PathLike[str], *, must_exist: bool = True) -> Path:
    """Canonicalise ``path`` and ensure it stays inside the MCP workdir.

    Args:
        path:       A path string from an MCP tool argument. Relative
                    paths are anchored to the workdir, not to the
                    process cwd, so the model never depends on an
                    implicit location.
        must_exist: When *True*, also fail if the target is missing.

    Returns:
        The resolved absolute :class:`~pathlib.Path`.

    Raises:
        WorkdirError: The path escapes the workdir, or (when
                      ``must_exist``) does not exist.
    """
    workdir = get_workdir()
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = workdir / p
    try:
        resolved = p.resolve()
    except OSError as exc:  # pragma: no cover - defensive
        raise WorkdirError(f"cannot resolve path {path!s}: {exc}") from exc

    # Reuse the CLI's traversal guard so there is a single implementation.
    try:
        _check_within_anchors(resolved, [workdir])
    except ConfigError as exc:
        raise WorkdirError(
            f"path outside MCP workdir: {path!s} (workdir: {workdir})"
        ) from exc

    if must_exist and not resolved.exists():
        raise WorkdirError(f"path not found in MCP workdir: {path!s}")
    return resolved
