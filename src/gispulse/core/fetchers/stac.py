"""A5 (issue #231) — STAC fetcher for the worldwide aggregator.

``STACFetcher`` is the core transport adapter for
:attr:`~gispulse.core.plugin_model.AccessProtocol.STAC`: a SpatioTemporal
Asset Catalog whose items expose Cloud-Optimized GeoTIFF assets.

Both modes wrap the consolidated STAC client
(``gispulse.catalog.providers.stac_client.STACClient``) — the catalog
search, the optional ``pystac-client`` path and the Planetary Computer
signing are *not* reimplemented here.

* lazy (``REFERENCE``) — search the catalog and return the chosen COG
  asset href in :attr:`SourceResult.reference`; the raster is consumed
  live by ``rasterio`` / GDAL ``/vsicurl/``. The STAC ``bbox`` is the
  spatial pushdown — the catalog filters items server-side.
* materialise (``MATERIALIZE``) — call ``STACClient.download_asset`` to
  pull the COG to local disk.
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

__all__ = ["STACFetcher"]

#: Default STAC asset key — most COG catalogs publish a browse asset
#: under ``"visual"``; override via ``access.params['asset']``.
_DEFAULT_ASSET = "visual"


class STACFetcher(LazyFetcher):
    """STAC catalog adapter — lazy COG href + ``download_asset`` materialise.

    ``access.params`` recognised keys:

    * ``collections`` — list of STAC collection IDs to search (required).
    * ``datetime`` — ISO 8601 date / interval (default ``""`` = any).
    * ``asset`` — asset key to expose / download (default ``"visual"``).
    * ``query`` — CQL2 / STAC query-extension dict (e.g. cloud-cover).
    * ``limit`` — max items returned by the search (default ``10``).
    * ``output_dir`` — materialise destination directory (default a temp
      dir).

    ``access.endpoint`` is the STAC catalog root URL. ``extent`` is the
    search bbox ``(minx, miny, maxx, maxy)`` in EPSG:4326 — it is the
    spatial pushdown handed straight to ``STACClient.search``.

    The payload is :attr:`~gispulse.core.plugin_model.Payload.RASTER`.
    """

    protocol: ClassVar[AccessProtocol] = AccessProtocol.STAC
    payload: ClassVar[Payload] = Payload.RASTER

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _collections(access: AccessSpec) -> list[str]:
        """The STAC collection IDs to search. Raises if absent."""
        cols = access.params.get("collections")
        if not cols:
            raise ValueError(
                "STACFetcher needs access.params['collections'] "
                "(a non-empty list of STAC collection IDs)"
            )
        return [str(c) for c in cols]

    @staticmethod
    def _bbox(extent: Any | None) -> list[float]:
        """``extent`` as a STAC bbox list, or worldwide when ``None``.

        A search needs a bbox; ``None`` means 'no spatial pushdown' →
        the whole-world extent.
        """
        if not extent:
            return [-180.0, -90.0, 180.0, 90.0]
        return [float(c) for c in extent]

    def _search(self, access: AccessSpec, extent: Any | None) -> list[dict]:
        """Run the catalog search — the bbox is the spatial pushdown."""
        from gispulse.catalog.providers.stac_client import STACClient

        client = STACClient(access.endpoint)
        return client.search(
            bbox=self._bbox(extent),
            datetime=str(access.params.get("datetime", "")),
            collections=self._collections(access),
            limit=int(access.params.get("limit", 10)),
            query=access.params.get("query"),
        )

    # -- LazyFetcher hooks -------------------------------------------------

    def _reference_scan(self, access: AccessSpec, extent: Any | None) -> str:
        """Return the first matching COG asset href — *not* a DuckDB scan.

        STAC yields rasters; a raster ``REFERENCE`` is a COG URL consumed
        live by GDAL, not a SQL table function. The base stores this
        string under ``metadata[DUCKDB_SCAN_KEY]`` and also echoes
        ``access.endpoint`` in ``SourceResult.reference``; the COG href is
        the load-bearing value here.
        """
        items = self._search(access, extent)
        if not items:
            raise LookupError(
                f"STAC search returned no items for {access.endpoint!r} "
                f"(collections={self._collections(access)})"
            )
        asset_key = str(access.params.get("asset", _DEFAULT_ASSET))
        assets = items[0].get("assets", {})
        if asset_key not in assets:
            raise KeyError(
                f"asset {asset_key!r} absent from STAC item; "
                f"available: {sorted(assets)}"
            )
        href = assets[asset_key].get("href", "")
        if not href:
            raise KeyError(f"STAC asset {asset_key!r} has no 'href'")
        log.debug("stac_reference_href", asset=asset_key)
        return str(href)

    def _materialize(self, access: AccessSpec, extent: Any | None) -> SourceResult:
        """Download the chosen COG asset via ``STACClient.download_asset``.

        Transport / signing live in the STAC client — this only picks the
        first matching item and hands the asset key over.
        """
        import tempfile

        from gispulse.catalog.providers.stac_client import STACClient

        items = self._search(access, extent)
        if not items:
            raise LookupError(
                f"STAC search returned no items for {access.endpoint!r}"
            )
        asset_key = str(access.params.get("asset", _DEFAULT_ASSET))
        output_dir = access.params.get("output_dir") or tempfile.mkdtemp(
            prefix="gispulse-stac-"
        )
        client = STACClient(access.endpoint)
        local_path = client.download_asset(items[0], asset_key, str(output_dir))
        log.info("stac_materialized", path=local_path, asset=asset_key)
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.MATERIALIZE,
            data=local_path,
            extent=tuple(float(c) for c in extent) if extent else None,
        )
