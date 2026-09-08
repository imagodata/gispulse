"""Opt-in bounded GeoJSON WFS collection using the shared counted pager."""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlencode

from gispulse.adapters.rest.retry import RetrySpec
from gispulse.adapters.rest.offset_pages import OffsetPagination, collect_offset_pages
from gispulse.adapters.rest.rest_fetcher import _get_geojson_with_retry


def fetch_counted_wfs(cfg: Any, params: dict[str, Any], bbox: tuple | None) -> tuple[Any, dict]:
    """Collect WFS 2.0 pages; bbox coordinates are in the declared output CRS.

    A recipe explicitly defines the service's JSON count and stable property ID.
    Existing uncounted WFS sources keep their original transport path.
    """
    import geopandas as gpd
    from pyproj import CRS

    spec = OffsetPagination.from_params(params["pagination"])
    if cfg.version != "2.0.0":
        raise ValueError("WFS_PAGINATION_VERSION: counted mode requires WFS 2.0.0")
    if bbox is None and params.get("require_extent"):
        raise ValueError("WFS_EXTENT_REQUIRED: supply a bounding box in the source CRS")
    if bbox is not None and (
        not all(math.isfinite(c) for c in bbox) or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]
    ):
        raise ValueError("WFS_EXTENT_INVALID: finite ordered bbox required")
    query = {
        "service": "WFS",
        "version": cfg.version,
        "request": "GetFeature",
        "typeNames": cfg.layer_name,
        "outputFormat": "application/json",
        "srsName": cfg.crs,
        **cfg.params,
    }
    if bbox is not None:
        query["bbox"] = ",".join(str(c) for c in bbox) + f",{cfg.crs}"
    if params.get("cql_filter"):
        query["CQL_FILTER"] = params["cql_filter"]
    retry = RetrySpec.from_params(params)

    def request(values):
        payload = _get_geojson_with_retry(
            cfg.url + ("&" if "?" in cfg.url else "?") + urlencode(values), 120, retry
        )
        if (
            isinstance(payload, dict)
            and payload.get("features")
            and not payload.get("crs")
            and not CRS.from_user_input(cfg.crs).equals(CRS.from_epsg(4326), ignore_axis_order=True)
        ):
            raise ValueError("WFS_CRS_MISSING: projected geometry response must advertise its CRS")
        if isinstance(payload, dict) and payload.get("crs"):
            try:
                advertised = CRS.from_user_input(payload["crs"]["properties"]["name"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("WFS_CRS_INVALID: inspect advertised response CRS") from exc
            if not advertised.equals(CRS.from_user_input(cfg.crs), ignore_axis_order=True):
                raise ValueError("WFS_CRS_MISMATCH: response CRS differs from requested CRS")
        return payload

    features, report = collect_offset_pages(spec, query, request)
    frame = (
        gpd.GeoDataFrame.from_features(features, crs=cfg.crs)
        if features
        else gpd.GeoDataFrame(geometry=[], crs=cfg.crs)
    )
    return frame, report
