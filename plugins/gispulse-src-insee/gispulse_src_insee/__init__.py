"""GISPulse data-source plugin — French INSEE statistical units.

Pilot of the ``gispulse-src-*`` family for statistical units exposed
through the IGN Géoplateforme WFS. The first entry is IRIS, the
infra-communal statistical grid used by INSEE.
"""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group.

    Registers an :class:`InseeSource` instance in the process-wide
    ``gispulse.core.sources.SOURCES`` registry so the source watcher can
    resolve ``insee://<entry>`` URIs declared in ``triggers.yaml``.
    """
    from gispulse.core.sources import SOURCES
    from gispulse_src_insee.source import InseeSource

    SOURCES.register(InseeSource())
