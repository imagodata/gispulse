"""OGC fetchers — WFS + OGC API Features in the ProtocolRegistry (issue #192).

PR #189 delivered the :class:`~core.sources.ProtocolRegistry` /
:class:`~core.sources.Fetcher` contract, but no real transport adapter
was registered — so a :class:`~core.sources.DeclarativeSource` had
nothing to dispatch ``fetch()`` to. This module wraps the existing
paginating OGC clients
(:func:`gispulse.adapters.ogc.wfs_client.fetch_wfs` and
:func:`~gispulse.adapters.ogc.wfs_client.fetch_ogc_api_features`) as
:class:`~core.sources.Fetcher` adapters and registers them under
:attr:`~core.plugin_model.AccessProtocol.WFS` and
:attr:`~core.plugin_model.AccessProtocol.OGC_FEATURES`.

Importing this module self-registers both fetchers in the process-wide
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

# AccessSpec.params keys the fetchers consume themselves. Every *other*
# key is forwarded verbatim to the OGC request as a vendor parameter.
_RESERVED_PARAMS = frozenset(
    {
        "typename",
        "typenames",
        "layer",
        "collection",
        "version",
        "crs",
        "max_features",
        "cql_filter",
    }
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


def _ogc_config_from_access(access: AccessSpec, *, source_type: str):
    """Translate an :class:`AccessSpec` into an ``OGCSourceConfig``.

    Returns ``(cfg, params)`` — ``params`` is the parsed ``AccessSpec``
    param dict so a caller can still read protocol-specific keys
    (e.g. WFS's ``cql_filter``). Raises :class:`ValueError` when no layer
    / collection is declared.
    """
    from gispulse.core.models import OGCSourceConfig

    params = dict(access.params or {})
    layer = (
        params.get("collection")
        or params.get("typename")
        or params.get("typenames")
        or params.get("layer")
    )
    if not layer:
        raise ValueError(
            "OGC AccessSpec.params must declare a 'typename' (WFS layer) "
            "or 'collection' (OGC API Features collection)"
        )
    crs = str(params.get("crs", "EPSG:4326"))
    vendor = {k: v for k, v in params.items() if k not in _RESERVED_PARAMS}
    cfg = OGCSourceConfig(
        source_type=source_type,
        url=access.endpoint,
        layer_name=str(layer),
        version=str(params.get("version", "2.0.0")),
        crs=crs,
        auth=None,
        max_features=params.get("max_features"),
        params=vendor,
    )
    return cfg, params


class WfsFetcher:
    """:class:`~core.sources.Fetcher` for ``AccessProtocol.WFS``."""

    protocol = AccessProtocol.WFS

    def fetch(
        self,
        access: AccessSpec,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        from gispulse.adapters.ogc.wfs_client import fetch_wfs

        cfg, params = _ogc_config_from_access(access, source_type="wfs")
        gdf = fetch_wfs(
            cfg,
            bbox=_bbox_from_extent(extent),
            cql_filter=params.get("cql_filter"),
        )
        log.info(
            "wfs_fetch",
            endpoint=access.endpoint,
            layer=cfg.layer_name,
            features=len(gdf),
        )
        return SourceResult(
            payload=Payload.VECTOR,
            mode=mode,
            data=gdf,
            crs=cfg.crs,
            metadata={"layer": cfg.layer_name, "feature_count": len(gdf)},
        )


class OgcFeaturesFetcher:
    """:class:`~core.sources.Fetcher` for ``AccessProtocol.OGC_FEATURES``.

    The modern OGC API - Features (GeoJSON, ``/collections/{id}/items``,
    ``next``-link pagination). The entry's layer is the collection id —
    declare it as ``collection`` (preferred) or ``typename``.
    """

    protocol = AccessProtocol.OGC_FEATURES

    def fetch(
        self,
        access: AccessSpec,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        from gispulse.adapters.ogc.wfs_client import fetch_ogc_api_features

        cfg, _params = _ogc_config_from_access(
            access, source_type="ogc_api_features"
        )
        gdf = fetch_ogc_api_features(cfg, bbox=_bbox_from_extent(extent))
        log.info(
            "ogc_features_fetch",
            endpoint=access.endpoint,
            collection=cfg.layer_name,
            features=len(gdf),
        )
        return SourceResult(
            payload=Payload.VECTOR,
            mode=mode,
            data=gdf,
            crs=cfg.crs,
            metadata={"collection": cfg.layer_name, "feature_count": len(gdf)},
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


def register_wfs_fetcher(registry: Any | None = None) -> None:
    """Register a :class:`WfsFetcher` (default registry: PROTOCOLS)."""
    _register(registry, WfsFetcher(), AccessProtocol.WFS)


def register_ogc_features_fetcher(registry: Any | None = None) -> None:
    """Register an :class:`OgcFeaturesFetcher` (default: PROTOCOLS)."""
    _register(registry, OgcFeaturesFetcher(), AccessProtocol.OGC_FEATURES)


def register_core_ogc_fetchers(registry: Any | None = None) -> None:
    """Register every built-in OGC fetcher in ``registry``."""
    register_wfs_fetcher(registry)
    register_ogc_features_fetcher(registry)


# Importing this module wires the fetchers into the global registry.
register_core_ogc_fetchers()


__all__ = [
    "OgcFeaturesFetcher",
    "WfsFetcher",
    "register_core_ogc_fetchers",
    "register_ogc_features_fetcher",
    "register_wfs_fetcher",
]
