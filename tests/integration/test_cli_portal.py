"""
Integration test for ``gispulse portal --no-browser`` (issue #51).

Validates the local-mode launch path end-to-end up to (but not including)
``uvicorn.run``:

* ``gispulse_portal`` package is resolved via a tmp dist directory.
* ``create_app(mode="portal")`` is called.
* The bundled SPA is mounted on ``/portal``.
* ``--no-browser`` skips the healthcheck/open thread (no daemon thread is
  spawned, no ``webbrowser.open`` call).
* ``uvicorn.run`` is invoked with the configured host/port.

Boots no real socket — uvicorn is monkey-patched.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from typer.testing import CliRunner

from gispulse.cli import app
from gispulse import cli_portal

runner = CliRunner()


@pytest.fixture
def fake_portal_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Inject a fake ``gispulse_portal`` package exposing PORTAL_DIST_PATH."""
    dist = tmp_path / "portal_dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><body>portal</body></html>"
    )
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("// fake bundle\n")

    fake_module = types.ModuleType("gispulse_portal")
    fake_module.PORTAL_DIST_PATH = str(dist)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gispulse_portal", fake_module)
    return dist


def test_portal_no_browser_mounts_spa_and_skips_browser(
    monkeypatch: pytest.MonkeyPatch,
    fake_portal_package: Path,
    tmp_path: Path,
) -> None:
    """``gispulse portal --no-browser`` mounts the SPA and never opens a browser."""
    captured: dict = {}

    # --- Stub uvicorn.run -------------------------------------------------
    fake_uvicorn = types.ModuleType("uvicorn")

    def fake_run(app_arg, host: str, port: int, log_level: str = "info", **kwargs):
        # FastAPI is what cmd_portal builds and hands off; capture for assertions.
        captured["app"] = app_arg
        captured["host"] = host
        captured["port"] = port

    fake_uvicorn.run = fake_run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    # --- Forbid any browser open / healthcheck thread spawn --------------
    def _no_open(*args, **kwargs):  # pragma: no cover — fail loud
        raise AssertionError("--no-browser must not call webbrowser.open")

    monkeypatch.setattr(cli_portal.webbrowser, "open", _no_open)

    # Track healthcheck thread spawns by intercepting the *target* used by
    # cmd_portal — patching ``threading.Thread`` globally would explode every
    # internal thread created by FastAPI's lifespan startup. We swap
    # ``_healthcheck_then_open`` instead so the assertion is targeted.
    healthcheck_calls: list = []

    def _track_healthcheck(*args, **kwargs):  # pragma: no cover — fail loud
        healthcheck_calls.append((args, kwargs))
        raise AssertionError("--no-browser must not invoke healthcheck/open")

    monkeypatch.setattr(cli_portal, "_healthcheck_then_open", _track_healthcheck)

    # --- Invoke ----------------------------------------------------------
    result = runner.invoke(
        app,
        [
            "portal",
            "--no-browser",
            "--port",
            "8765",
            "--host",
            "127.0.0.1",
            "--data-dir",
            str(tmp_path / "data"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert healthcheck_calls == []  # no browser/healthcheck spawned

    # Uvicorn was called with the expected wiring.
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    fastapi_app = captured["app"]
    assert isinstance(fastapi_app, FastAPI)

    # The bundled SPA was mounted at /portal.
    portal_routes = [
        r for r in fastapi_app.routes if getattr(r, "name", None) == "portal-bundled"
    ]
    assert len(portal_routes) == 1, (
        f"expected 1 mount named 'portal-bundled', got {len(portal_routes)}: "
        f"{[getattr(r, 'name', '?') for r in fastapi_app.routes]}"
    )

    # Output references the SPA dist path so the user knows what is being served.
    assert "Serving SPA from" in result.output
    assert str(fake_portal_package) in result.output


def test_portal_no_browser_exit_when_package_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``gispulse_portal`` installed (and not in --dev), exit 1 cleanly."""
    monkeypatch.setattr(cli_portal, "_resolve_portal_dist", lambda dev=False: None)

    # Ensure uvicorn would fail loudly if reached.
    fake_uvicorn = types.ModuleType("uvicorn")

    def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("uvicorn.run must not be invoked when package is missing")

    fake_uvicorn.run = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    result = runner.invoke(app, ["portal", "--no-browser"])
    assert result.exit_code == 1
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "pip install gispulse-portal" in combined
