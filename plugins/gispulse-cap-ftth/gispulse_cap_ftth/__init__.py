"""GISPulse capability plugin — FTTH network design."""

from __future__ import annotations


def register() -> None:
    """Register all capabilities provided by this plugin."""
    from gispulse_cap_ftth import capabilities  # noqa: F401
