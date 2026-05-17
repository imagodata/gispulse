"""STAC fetcher — SpatioTemporal Asset Catalog in the ProtocolRegistry (#192).

PR #189 delivered the :class:`~core.sources.ProtocolRegistry` /
:class:`~core.sources.Fetcher` contract; #209 absorbed the WFS / OGC API
Features clients. This module completes issue #192 by wrapping the
existing :class:`catalog.providers.stac_client.STACClient` as a
:class:`~core.sources.Fetcher` registered under
:attr:`~core.plugin_model.AccessProtocol.STAC`.

A STAC search resolves to *asset references* — COG / imagery hrefs —
never a materialised raster. The returned
:class:`~core.plugin_model.SourceResult` is therefore always
:attr:`~core.plugin_model.FetchMode.REFERENCE`, regardless of the
requested mode; downloading a scene is an explicit downstream step.

Importing this module self-registers the fetcher in the process-wide
:data:`core.sources.PROTOCOLS` registry (idempotent).
"""

from __future__ import annotations

from typing import Any

from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
)

log = get_logger(__name__)

#: STAC search needs a bbox — fall back to the whole world when the fetch
#: carries no extent.
_DEFAULT_BBOX: list[float] = [-180.0, -90.0, 180.0, 90.0]
#: STAC open datetime interval — matches any date.
_DEFAULT_DATETIME = "../.."


def _bbox_from_extent(extent: Any) -> list[float] | None:
    """Coerce a fetch ``extent`` into a ``[minx, miny, maxx, maxy]`` list."""
    if extent is None:
        return None
    try:
        coords = [float(c) for c in extent]
    except (TypeError, ValueError):
        return None
    return coords if len(coords) == 4 else None


def _collections_from_params(params: dict[str, Any]) -> list[str]:
    """Resolve the STAC collection ids declared on an :class:`AccessSpec`.

    Accepts ``collections`` (str or list) or the singular ``collection``.

    Raises:
        ValueError: when neither key is declared.
    """
    raw = params.get("collections")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)) and raw:
        return [str(c) for c in raw]
    single = params.get("collection")
    if single:
        return [str(single)]
    raise ValueError(
        "STAC AccessSpec.params must declare a 'collection' (id) or "
        "'collections' (list of ids)"
    )


def _asset_href(item: dict, preferred: str | None) -> str | None:
    """Pick an asset href from a STAC item — ``preferred`` key, else first."""
    assets = item.get("assets") or {}
    if not assets:
        return None
    if preferred and preferred in assets:
        return assets[preferred].get("href")
    first = next(iter(assets.values()))
    return first.get("href")


class StacFetcher:
    """:class:`~core.sources.Fetcher` for ``AccessProtocol.STAC``.

    Wraps :class:`catalog.providers.stac_client.STACClient`. The
    :class:`AccessSpec` carries the catalog URL as ``endpoint`` and the
    search in ``params``:

    - ``collection`` / ``collections`` — STAC collection id(s) *(required)*
    - ``datetime`` — ISO-8601 instant or interval *(default: any date)*
    - ``limit`` — max items returned *(default: 10)*
    - ``query`` — optional CQL2 property-filter dict
    - ``asset`` — preferred asset key for the reference href
    - ``timeout`` — HTTP timeout in seconds *(default: 30)*

    The fetch ``extent`` is used as the search bbox (EPSG:4326).
    """

    protocol = AccessProtocol.STAC

    def fetch(
        self,
        access: AccessSpec,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        from gispulse.catalog.providers.stac_client import STACClient

        params = dict(access.params or {})
        collections = _collections_from_params(params)
        bbox = (
            _bbox_from_extent(extent)
            or _bbox_from_extent(params.get("bbox"))
            or _DEFAULT_BBOX
        )
        client = STACClient(access.endpoint, timeout=int(params.get("timeout", 30)))
        items = client.search(
            bbox=bbox,
            datetime=str(params.get("datetime", _DEFAULT_DATETIME)),
            collections=collections,
            limit=int(params.get("limit", 10)),
            query=params.get("query"),
        )
        preferred = params.get("asset")
        hrefs = [h for h in (_asset_href(it, preferred) for it in items) if h]
        log.info(
            "stac_fetch",
            endpoint=access.endpoint,
            collections=collections,
            items=len(items),
            assets=len(hrefs),
        )
        return SourceResult(
            payload=Payload.RASTER,
            # A STAC search yields asset references, never a materialised
            # raster — the result is REFERENCE whatever the caller asked.
            mode=FetchMode.REFERENCE,
            reference=hrefs[0] if hrefs else None,
            extent=tuple(bbox),  # type: ignore[arg-type]
            metadata={
                "catalog": access.endpoint,
                "collections": collections,
                "item_count": len(items),
                "asset_hrefs": hrefs,
                "requested_mode": mode.value,
            },
        )


def _register(registry: Any, fetcher: Any, protocol: AccessProtocol) -> None:
    """Register ``fetcher`` under ``protocol`` — idempotent."""
    from gispulse.core.sources import PROTOCOLS, ProtocolNotSupported

    target = registry if registry is not None else PROTOCOLS
    try:
        target.get_fetcher(protocol)
        return  # already registered
    except ProtocolNotSupported:
        pass
    target.register(fetcher)


def register_stac_fetcher(registry: Any | None = None) -> None:
    """Register a :class:`StacFetcher` (default registry: ``PROTOCOLS``)."""
    _register(registry, StacFetcher(), AccessProtocol.STAC)


# Importing this module wires the fetcher into the global registry.
register_stac_fetcher()


__all__ = ["StacFetcher", "register_stac_fetcher"]
