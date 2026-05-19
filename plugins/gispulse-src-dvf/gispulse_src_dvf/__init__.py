"""GISPulse data-source plugin — French DVF (issue #184, pilot wave 2).

Second-wave pilot of the ``gispulse-src-*`` family: it validates the
``DeclarativeSource`` contract on a ``Payload.TABLE`` source whose
spatial join is keyed on cadastral references rather than geometry.
"""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group.

    Registers a :class:`DvfSource` instance in the process-wide
    ``gispulse.core.sources.SOURCES`` registry so the source watcher
    (issue #197) can resolve ``dvf://<entry>`` URIs declared in
    ``triggers.yaml``.
    """
    from gispulse.core.sources import SOURCES
    from gispulse_src_dvf.source import DvfSource

    SOURCES.register(DvfSource())
