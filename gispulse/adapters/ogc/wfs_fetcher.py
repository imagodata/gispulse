"""WFS Fetcher — absorbs ``wfs_client`` into the ProtocolRegistry (issue #192).

PR #189 delivered the :class:`~core.sources.ProtocolRegistry` /
:class:`~core.sources.Fetcher` contract, but no real transport adapter
was registered — so a :class:`~core.sources.DeclarativeSource` had
nothing to dispatch ``fetch()`` to. This module wraps the existing
paginating WFS client (:func:`gispulse.adapters.ogc.wfs_client.fetch_wfs`)
as a :class:`~core.sources.Fetcher` and registers it under
:attr:`~core.plugin_model.AccessProtocol.WFS`.

Importing this module self-registers the fetcher in the process-wide
:data:`core.sources.PROTOCOLS` registry (idempotent), so
``gispulse-src-cadastre`` and the like resolve a real WFS round-trip.
"""

from __future__ import annotations

from typing import Any

from core.logging import get_logger
from core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
)

log = get_logger(__name__)

# AccessSpec.params keys the fetcher consumes itself. Every *other* key
# is forwarded verbatim to the WFS request as a vendor parameter.
_RESERVED_PARAMS = frozenset(
    {"typename", "typenames", "layer", "version", "crs", "max_features", "cql_filter"}
)


def _bbox_from_extent(
    extent: Any,
) -> tuple[float, float, float, float] | None:
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


class WfsFetcher:
    """:class:`~core.sources.Fetcher` for ``AccessProtocol.WFS``.

    Translates an :class:`~core.plugin_model.AccessSpec` into the
    ``OGCSourceConfig`` the legacy paginating client expects, runs the
    request, and wraps the resulting GeoDataFrame in a
    :class:`~core.plugin_model.SourceResult`.
    """

    protocol = AccessProtocol.WFS

    def fetch(
        self,
        access: AccessSpec,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        from core.models import OGCSourceConfig
        from gispulse.adapters.ogc.wfs_client import fetch_wfs

        params = dict(access.params or {})
        layer = (
            params.get("typename")
            or params.get("typenames")
            or params.get("layer")
        )
        if not layer:
            raise ValueError(
                "WFS AccessSpec.params must declare a 'typename' "
                "(the WFS layer / featureType to request)"
            )

        crs = str(params.get("crs", "EPSG:4326"))
        vendor = {k: v for k, v in params.items() if k not in _RESERVED_PARAMS}
        cfg = OGCSourceConfig(
            source_type="wfs",
            url=access.endpoint,
            layer_name=str(layer),
            version=str(params.get("version", "2.0.0")),
            crs=crs,
            auth=None,
            max_features=params.get("max_features"),
            params=vendor,
        )

        gdf = fetch_wfs(
            cfg,
            bbox=_bbox_from_extent(extent),
            cql_filter=params.get("cql_filter"),
        )
        log.info(
            "wfs_fetch",
            endpoint=access.endpoint,
            layer=str(layer),
            features=len(gdf),
        )
        return SourceResult(
            payload=Payload.VECTOR,
            mode=mode,
            data=gdf,
            crs=crs,
            metadata={"layer": str(layer), "feature_count": len(gdf)},
        )


def register_wfs_fetcher(registry: Any | None = None) -> None:
    """Register a :class:`WfsFetcher` in ``registry`` (default: PROTOCOLS).

    Idempotent — a second call is a no-op rather than a duplicate-slot
    error, so importing this module repeatedly is safe.
    """
    from core.sources import PROTOCOLS, ProtocolNotSupported

    target = registry if registry is not None else PROTOCOLS
    try:
        target.get_fetcher(AccessProtocol.WFS)
        return  # already registered
    except ProtocolNotSupported:
        pass
    target.register(WfsFetcher())


# Importing this module wires the fetcher into the global registry.
register_wfs_fetcher()


__all__ = ["WfsFetcher", "register_wfs_fetcher"]
