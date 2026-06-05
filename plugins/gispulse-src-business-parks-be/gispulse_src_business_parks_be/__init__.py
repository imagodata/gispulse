"""GISPulse data-source plugin - Belgian business parks."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_business_parks_be.source import BusinessParksBeSource

    SOURCES.register(BusinessParksBeSource())
