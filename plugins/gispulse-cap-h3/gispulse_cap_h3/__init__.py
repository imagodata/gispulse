"""GISPulse capability plugin — H3 hexagonal analysis."""

from __future__ import annotations


def register() -> None:
    """Register all capabilities provided by this plugin."""
    from gispulse_cap_h3 import capabilities  # noqa: F401
