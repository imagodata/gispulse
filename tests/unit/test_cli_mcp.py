"""Tests for ``gispulse mcp`` (issue #201)."""

from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from gispulse.cli import app

runner = CliRunner()


def test_mcp_rejects_non_stdio_transport() -> None:
    """v1.7.0 ships stdio only — an HTTP request fails cleanly."""
    result = runner.invoke(app, ["mcp", "--transport", "http"])
    assert result.exit_code == 1
    assert "only 'stdio'" in result.output


def test_mcp_launches_stdio_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """``gispulse mcp`` builds the server and runs its stdio transport."""
    runs: list[bool] = []

    class FakeServer:
        def run(self) -> None:
            runs.append(True)

    import gispulse.adapters.mcp.server as srv

    monkeypatch.setattr(srv, "create_mcp_server", lambda *a, **k: FakeServer())
    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 0
    assert runs == [True]


def test_mcp_reports_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clear message when the optional 'mcp' extra is not installed."""
    # Simulate the import failing inside cmd_mcp.
    monkeypatch.setitem(sys.modules, "gispulse.adapters.mcp.server", None)
    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 1
    assert "pip install 'gispulse[mcp]'" in result.output
