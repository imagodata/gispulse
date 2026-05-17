"""
``gispulse portal`` command — launch the bundled SPA workbench locally.

Mounts the SPA from the optional :mod:`gispulse_portal` PyPI package onto the
local FastAPI engine at ``/portal``. Same-origin = no mixed-content, no local
cert, no CORS.

Two modes:

* default — start a local engine on ``http://localhost:8001``, mount the bundled
  SPA on ``/portal``, healthcheck-then-open-browser. Requires the ``gispulse-portal``
  package to be installed (``pip install gispulse-portal``).
* ``--backend=URL`` — skip the local engine entirely, open the browser straight
  to ``https://gispulse.dev/?backend=URL``. Useful when the user already has a
  remote engine and just wants the GH-Pages UI pointed at it.

Issue #51 — sprint v1.5.1.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

import typer

# Default GH-Pages portal URL used by ``--backend=URL`` (advanced mode).
_GH_PAGES_PORTAL = "https://gispulse.dev/"

# Healthcheck loop budget: 3 s with 100 ms sleep between attempts.
_HEALTHCHECK_TIMEOUT_S = 3.0
_HEALTHCHECK_INTERVAL_S = 0.1


def _resolve_portal_dist(dev: bool) -> Optional[Path]:
    """Locate the bundled SPA dist directory.

    Resolution order:

    1. ``gispulse_portal.PORTAL_DIST_PATH`` (the PyPI two-package install path).
    2. Local ``portal/dist/`` checkout — only when ``dev`` is true (dev workflow).

    Returns ``None`` when nothing usable was found. Caller is expected to
    surface a remediation message and exit non-zero.
    """
    try:
        from gispulse_portal import PORTAL_DIST_PATH  # type: ignore[import-not-found]

        candidate = Path(PORTAL_DIST_PATH)
        if candidate.exists() and candidate.is_dir():
            return candidate
    except ImportError:
        pass

    if dev:
        # Repo-relative fallback: gispulse/<here>/cli_portal.py -> repo_root/portal/dist
        repo_dist = Path(__file__).resolve().parents[2] / "portal" / "dist"
        if repo_dist.exists() and repo_dist.is_dir():
            return repo_dist

    return None


def _healthcheck_then_open(url: str, port: int, timeout: float = _HEALTHCHECK_TIMEOUT_S) -> None:
    """Poll ``http://127.0.0.1:<port>/health`` until 200 OK, then open browser.

    Runs on a daemon thread so it does not block the uvicorn event loop.
    Silently gives up after ``timeout`` seconds — the server may still come
    up, but we will not hold the user's browser hostage.
    """
    health_url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=0.5) as resp:  # noqa: S310
                if 200 <= resp.status < 500:
                    webbrowser.open(url)
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(_HEALTHCHECK_INTERVAL_S)
    # Timeout — best-effort open anyway so the user is not left staring at a terminal.
    webbrowser.open(url)


def _open_remote_portal(backend_url: str, *, opener=None) -> None:
    """Open GH-Pages portal pointed at a remote backend.

    Encoded ``backend`` query string is appended to ``_GH_PAGES_PORTAL``.

    ``opener`` defaults to a late-bound lookup of ``webbrowser.open`` so
    tests can monkey-patch ``cli_portal.webbrowser.open`` and have the
    patch honoured.
    """
    qs = urllib.parse.urlencode({"backend": backend_url})
    target = f"{_GH_PAGES_PORTAL}?{qs}"
    typer.echo(f"Opening remote portal: {target}")
    if opener is None:
        opener = webbrowser.open
    opener(target)


def cmd_portal(
    port: int = typer.Option(
        8001,
        "--port",
        "-p",
        help="Port to listen on (local mode only).",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Host to bind to (local mode only).",
    ),
    data_dir: str = typer.Option(
        "~/.gispulse/data",
        "--data-dir",
        "-d",
        help="Directory for uploaded datasets (local mode only).",
    ),
    backend: Optional[str] = typer.Option(
        None,
        "--backend",
        help=(
            "Advanced: open the GH-Pages portal pointed at this backend URL "
            "instead of starting a local engine. Skips the SPA mount."
        ),
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Don't open the browser automatically.",
    ),
    dev: bool = typer.Option(
        False,
        "--dev",
        help=(
            "Dev mode: allow falling back to the repo-local portal/dist/ "
            "when the gispulse-portal package is not installed."
        ),
    ),
) -> None:
    """Launch the GISPulse Portal — bundled SPA workbench, same-origin local engine.

    ``gispulse portal`` mounts the visual workbench shipped by the optional
    ``gispulse-portal`` PyPI package onto a locally running engine and opens
    your browser. ``--backend=URL`` swaps that for the remote GH-Pages
    portal pointed at any reachable backend.
    """
    # ------------------------------------------------------------------ Remote mode
    if backend:
        _open_remote_portal(backend)
        return

    # ------------------------------------------------------------------ Local mode
    portal_dist = _resolve_portal_dist(dev=dev)
    if portal_dist is None:
        typer.echo(
            "Error: gispulse-portal package is not installed.\n"
            "Install it with:\n"
            "  pip install gispulse-portal\n"
            "Or, for a remote workbench without a local install:\n"
            "  gispulse portal --backend=https://your-engine.example.com",
            err=True,
        )
        raise typer.Exit(1)

    # Lazy import — keeps `gispulse --help` startup fast and lets the unit test
    # for "gispulse_portal absent" run without dragging FastAPI into the assertion.
    from fastapi.staticfiles import StaticFiles

    from gispulse.adapters.http.app import create_app

    app = create_app(mode="portal", data_dir=data_dir)
    app.mount(
        "/portal",
        StaticFiles(directory=str(portal_dist), html=True),
        name="portal-bundled",
    )

    display_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    portal_url = f"http://{display_host}:{port}/portal/"

    typer.echo(f"GISPulse Portal at {portal_url}")
    typer.echo(f"Serving SPA from {portal_dist}")

    if not no_browser:
        # Healthcheck retry loop on a daemon thread so uvicorn can boot
        # before we hit the URL.
        opener = threading.Thread(
            target=_healthcheck_then_open,
            args=(portal_url, port),
            daemon=True,
        )
        opener.start()

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
