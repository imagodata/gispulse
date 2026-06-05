"""GISPulse data-source plugin for BODACC commercial notices."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_bodacc.source import BodaccSource

    SOURCES.register(BodaccSource())
