"""A4 (issue #230) — OGC API Features / WFS fetcher for the aggregator.

``OGCFeaturesFetcher`` is the core transport adapter for
:attr:`~gispulse.core.plugin_model.AccessProtocol.OGC_FEATURES` (it also
covers classic ``WFS`` — both are GeoJSON-over-HTTP feature services).

The lazy path emits a DuckDB ``ST_Read`` scan against the OGC API
Features ``/items`` URL: the GDAL ``OAPIF`` driver streams the collection
zero-copy, and the bbox is pushed down through the standard ``bbox=``
query parameter so the server filters before sending. The materialise
path delegates to the consolidated WFS client
(``gispulse.adapters.ogc.wfs_client``) — the transport, pagination and
GeoParquet caching are *not* reimplemented here.
"""

from __future__ import annotations

from typing import Any, ClassVar

from gispulse.core.fetchers.base import LazyFetcher
from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
)

log = get_logger(__name__)

__all__ = ["OGCFeaturesFetcher"]


def _bbox_param(extent: Any | None) -> str | None:
    """Render ``extent`` as an OGC API Features ``bbox`` value, or ``None``.

    The OGC API Features spec expects ``minx,miny,maxx,maxy`` (CRS84).
    """
    if not extent:
        return None
    return ",".join(str(float(c)) for c in extent)


class OGCFeaturesFetcher(LazyFetcher):
    """OGC API Features / WFS adapter — lazy ``ST_Read`` + WFS-client copy.

    ``access.params`` recognised keys:

    * ``collection`` / ``layer_name`` — the collection (``typeName``) to
      fetch. Required for both modes.
    * ``version`` — WFS version for the materialise path (default
      ``"2.0.0"``).
    * ``crs`` — requested SRS (default ``"EPSG:4326"``).
    * ``source_type`` — ``"wfs"`` or ``"ogc_api_features"`` (default
      ``"ogc_api_features"``) — selects the materialise client function.
    * ``max_features`` — per-page limit handed to the WFS client.

    ``access.endpoint`` is the service base URL (the landing page for
    OGC API Features, the WFS endpoint for classic WFS).
    """

    protocol: ClassVar[AccessProtocol] = AccessProtocol.OGC_FEATURES
    payload: ClassVar[Payload] = Payload.VECTOR

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _collection(access: AccessSpec) -> str:
        """The collection / typeName, from ``params``. Raises if absent."""
        coll = access.params.get("collection") or access.params.get("layer_name")
        if not coll:
            raise ValueError(
                "OGCFeaturesFetcher needs access.params['collection'] "
                "(the OGC API Features collection / WFS typeName)"
            )
        return str(coll)

    def _items_url(self, access: AccessSpec, extent: Any | None) -> str:
        """Build the ``/collections/<id>/items`` URL with a bbox query.

        GDAL's ``OAPIF`` driver consumes this URL directly; the ``bbox``
        query parameter is the OGC-standard server-side spatial filter.
        """
        base = access.endpoint.rstrip("/")
        url = f"{base}/collections/{self._collection(access)}/items"
        bbox = _bbox_param(extent)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}f=json"
        if bbox is not None:
            url = f"{url}&bbox={bbox}"
        return url

    # -- LazyFetcher hooks -------------------------------------------------

    def _reference_scan(self, access: AccessSpec, extent: Any | None) -> str:
        """Lazy scan: ``ST_Read('<items-url>')`` — GDAL OAPIF, bbox pushed down.

        The bbox is carried inside the URL (the OGC API ``bbox`` query
        parameter), so the server — not DuckDB — does the filtering.
        """
        url = self._items_url(access, extent).replace("'", "''")
        return f"ST_Read('{url}')"

    def _materialize(self, access: AccessSpec, extent: Any | None) -> SourceResult:
        """Download the collection via the consolidated WFS client.

        Delegates to :func:`gispulse.adapters.ogc.wfs_client.fetch_wfs`
        or :func:`~gispulse.adapters.ogc.wfs_client.fetch_ogc_api_features`
        — the transport, pagination and caching live there (no reinvented
        WFS client). The result GeoDataFrame is returned in
        :attr:`SourceResult.data`.
        """
        from gispulse.adapters.ogc.wfs_client import (
            fetch_ogc_api_features,
            fetch_wfs,
        )
        from gispulse.core.models import OGCSourceConfig

        source_type = str(
            access.params.get("source_type", "ogc_api_features")
        ).lower()
        cfg = OGCSourceConfig(
            source_type="wfs" if source_type == "wfs" else "ogc_api_features",
            url=access.endpoint,
            layer_name=self._collection(access),
            version=str(access.params.get("version", "2.0.0")),
            crs=str(access.params.get("crs", "EPSG:4326")),
            auth=access.params.get("auth"),
            max_features=access.params.get("max_features"),
            params=dict(access.params.get("query", {})),
        )
        bbox = tuple(float(c) for c in extent) if extent else None
        if cfg.source_type == "wfs":
            gdf = fetch_wfs(cfg, bbox=bbox)
        else:
            gdf = fetch_ogc_api_features(cfg, bbox=bbox)
        log.info(
            "ogc_features_materialized",
            collection=cfg.layer_name,
            rows=len(gdf),
        )
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.MATERIALIZE,
            data=gdf,
            crs=cfg.crs,
            extent=bbox,
        )
