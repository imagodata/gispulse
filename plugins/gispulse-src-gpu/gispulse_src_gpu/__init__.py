"""GISPulse data-source plugin — Géoportail de l'Urbanisme (issue #184, pilot wave 2).

Second-wave pilot of the ``gispulse-src-*`` family: exercises the
``DeclarativeSource`` contract on a multi-entry ``Payload.VECTOR`` WFS
source for French urban-planning documents (PLU / PLUi / POS / CC).

Domain: :data:`SourceDomain.REGLEMENTAIRE` — promotion to
:class:`RegulatorySource` (with a wired :meth:`ruleset`) is left to a
follow-up plugin once the :class:`RuleClause`-to-PLU mapping is
stabilised.
"""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group.

    Registers a :class:`GpuSource` instance in the process-wide
    ``gispulse.core.sources.SOURCES`` registry so the source watcher
    (issue #197) can resolve ``gpu://<entry>`` URIs declared in
    ``triggers.yaml``.
    """
    from gispulse.core.sources import SOURCES
    from gispulse_src_gpu.source import GpuSource

    SOURCES.register(GpuSource())
