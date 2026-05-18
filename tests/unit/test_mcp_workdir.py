"""Tests for the MCP filesystem-scoping helper (issue #204).

``gispulse.adapters.mcp.workdir`` bounds every path an MCP tool reads to
the configured MCP workdir, so a prompt-injected agent cannot escape it.
These tests do not need fastmcp — the helper has no FastMCP dependency.
"""

from __future__ import annotations


import pytest

from gispulse.adapters.mcp.workdir import (
    WorkdirError,
    get_workdir,
    resolve_in_workdir,
)


def test_get_workdir_defaults_to_cwd(monkeypatch, tmp_path):
    """Without the env var, the workdir is the process cwd."""
    monkeypatch.delenv("GISPULSE_MCP_WORKDIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert get_workdir() == tmp_path.resolve()


def test_get_workdir_honours_env_var(monkeypatch, tmp_path):
    """GISPULSE_MCP_WORKDIR overrides the cwd."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    assert get_workdir() == tmp_path.resolve()


def test_resolve_accepts_file_inside_workdir(monkeypatch, tmp_path):
    """A path inside the workdir resolves to its canonical absolute form."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    target = tmp_path / "data.gpkg"
    target.write_text("x", encoding="utf-8")
    resolved = resolve_in_workdir("data.gpkg")
    assert resolved == target.resolve()


def test_resolve_accepts_nested_path(monkeypatch, tmp_path):
    """A nested path inside the workdir is accepted."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    nested = tmp_path / "configs"
    nested.mkdir()
    target = nested / "triggers.yaml"
    target.write_text("x", encoding="utf-8")
    assert resolve_in_workdir("configs/triggers.yaml") == target.resolve()


def test_resolve_rejects_parent_traversal(monkeypatch, tmp_path):
    """A ``../`` escape is refused with a WorkdirError."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    with pytest.raises(WorkdirError, match="outside MCP workdir"):
        resolve_in_workdir("../../../etc/passwd")


def test_resolve_rejects_absolute_path_outside(monkeypatch, tmp_path):
    """An absolute path outside the workdir is refused."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    with pytest.raises(WorkdirError, match="outside MCP workdir"):
        resolve_in_workdir("/etc/passwd")


def test_resolve_rejects_missing_file_when_must_exist(monkeypatch, tmp_path):
    """A missing path inside the workdir fails when must_exist is set."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    with pytest.raises(WorkdirError, match="not found"):
        resolve_in_workdir("does_not_exist.gpkg")


def test_resolve_allows_missing_file_when_not_required(monkeypatch, tmp_path):
    """must_exist=False resolves a not-yet-created path inside the workdir."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    resolved = resolve_in_workdir("future.gpkg", must_exist=False)
    assert resolved == (tmp_path / "future.gpkg").resolve()


def test_resolve_rejects_symlink_escape(monkeypatch, tmp_path):
    """A symlink pointing outside the workdir is caught after resolution."""
    monkeypatch.setenv("GISPULSE_MCP_WORKDIR", str(tmp_path))
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - platform
        pytest.skip("symlinks unsupported on this platform")
    with pytest.raises(WorkdirError, match="outside MCP workdir"):
        resolve_in_workdir("link.txt")
