"""WFS 2.0 GetFeature client for the Géoplateforme public WFS endpoint.

This module provides a thin, stateless helper for fetching GeoJSON features
from any WFS 2.0 endpoint. It has no coupling to any specific plugin or
dataset type — callers supply the layer name, the bounding box, and
optionally an ``httpx.Client`` to control connection pooling.

This is the lightweight runtime-service variant: it returns a plain GeoJSON
dict and performs a single request. For pipeline-grade fetching (source
configs, pagination, GeoParquet caching, GeoDataFrame output) use
:func:`gispulse.adapters.ogc.wfs_client.fetch_wfs` instead.

Closes #68.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    import httpx

from gispulse.utils.http import DataClientError, json_get

#: Default WFS base URL for the French Géoplateforme (IGN).
GEOPLATEFORME_WFS_URL = "https://data.geopf.fr/wfs/ows"


def fetch_wfs_features(
    layer_name: str,
    bbox: tuple[float, float, float, float],
    *,
    client: "httpx.Client | None" = None,
    wfs_url: str = GEOPLATEFORME_WFS_URL,
    max_features: int = 200,
    sort_by: str | None = "gid",
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Run a WFS 2.0 ``GetFeature`` request and return a GeoJSON FeatureCollection.

    BBOX axis order: Géoplateforme WFS treats ``EPSG:4326`` as **lon, lat**
    (legacy WFS 1.1 behaviour), even when version=2.0.0. Sending the strict
    OGC lat, lon ordering returns zero features silently. The function
    therefore takes ``(minlon, minlat, maxlon, maxlat)`` and forwards it
    verbatim. Forcing strict lat, lon would require
    ``urn:ogc:def:crs:EPSG::4326`` as the srsName — Géoplateforme accepts
    that too, but lon, lat with plain ``EPSG:4326`` matches what every other
    WFS client in the ecosystem already does.

    Args:
        layer_name: WFS ``typeNames`` value, e.g. ``"BDPARCELLAIRE-VECTEUR_WLD_BDT_DOMAN:parcelle"``.
        bbox: ``(minlon, minlat, maxlon, maxlat)`` in EPSG:4326.
        client: An ``httpx.Client`` to reuse (connection pooling). When
            ``None`` a new client is created for this call.
        wfs_url: WFS base URL. Defaults to :data:`GEOPLATEFORME_WFS_URL`.
        max_features: ``count`` parameter (WFS 2.0) — maximum number of
            features returned by the server.
        sort_by: Optional ``sortBy`` parameter. Pass ``None`` to omit it.
        timeout: Per-request timeout in seconds.

    Returns:
        A GeoJSON ``FeatureCollection`` dict with a ``"features"`` list.

    Raises:
        DataClientError: The response is non-JSON, missing the ``features``
            key, or otherwise malformed.
    """
    import httpx as _httpx

    params: dict[str, Any] = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": layer_name,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]},EPSG:4326",
        "count": max_features,
    }
    if sort_by:
        params["sortBy"] = sort_by

    if client is not None:
        # Caller owns the client lifecycle — do not close it.
        payload = json_get(client, wfs_url, params=params, timeout=timeout)
    else:
        # We created the client; close it when done to release the connection.
        with _httpx.Client() as _owned:
            payload = json_get(_owned, wfs_url, params=params, timeout=timeout)

    features = payload.get("features")
    if not isinstance(features, list):
        raise DataClientError(
            f"WFS response missing 'features' array for layer {layer_name!r}"
        )
    return {"type": "FeatureCollection", "features": features}
