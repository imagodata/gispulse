"""GISPulse data-source plugin — French Géorisques (issue #196).

Declarative source over ``AccessProtocol.REST_TABLE`` (the paginated
tabular-JSON fetcher, #196 wave 1). It replaces the per-product Géorisques
HTTP clients duplicated in ``gispulse-permis`` and ``gispulse-foncier``:
the plugin only declares the six Géorisques endpoints; the orchestrator
supplies the runtime spatial key (``code_insee`` / ``latlon``) via
:meth:`GeorisquesSource.access_for`.
"""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group.

    Registers a :class:`GeorisquesSource` instance in the process-wide
    ``gispulse.core.sources.SOURCES`` registry so the source watcher can
    resolve ``georisques://<entry>`` URIs.
    """
    from gispulse.core.sources import SOURCES
    from gispulse_src_georisques.source import GeorisquesSource

    SOURCES.register(GeorisquesSource())
