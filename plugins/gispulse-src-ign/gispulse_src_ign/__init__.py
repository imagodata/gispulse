"""GISPulse data-source plugin — IGN reference data (issue #194, pilot wave 1).

Second ``gispulse-src-*`` pilot: validates the ``DeclarativeSource``
contract on a *multi-layer* pure ``fetch()`` source (BD TOPO + Admin
Express), after ``gispulse-src-cadastre`` proved the single-dataset case.
"""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group.

    Registers an :class:`IgnSource` instance in the process-wide
    ``core.sources.SOURCES`` registry so the source watcher (#197) can
    resolve ``ign://<entry>`` URIs declared in ``triggers.yaml``.
    """
    from core.sources import SOURCES
    from gispulse_src_ign.source import IgnSource

    SOURCES.register(IgnSource())
