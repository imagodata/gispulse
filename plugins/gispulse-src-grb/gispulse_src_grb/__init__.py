"""GISPulse data-source plugin — GRB Flanders (voirie/bâti)."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_grb.source import GrbSource

    SOURCES.register(GrbSource())
