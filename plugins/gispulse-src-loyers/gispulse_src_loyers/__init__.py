"""GISPulse data-source plugin - French rent and rental tension signals."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_loyers.source import LoyersSource

    SOURCES.register(LoyersSource())
