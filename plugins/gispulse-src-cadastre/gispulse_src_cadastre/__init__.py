"""GISPulse data-source plugin — French cadastre (issue #184, pilot wave 1).

First pilot of the ``gispulse-src-*`` family: it validates the
``DeclarativeSource`` contract end-to-end on a pure ``fetch()`` source.
"""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group.

    Importing the module is enough — :class:`CadastreSource` is a plain
    declarative class; the PluginHub records the source from this
    callable.
    """
    from gispulse_src_cadastre import source  # noqa: F401
