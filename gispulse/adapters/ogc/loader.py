"""Facade for loading datasets from OGC services.

Dispatches to the appropriate client based on
``dataset.ogc_source.source_type``.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from core.models import Dataset
from gispulse.adapters.ogc.wfs_client import fetch_ogc_api_features, fetch_wfs


def load_ogc_dataset(
    dataset: Dataset,
    bbox: tuple[float, float, float, float] | None = None,
    cache_dir: Path | str | None = None,
) -> gpd.GeoDataFrame:
    """Load features from a remote OGC source attached to *dataset*.

    Parameters
    ----------
    dataset:
        A ``Dataset`` instance whose ``ogc_source`` is populated.
    bbox:
        Optional spatial filter ``(minx, miny, maxx, maxy)``.
    cache_dir:
        Optional directory for GeoParquet cache.

    Returns
    -------
    GeoDataFrame containing all fetched features.

    Raises
    ------
    ValueError
        If ``dataset.ogc_source`` is ``None`` or has an unknown ``source_type``.
    """
    cfg = dataset.ogc_source
    if cfg is None:
        raise ValueError(
            f"Dataset {dataset.id!s} has no OGC source configuration."
        )

    if cfg.source_type == "wfs":
        return fetch_wfs(cfg, bbox=bbox, cache_dir=cache_dir)
    elif cfg.source_type == "ogc_api_features":
        return fetch_ogc_api_features(cfg, bbox=bbox, cache_dir=cache_dir)
    else:
        raise ValueError(
            f"Unknown OGC source_type: {cfg.source_type!r}. "
            "Expected 'wfs' or 'ogc_api_features'."
        )
