"""GISPulse data-source plugin — API Carto Nature (IGN / INPN)."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    import sys

    from gispulse.plugins.api import DataSource
    from gispulse_src_nature.source import NatureSource

    sources = getattr(sys.modules[DataSource.__module__], "SOURCES")
    sources.register(NatureSource())
