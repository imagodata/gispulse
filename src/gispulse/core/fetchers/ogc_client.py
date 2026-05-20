"""Story T1 (#267) — high-level OGC client for data packs.

The OSS engine already ships :class:`OGCFeaturesFetcher` and the consolidated
transport in :mod:`gispulse.adapters.ogc.wfs_client`. A data pack, however,
does not want to construct an ``AccessSpec`` to fetch one collection — it
wants a one-liner: *given an endpoint and a typename, give me a
GeoDataFrame*.

This module is that one-liner. It is intentionally thin (no transport, no
caching, no pagination logic of its own): it normalises the input, picks
the right transport function (classic WFS vs OGC API Features), and surfaces
network errors as a single explicit exception type so callers can react
without depending on httpx internals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from gispulse.core.logging import get_logger
from gispulse.core.models import OGCSourceConfig

if TYPE_CHECKING:  # pragma: no cover
    import geopandas as gpd


log = get_logger(__name__)

__all__ = [
    "OGCClientError",
    "OGCEndpointUnreachable",
    "fetch_features",
]


Protocol = Literal["wfs", "ogc_api_features"]


class OGCClientError(Exception):
    """Base error for the high-level OGC client."""


class OGCEndpointUnreachable(OGCClientError):
    """The remote service could not be reached (DNS / connect / read timeout).

    Carries the *original* exception as ``__cause__`` so debuggers can see
    the underlying ``httpx.ConnectError`` / ``httpx.ReadTimeout`` without the
    caller having to depend on httpx in its own code.
    """


def fetch_features(
    *,
    endpoint: str,
    typename: str,
    protocol: Protocol = "ogc_api_features",
    crs: str = "EPSG:4326",
    bbox: tuple[float, float, float, float] | None = None,
    version: str = "2.0.0",
    max_features: int | None = None,
    query: dict[str, str] | None = None,
    auth: dict[str, str] | None = None,
) -> "gpd.GeoDataFrame":
    """Fetch one collection from a WFS or OGC API Features endpoint.

    Args:
        endpoint: Base URL of the service. For WFS this is the ``?service=WFS``
            URL; for OGC API Features it is the landing page (no trailing
            ``/collections/...``).
        typename: Collection / typeName to fetch (e.g. ``wfs_du:zone_urba``).
        protocol: ``"wfs"`` for classic WFS, ``"ogc_api_features"`` for the
            REST API Features. Default ``"ogc_api_features"``.
        crs: Requested SRS, default ``"EPSG:4326"`` (CRS84 for OAPIF).
        bbox: Server-side spatial filter as ``(minx, miny, maxx, maxy)``.
        version: WFS version (ignored for OGC API Features). Default ``2.0.0``.
        max_features: Per-page limit handed to the underlying client; the
            transport handles pagination above that ceiling.
        query: Extra query parameters merged in (e.g. ``CQL_FILTER`` for WFS).
        auth: Auth dict accepted by the transport (e.g. basic).

    Returns:
        A ``geopandas.GeoDataFrame`` with the requested features and the
        requested CRS.

    Raises:
        OGCEndpointUnreachable: the service could not be reached.
        OGCClientError: any other failure surfaced by the transport (kept as
            ``__cause__``).
        ValueError: invalid ``endpoint``/``typename`` arguments.
    """
    if not endpoint or not isinstance(endpoint, str):
        raise ValueError("endpoint must be a non-empty string")
    if not typename or not isinstance(typename, str):
        raise ValueError("typename must be a non-empty string")
    if protocol not in ("wfs", "ogc_api_features"):
        raise ValueError(
            f"protocol must be 'wfs' or 'ogc_api_features', got {protocol!r}"
        )

    cfg = OGCSourceConfig(
        source_type=protocol,
        url=endpoint,
        layer_name=typename,
        version=version,
        crs=crs,
        auth=auth,
        max_features=max_features,
        params=dict(query or {}),
    )

    # Lazy import — keep the data-pack code path from pulling httpx and
    # geopandas at import time.
    from gispulse.adapters.ogc import wfs_client as _wfs

    try:
        if protocol == "wfs":
            gdf = _wfs.fetch_wfs(cfg, bbox=bbox)
        else:
            gdf = _wfs.fetch_ogc_api_features(cfg, bbox=bbox)
    except Exception as exc:  # noqa: BLE001 — funnel into a typed surface
        if _is_unreachable(exc):
            log.warning(
                "ogc_endpoint_unreachable",
                endpoint=endpoint,
                typename=typename,
                error=str(exc),
            )
            raise OGCEndpointUnreachable(
                f"OGC endpoint unreachable: {endpoint!r} — {exc!s}"
            ) from exc
        raise OGCClientError(
            f"OGC fetch failed for {typename!r} at {endpoint!r}: {exc!s}"
        ) from exc

    log.info(
        "ogc_features_fetched",
        protocol=protocol,
        endpoint=endpoint,
        typename=typename,
        rows=len(gdf),
    )
    return gdf


def _is_unreachable(exc: BaseException) -> bool:
    """Recognise connect / DNS / read-timeout failures across httpx versions.

    We can't always rely on importing httpx (it may not be installed in a
    bare data-pack test env), so we match the class name walking the MRO.
    """
    sentinels = {
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutException",
        "RemoteProtocolError",
        # Stdlib equivalents some transports raise.
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "TimeoutError",
        "OSError",
    }
    for cls in type(exc).mro():
        if cls.__name__ in sentinels:
            return True
    return False
