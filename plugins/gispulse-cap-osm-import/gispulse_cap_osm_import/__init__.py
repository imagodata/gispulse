"""GISPulse capability plugin — OSM data import."""

from __future__ import annotations


def register() -> None:
    """Register all capabilities provided by this plugin."""
    from gispulse_cap_osm_import import capabilities  # noqa: F401
