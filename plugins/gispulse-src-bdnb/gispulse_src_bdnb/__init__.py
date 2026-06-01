"""GISPulse data-source plugin - BDNB building archives."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_bdnb.source import BdnbSource

    SOURCES.register(BdnbSource())
