"""GISPulse data-source plugin — Servitudes d'Utilité Publique (SUP).

Declarative ``Payload.VECTOR`` WFS source for the French SUP layers
published by the Géoplateforme / Géoportail de l'Urbanisme. The plugin
declares raw access specs only; interpretation as ABF, PPR or other
product rules stays downstream.
"""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group."""
    from gispulse.core.sources import SOURCES
    from gispulse_src_sup.source import SupSource

    SOURCES.register(SupSource())
