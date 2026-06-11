"""GISPulse data-source plugin — OpenStreetMap (Geofabrik) extracts."""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_osm.source import OsmSource

    SOURCES.register(OsmSource())
