"""GISPulse data-source plugin - Base Adresse Nationale."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_ban.source import BanSource

    SOURCES.register(BanSource())
