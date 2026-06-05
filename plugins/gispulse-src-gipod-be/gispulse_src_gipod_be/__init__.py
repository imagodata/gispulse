"""GISPulse data-source plugin — GIPOD public domain works and occupancies (Flandre/BE)."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_gipod_be.source import GipodBeSource

    SOURCES.register(GipodBeSource())
