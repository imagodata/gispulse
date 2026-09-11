"""Opt-in bounded GeoJSON WFS collection using the shared counted pager."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

from gispulse.adapters.rest.retry import RetrySpec, get_json_with_retry
from gispulse.adapters.rest.offset_pages import OffsetPagination, collect_offset_pages
from gispulse.adapters.rest.rest_fetcher import _get_geojson_with_retry


def _get_wfs_hits(url: str, timeout: float) -> dict:
    """Parse only WFS 2.0 hits; unknown counts and service exceptions fail closed."""
    import httpx

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise ValueError("WFS_COUNT_INVALID: malformed XML hits response") from exc
    raw = root.get("numberMatched", "")
    if root.tag != "{http://www.opengis.net/wfs/2.0}FeatureCollection" or not re.fullmatch(
        r"[0-9]+", raw
    ):
        raise ValueError("WFS_COUNT_INVALID: exact WFS numberMatched required")
    return {"count": int(raw)}


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
    spatial_mode = params.get("bbox_filter", "bbox")
    count_format = params.get("count_format", "json")
    if spatial_mode not in ("bbox", "intersects") or count_format not in ("json", "wfs_hits_xml"):
        raise ValueError("WFS_RECIPE_INVALID: unsupported spatial/count mode")
    if count_format == "wfs_hits_xml" and (
        spec.count_key != "count" or spec.count_query.get("resultType") != "hits"
    ):
        raise ValueError("WFS_RECIPE_INVALID: XML hits requires count key and resultType=hits")
    cql = params.get("cql_filter")
    if spatial_mode == "intersects":
        field = params.get("geometry_field")
        if not isinstance(field, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", field):
            raise ValueError("WFS_GEOMETRY_FIELD_INVALID: explicit geometry field required")
        # CQL geometry literals use the native layer CRS, not srsName.
        if params.get("native_crs") is None or not CRS.from_user_input(params["native_crs"]).equals(
            CRS.from_user_input(cfg.crs)
        ):
            raise ValueError("WFS_FILTER_CRS_INVALID: CQL bbox must use declared native CRS")
        if bbox is None:
            raise ValueError("WFS_EXTENT_REQUIRED: INTERSECTS requires a bounding box")
        x, y, X, Y = bbox
        spatial = f"INTERSECTS({field},POLYGON(({x} {y},{X} {y},{X} {Y},{x} {Y},{x} {y})))"
        query["CQL_FILTER"] = f"({spatial}) AND ({cql})" if cql else spatial
    else:
        if bbox is not None:
            query["bbox"] = ",".join(str(c) for c in bbox) + f",{cfg.crs}"
        if cql:
            query["CQL_FILTER"] = cql
    retry = RetrySpec.from_params(params)

    def request(values):
        url = cfg.url + ("&" if "?" in cfg.url else "?") + urlencode(values)
        if count_format == "wfs_hits_xml" and values.get("resultType") == "hits":
            return get_json_with_retry(_get_wfs_hits, url, 120, retry, retry_event="wfs_hits_retry")
        payload = _get_geojson_with_retry(url, 120, retry)
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
