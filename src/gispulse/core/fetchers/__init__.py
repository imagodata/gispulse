"""Worldwide-aggregator protocol fetchers (EPIC #226, v1.9.0).

A2 (issue #228) ships this package skeleton, the :class:`LazyFetcher`
base (``base.py``) and :func:`register_core_fetchers`. The concrete
adapters are:

* ``geoparquet_s3``  (A3 #229) — ``AccessProtocol.REMOTE_TABLE``
* ``ogc_features``   (A4 #230) — ``AccessProtocol.OGC_FEATURES`` / ``WFS``
* ``stac``           (A5 #231) — ``AccessProtocol.STAC``
* ``http_file``      (A6 #232) — ``AccessProtocol.DOWNLOAD``
* ``table_file``     — ``AccessProtocol.TABLE_FILE``

Until those land, :func:`register_core_fetchers` walks an empty roster
and registers nothing — calling it is always safe.
"""

from __future__ import annotations

from gispulse.core.fetchers.base import DUCKDB_SCAN_KEY, LazyFetcher
from gispulse.core.logging import get_logger
from gispulse.core.sources import PROTOCOLS, ProtocolRegistry

log = get_logger(__name__)

__all__ = ["DUCKDB_SCAN_KEY", "LazyFetcher", "register_core_fetchers"]


def _core_fetchers() -> list[LazyFetcher]:
    """Instantiate every concrete core fetcher.

    Each of issues A3-A6 appends its adapter import here. Kept as a
    function (not a module-level list) so the heavy per-protocol imports
    stay lazy — ``import gispulse`` must not pull DuckDB / httpx. The
    fetcher *modules* themselves only import the stdlib + ``core`` at
    module scope; their DuckDB / httpx / client imports are deferred into
    the fetch methods, so this roster is cheap to build.
    """
    from .geoparquet_s3 import GeoParquetS3Fetcher  # A3 #229 — REMOTE_TABLE
    from .http_file import HttpFileFetcher  # A6 #232 — DOWNLOAD
    from .ogc_features import OGCFeaturesFetcher  # A4 #230 — OGC_FEATURES
    from .stac import STACFetcher  # A5 #231 — STAC
    from .table_file import TableFileFetcher

    return [
        GeoParquetS3Fetcher(),
        OGCFeaturesFetcher(),
        STACFetcher(),
        HttpFileFetcher(),
        TableFileFetcher(),
    ]


def register_core_fetchers(
    registry: ProtocolRegistry | None = None, *, override: bool = True
) -> int:
    """Register the core worldwide fetchers into a protocol registry.

    Args:
        registry: Target registry. Defaults to the process-wide
            :data:`~gispulse.core.sources.PROTOCOLS`.
        override: Replace an adapter already filed under the same
            protocol. ``True`` by default — a core fetcher is the
            canonical adapter for its protocol family.

    Returns:
        The number of fetchers registered.
    """
    target = registry if registry is not None else PROTOCOLS
    count = 0
    for fetcher in _core_fetchers():
        target.register(fetcher, override=override)
        count += 1
    log.info("core_fetchers_registered", count=count)
    return count
