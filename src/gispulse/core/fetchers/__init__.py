"""Worldwide-aggregator protocol fetchers (EPIC #226, v1.9.0).

A2 (issue #228) ships this package skeleton, the :class:`LazyFetcher`
base (``base.py``) and :func:`register_core_fetchers`. The four concrete
adapters land in the follow-up issues:

* ``geoparquet_s3``  (A3 #229) — ``AccessProtocol.REMOTE_TABLE``
* ``ogc_features``   (A4 #230) — ``AccessProtocol.OGC_FEATURES`` / ``WFS``
* ``stac``           (A5 #231) — ``AccessProtocol.STAC``
* ``http_file``      (A6 #232) — ``AccessProtocol.DOWNLOAD``

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
    stay lazy — ``import gispulse`` must not pull DuckDB / httpx.
    """
    fetchers: list[LazyFetcher] = []
    # A3 #229 — from .geoparquet_s3 import GeoParquetS3Fetcher; fetchers.append(...)
    # A4 #230 — from .ogc_features  import OGCFeaturesFetcher;  fetchers.append(...)
    # A5 #231 — from .stac          import STACFetcher;         fetchers.append(...)
    # A6 #232 — from .http_file     import HttpFileFetcher;     fetchers.append(...)
    return fetchers


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
