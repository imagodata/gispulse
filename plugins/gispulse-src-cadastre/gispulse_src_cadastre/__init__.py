"""GISPulse data-source plugin — French cadastre (issue #184, pilot wave 1).

First pilot of the ``gispulse-src-*`` family: it validates the
``DeclarativeSource`` contract end-to-end on a pure ``fetch()`` source.
"""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group.

    Registers a :class:`CadastreSource` instance in the process-wide
    ``core.sources.SOURCES`` registry so the source watcher (issue #197)
    can resolve ``cadastre://<entry>`` URIs declared in ``triggers.yaml``.
    """
    from core.sources import SOURCES
    from gispulse_src_cadastre.source import CadastreSource

    SOURCES.register(CadastreSource())
