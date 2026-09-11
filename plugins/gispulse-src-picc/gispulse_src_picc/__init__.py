"""PICC Wallonia source plugin for GISPulse."""


def register() -> None:
    """Register in the standard source registry."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_picc.source import PiccSource

    SOURCES.register(PiccSource())
