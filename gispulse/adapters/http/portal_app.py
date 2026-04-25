"""
GISPulse Portal — FastAPI application factory (shim).

Delegates to ``adapters.http.app.create_app(mode="portal")``.
Kept for backward compatibility with ``gispulse serve`` CLI.
"""

from __future__ import annotations

from pathlib import Path

from gispulse.adapters.http.app import create_app

_PORTAL_DIST = Path(__file__).resolve().parent.parent.parent / "portal" / "dist"


def create_portal_app(
    data_dir: str | Path = "~/.gispulse/data",
    static_dir: Path | None = None,
):
    """Create the portal-mode FastAPI application.

    Args:
        data_dir:   Directory for storing uploaded datasets.
        static_dir: Optional path to the built SPA. Defaults to portal/dist/.

    Returns:
        Configured FastAPI app.
    """
    return create_app(mode="portal", data_dir=data_dir, static_dir=static_dir)
