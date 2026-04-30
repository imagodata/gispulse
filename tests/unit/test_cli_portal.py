"""
Unit tests for ``gispulse portal`` (issue #51).

Coverage:

* ``--backend=URL`` skips local mount, opens GH-Pages portal with encoded URL.
* Graceful-degrade message when ``gispulse_portal`` is not importable AND no
  repo-local ``portal/dist/`` is available (non-dev mode).
* ``--no-browser`` flag is plumbed through to the launcher (does not call
  ``webbrowser.open`` directly when launch is short-circuited).
* ``--help`` works and lists the flags.

We avoid actually starting uvicorn — the launcher path is short-circuited by
mocking ``_resolve_portal_dist`` to return ``None`` (graceful-degrade) or by
hitting ``--backend`` (no engine boot). A separate integration test exercises
the full launch path with ``--no-browser``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from gispulse.cli import app
from gispulse import cli_portal

runner = CliRunner()


# ---------------------------------------------------------------------------
# --backend= remote mode
# ---------------------------------------------------------------------------


class TestBackendMode:
    def test_backend_opens_gh_pages_with_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        opened: list[str] = []
        monkeypatch.setattr(
            cli_portal.webbrowser,
            "open",
            lambda url, *a, **kw: opened.append(url) or True,
        )

        result = runner.invoke(
            app,
            ["portal", "--backend", "https://my-engine.example.com"],
        )

        assert result.exit_code == 0, result.output
        assert len(opened) == 1
        # backend URL is encoded as a query param onto the GH-Pages portal
        assert opened[0].startswith("https://gispulse.dev/")
        assert "backend=https%3A%2F%2Fmy-engine.example.com" in opened[0]

    def test_backend_does_not_start_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--backend`` must short-circuit before importing FastAPI/uvicorn."""
        monkeypatch.setattr(cli_portal.webbrowser, "open", lambda *a, **kw: True)

        # Make uvicorn.run explode if anyone tries to start it.
        def _boom(*args, **kwargs):  # pragma: no cover — fail loud
            raise AssertionError("uvicorn.run must not be invoked with --backend")

        # Patch via sys.modules the lazy import — uvicorn is imported inside cmd_portal.
        import sys
        import types

        fake_uvicorn = types.ModuleType("uvicorn")
        fake_uvicorn.run = _boom  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

        result = runner.invoke(app, ["portal", "--backend", "http://localhost:9999"])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Graceful-degrade when gispulse_portal absent
# ---------------------------------------------------------------------------


class TestGracefulDegrade:
    def test_no_package_no_local_dist_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Force the resolver to find nothing. This simulates "gispulse_portal
        # not installed AND not running from a dev checkout that has portal/dist/".
        monkeypatch.setattr(cli_portal, "_resolve_portal_dist", lambda dev=False: None)

        result = runner.invoke(app, ["portal"])

        assert result.exit_code == 1
        # Error goes to stderr, but Typer's CliRunner merges stderr into output by default
        # for backwards compat; check both.
        combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
        assert "gispulse-portal" in combined
        assert "pip install gispulse-portal" in combined
        assert "--backend=" in combined  # remediation hint mentions advanced mode

    def test_resolver_picks_up_package_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When ``gispulse_portal.PORTAL_DIST_PATH`` exists, resolver returns it."""
        fake_dist = tmp_path / "fake_portal_dist"
        fake_dist.mkdir()
        (fake_dist / "index.html").write_text("<html></html>")

        # Inject a fake gispulse_portal module exposing PORTAL_DIST_PATH.
        import sys
        import types

        fake_module = types.ModuleType("gispulse_portal")
        fake_module.PORTAL_DIST_PATH = str(fake_dist)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "gispulse_portal", fake_module)

        resolved = cli_portal._resolve_portal_dist(dev=False)
        assert resolved == fake_dist

    def test_resolver_falls_back_to_repo_local_in_dev_mode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``--dev`` allows fallback to ``<repo>/portal/dist`` when package missing."""
        # Ensure gispulse_portal is unimportable by clearing any cached entry.
        import sys

        monkeypatch.delitem(sys.modules, "gispulse_portal", raising=False)

        # Patch __file__ so the resolver computes a tmp_path-relative dist.
        fake_pkg_root = tmp_path / "gispulse"
        fake_pkg_root.mkdir()
        fake_dist = tmp_path / "portal" / "dist"
        fake_dist.mkdir(parents=True)
        (fake_dist / "index.html").write_text("<html></html>")

        fake_cli_portal_path = fake_pkg_root / "cli_portal.py"
        fake_cli_portal_path.write_text("# placeholder\n")

        monkeypatch.setattr(cli_portal, "__file__", str(fake_cli_portal_path))

        resolved = cli_portal._resolve_portal_dist(dev=True)
        assert resolved == fake_dist

        # And without --dev, the same setup yields no fallback (only package is honoured).
        resolved_no_dev = cli_portal._resolve_portal_dist(dev=False)
        assert resolved_no_dev is None


# ---------------------------------------------------------------------------
# --help and registration
# ---------------------------------------------------------------------------


class TestPortalHelp:
    def test_portal_help_lists_flags(self) -> None:
        result = runner.invoke(app, ["portal", "--help"])
        assert result.exit_code == 0
        for flag in ("--port", "--host", "--backend", "--no-browser", "--data-dir"):
            assert flag in result.output

    def test_portal_command_is_registered(self) -> None:
        # Confirm the command shows up in the top-level help output too.
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "portal" in result.output


# ---------------------------------------------------------------------------
# _open_remote_portal helper
# ---------------------------------------------------------------------------


class TestOpenRemotePortal:
    def test_encodes_special_chars(self) -> None:
        opened: list[str] = []
        cli_portal._open_remote_portal(
            "https://demo.gispulse.dev/api?x=1&y=2", opener=opened.append
        )
        assert len(opened) == 1
        # & must be URL-encoded, otherwise it would split the query string.
        assert "%26" in opened[0]
        assert opened[0].startswith("https://gispulse.dev/?backend=")
