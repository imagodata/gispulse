"""GISPulse capability plugin — Report builder."""

from __future__ import annotations


def register() -> None:
    """Register all capabilities provided by this plugin."""
    from gispulse_cap_report import capabilities  # noqa: F401
