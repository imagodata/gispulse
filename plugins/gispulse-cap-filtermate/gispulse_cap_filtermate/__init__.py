"""GISPulse capability plugin — FilterMate spatial filtering."""

from __future__ import annotations


def register() -> None:
    """Register all capabilities provided by this plugin."""
    from gispulse_cap_filtermate import capabilities  # noqa: F401
