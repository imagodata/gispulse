"""GISPulse capability plugin — STAC catalog connector."""

from __future__ import annotations


def register() -> None:
    """Register all capabilities provided by this plugin."""
    from gispulse_cap_stac import capabilities  # noqa: F401
