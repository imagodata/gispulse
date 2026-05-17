"""REST GeoJSON fetcher — generic GeoJSON-over-HTTP in the ProtocolRegistry.

Issue #192. Completes the absorption of the core transport clients into
:data:`core.sources.PROTOCOLS`: this module registers a fetcher under
:attr:`~core.plugin_model.AccessProtocol.REST_API` for any endpoint that
answers a GeoJSON ``FeatureCollection``.

The canonical consumer is IGN **API Carto** (``apicarto.ign.fr``) — a
geometry-filtered GeoJSON REST API — but the fetcher is endpoint-agnostic:
the :class:`~core.plugin_model.AccessSpec` carries the literal query
parameters, and an optional ``geom_param`` names the field that receives
the fetch ``extent`` as a GeoJSON polygon.

Importing this module self-registers the fetcher in the process-wide
:data:`core.sources.PROTOCOLS` registry (idempotent).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
)

log = get_logger(__name__)

#: GeoJSON (RFC 7946) coordinates are always WGS84.
_GEOJSON_CRS = "EPSG:4326"
_DEFAULT_TIMEOUT_S = 20.0
#: AccessSpec.params keys the fetcher consumes itself — every *other* key
#: is forwarded verbatim as an HTTP query parameter.
_RESERVED_PARAMS = frozenset({"geom_param", "timeout"})


def _bbox_from_extent(extent: Any) -> tuple[float, float, float, float] | None:
    """Coerce a fetch ``extent`` into a 4-tuple bbox, or ``None``."""
    if extent is None:
        return None
    try:
        coords = tuple(float(c) for c in extent)
    except (TypeError, ValueError):
        return None
    if len(coords) != 4:
        return None
    return coords  # type: ignore[return-value]


def _bbox_polygon(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    """Return a closed GeoJSON Polygon for ``bbox`` (minx, miny, maxx, maxy)."""
    minx, miny, maxx, maxy = bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [minx, miny],
                [maxx, miny],
                [maxx, maxy],
                [minx, maxy],
                [minx, miny],
            ]
        ],
    }


def _get_geojson(url: str, timeout: float) -> dict[str, Any]:
    """GET ``url`` and return the parsed JSON body."""
    import httpx

    resp = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"Accept": "application/geo+json, application/json"},
    )
    resp.raise_for_status()
    return resp.json()


class RestGeoJsonFetcher:
    """:class:`~core.sources.Fetcher` for ``AccessProtocol.REST_API``.

    Reads a GeoJSON ``FeatureCollection`` from an HTTP endpoint. The
    :class:`AccessSpec` carries the URL as ``endpoint`` and, in ``params``:

    - ``geom_param`` — *(optional)* name of the query field that receives
      the fetch ``extent`` as a GeoJSON polygon (API Carto uses ``geom``)
    - ``timeout`` — HTTP timeout in seconds *(default: 20)*
    - every other key — forwarded verbatim as an HTTP query parameter
    """

    protocol = AccessProtocol.REST_API

    def fetch(
        self,
        access: AccessSpec,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        import geopandas as gpd

        params = dict(access.params or {})
        timeout = float(params.get("timeout", _DEFAULT_TIMEOUT_S))
        geom_param = params.get("geom_param")
        query: dict[str, Any] = {
            k: v for k, v in params.items() if k not in _RESERVED_PARAMS
        }
        bbox = _bbox_from_extent(extent)
        if geom_param and bbox is not None:
            query[str(geom_param)] = json.dumps(
                _bbox_polygon(bbox), separators=(",", ":")
            )
        url = access.endpoint
        if query:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urlencode(query)}"

        payload = _get_geojson(url, timeout)
        if payload.get("type") != "FeatureCollection":
            raise ValueError(
                f"REST endpoint did not return a GeoJSON FeatureCollection "
                f"(got type={payload.get('type')!r}): {access.endpoint}"
            )
        features = payload.get("features") or []
        if features:
            gdf = gpd.GeoDataFrame.from_features(features, crs=_GEOJSON_CRS)
        else:
            gdf = gpd.GeoDataFrame(geometry=[], crs=_GEOJSON_CRS)
        log.info(
            "rest_geojson_fetch",
            endpoint=access.endpoint,
            features=len(gdf),
        )
        return SourceResult(
            payload=Payload.VECTOR,
            mode=mode,
            data=gdf,
            crs=_GEOJSON_CRS,
            metadata={"endpoint": access.endpoint, "feature_count": len(gdf)},
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


def register_rest_geojson_fetcher(registry: Any | None = None) -> None:
    """Register a :class:`RestGeoJsonFetcher` (default registry: ``PROTOCOLS``)."""
    _register(registry, RestGeoJsonFetcher(), AccessProtocol.REST_API)


# Importing this module wires the fetcher into the global registry.
register_rest_geojson_fetcher()


__all__ = ["RestGeoJsonFetcher", "register_rest_geojson_fetcher"]
