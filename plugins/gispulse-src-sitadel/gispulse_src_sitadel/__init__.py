"""GISPulse data-source plugin - Sitadel urban planning authorisations (SDES)."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_sitadel.source import SitadelSource

    SOURCES.register(SitadelSource())
