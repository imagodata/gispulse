"""Tests for plugin discovery via entry-points and marketplace CLI commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Registry: _discover_plugins
# ---------------------------------------------------------------------------


class TestDiscoverPlugins:
    """Test capabilities.registry._discover_plugins."""

    def test_discover_no_plugins(self):
        """When no entry-points exist, discovery returns empty list."""
        from capabilities.registry import _discover_plugins

        with patch("importlib.metadata.entry_points", return_value=[]):
            result = _discover_plugins()
        assert result == []

    def test_discover_loads_plugin(self):
        """Valid entry-point is loaded and called."""
        from capabilities.registry import _discover_plugins

        ep = MagicMock()
        ep.name = "test_plugin"
        ep.value = "gispulse_cap_test:register"
        register_fn = MagicMock()
        ep.load.return_value = register_fn

        with patch("importlib.metadata.entry_points", return_value=[ep]):
            result = _discover_plugins()

        register_fn.assert_called_once()
        assert len(result) == 1
        assert result[0]["name"] == "test_plugin"
        assert result[0]["status"] == "ok"

    def test_discover_handles_load_failure(self):
        """If a plugin's register() raises, discovery logs warning and continues."""
        from capabilities.registry import _discover_plugins

        ep = MagicMock()
        ep.name = "bad_plugin"
        ep.value = "gispulse_cap_bad:register"
        ep.load.return_value = MagicMock(side_effect=ImportError("missing dep"))

        with patch("importlib.metadata.entry_points", return_value=[ep]):
            result = _discover_plugins()

        assert len(result) == 1
        assert result[0]["name"] == "bad_plugin"
        assert "error" in result[0]["status"]

    def test_discover_handles_importlib_failure(self):
        """If importlib.metadata itself fails, discovery returns empty."""
        from capabilities.registry import _discover_plugins

        with patch(
            "importlib.metadata.entry_points",
            side_effect=Exception("broken"),
        ):
            result = _discover_plugins()

        assert result == []

    def test_discover_multiple_plugins(self):
        """Multiple entry-points are all loaded."""
        from capabilities.registry import _discover_plugins

        eps = []
        for name in ("alpha", "beta", "gamma"):
            ep = MagicMock()
            ep.name = name
            ep.value = f"gispulse_cap_{name}:register"
            ep.load.return_value = MagicMock()
            eps.append(ep)

        with patch("importlib.metadata.entry_points", return_value=eps):
            result = _discover_plugins()

        assert len(result) == 3
        assert all(r["status"] == "ok" for r in result)


class TestListPlugins:
    """Test capabilities.registry.list_plugins."""

    def test_list_plugins_empty(self):
        from capabilities.registry import list_plugins

        with patch("importlib.metadata.entry_points", return_value=[]):
            result = list_plugins()
        assert result == []

    def test_list_plugins_returns_metadata(self):
        from capabilities.registry import list_plugins

        ep = MagicMock()
        ep.name = "ftth"
        ep.value = "gispulse_cap_ftth:register"

        with patch("importlib.metadata.entry_points", return_value=[ep]):
            result = list_plugins()

        assert len(result) == 1
        assert result[0] == {"name": "ftth", "module": "gispulse_cap_ftth:register"}


# ---------------------------------------------------------------------------
# CLI: marketplace commands
# ---------------------------------------------------------------------------


class TestMarketplaceCLI:
    """Test marketplace CLI commands via typer.testing."""

    @pytest.fixture()
    def runner(self):
        from typer.testing import CliRunner

        from gispulse.cli import app

        return CliRunner(), app

    def test_marketplace_list_empty(self, runner):
        cli_runner, app = runner
        with patch("capabilities.registry.list_plugins", return_value=[]):
            result = cli_runner.invoke(app, ["marketplace", "list"])
        assert result.exit_code == 0
        assert "No plugins installed" in result.output

    def test_marketplace_list_with_plugins(self, runner):
        cli_runner, app = runner
        plugins = [
            {"name": "ftth", "module": "gispulse_cap_ftth:register"},
            {"name": "urban", "module": "gispulse_cap_urban:register"},
        ]
        with patch("capabilities.registry.list_plugins", return_value=plugins):
            result = cli_runner.invoke(app, ["marketplace", "list"])
        assert result.exit_code == 0
        assert "2 plugin(s)" in result.output
        assert "ftth" in result.output
        assert "urban" in result.output

    def test_marketplace_info_not_installed(self, runner):
        cli_runner, app = runner
        result = cli_runner.invoke(app, ["marketplace", "info", "nonexistent"])
        assert result.exit_code == 1
        assert "not installed" in result.output

    def test_marketplace_install_success(self, runner):
        cli_runner, app = runner
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cli_runner.invoke(app, ["marketplace", "install", "ftth"])
        assert result.exit_code == 0
        assert "Installed" in result.output
        # Verify pip was called with the right package
        call_args = mock_run.call_args[0][0]
        assert "gispulse-cap-ftth" in call_args

    def test_marketplace_install_failure(self, runner):
        cli_runner, app = runner
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="No matching distribution"
            )
            result = cli_runner.invoke(app, ["marketplace", "install", "nonexistent"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_marketplace_uninstall_success(self, runner):
        cli_runner, app = runner
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cli_runner.invoke(app, ["marketplace", "uninstall", "ftth"])
        assert result.exit_code == 0
        assert "Uninstalled" in result.output

    def test_marketplace_install_with_prefix(self, runner):
        """If user passes full package name, don't double-prefix."""
        cli_runner, app = runner
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cli_runner.invoke(
                app, ["marketplace", "install", "gispulse-cap-ftth"]
            )
        assert result.exit_code == 0
        call_args = mock_run.call_args[0][0]
        # Should NOT be gispulse-cap-gispulse-cap-ftth
        assert "gispulse-cap-ftth" in call_args
        assert "gispulse-cap-gispulse-cap" not in " ".join(call_args)
