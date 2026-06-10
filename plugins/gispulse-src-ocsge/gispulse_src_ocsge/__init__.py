"""GISPulse data-source plugin - OCS GE (IGN)."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    import sys

    from gispulse.plugins.api import DataSource
    from gispulse_src_ocsge.source import OcsgeSource

    sources = getattr(sys.modules[DataSource.__module__], "SOURCES")
    sources.register(OcsgeSource())
