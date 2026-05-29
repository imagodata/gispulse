"""GISPulse data-source plugin — French DPE (diagnostics de performance énergétique).

Declarative source over ``AccessProtocol.REST_TABLE`` for the ADEME
data-fair API (data.ademe.fr). Two entries are declared:

* ``logements-existants`` — DPE logements existants depuis juillet 2021
  (dataset id ``meg-83tjwtg8dyz4vv7h1dqe``).
* ``logements-neufs`` — DPE logements neufs depuis juillet 2021
  (dataset id ``g3cgx7jb3cmys5voxz1mrm22``).

The source is spatial-keyed at commune level (``code_insee_ban``) or
département level (``code_departement_ban``). The runtime spatial key
is supplied per call by the ingestion orchestrator via
:meth:`DpeSource.access_for`.
"""

from __future__ import annotations


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group.

    Registers a :class:`DpeSource` instance in the process-wide
    ``gispulse.core.sources.SOURCES`` registry so the source watcher can
    resolve ``dpe://<entry>`` URIs declared in ``triggers.yaml``.
    """
    from gispulse.core.sources import SOURCES
    from gispulse_src_dpe.source import DpeSource

    SOURCES.register(DpeSource())
