"""GISPulse data-source plugin — French DVF (issue #184, pilot wave 2).

Second-wave pilot of the ``gispulse-src-*`` family: it validates the
``DeclarativeSource`` contract on a ``Payload.TABLE`` source whose
spatial join is keyed on cadastral references rather than geometry.
"""

from __future__ import annotations

from typing import Any

__all__ = ["dvf_registry", "register", "resolve_dvf_scan"]


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


def dvf_registry():
    """Return the plugin-local DVF CSV protocol registry."""
    from gispulse_src_dvf.source import dvf_registry as _dvf_registry

    return _dvf_registry()


def resolve_dvf_scan(entry: object, *, extent: Any | None = None) -> str:
    """Resolve a DVF catalog entry to a DuckDB ``read_csv_auto`` scan."""
    from gispulse_src_dvf.source import resolve_dvf_scan as _resolve_dvf_scan

    return _resolve_dvf_scan(entry, extent=extent)
