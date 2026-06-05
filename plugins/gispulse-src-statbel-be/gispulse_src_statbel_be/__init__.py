"""GISPulse data-source plugin — Statbel Belgium open statistical data."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_statbel_be.source import StatbelBeSource

    SOURCES.register(StatbelBeSource())
