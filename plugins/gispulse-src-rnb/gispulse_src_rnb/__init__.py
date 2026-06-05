"""GISPulse data-source plugin - RNB building identity API."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_rnb.source import RnbSource

    SOURCES.register(RnbSource())
