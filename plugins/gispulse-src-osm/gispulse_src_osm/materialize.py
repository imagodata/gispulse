"""Materialization helpers for raw OSM PBF extracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from gispulse.core.fetchers.http_file import HttpFileFetcher
from gispulse.core.plugin_model import FetchMode
from gispulse_src_osm.source import OsmSource

__all__ = ["materialize_pbf"]


def materialize_pbf(dest: str | Path, *, force: bool = False) -> Path:
    """Télécharge l'extract PBF Belgique déclaré vers un fichier brut local.

    Le téléchargement est délégué au fetcher DOWNLOAD du core. Si ``dest``
    existe déjà, il est réutilisé sauf quand ``force=True``.
    """
    destination = Path(dest)
    if destination.exists() and not force:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    access = OsmSource().access_for("osm-pbf-be")
    materialized_access = replace(
        access,
        params={**access.params, "local_path": str(destination)},
    )
    HttpFileFetcher().fetch(materialized_access, mode=FetchMode.MATERIALIZE)
    return destination
