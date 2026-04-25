"""CRS helpers — metric-vs-angular reprojection utilities.

Most GISPulse capabilities that compute distances, lengths, areas, or graph
costs need a *projected* (metric) CRS to return correct numbers. Source
datasets are often in EPSG:4326 (degrees, angular) where a naive
``.length`` or ``.buffer(300)`` silently yields wrong results.

The functions here centralise the reprojection logic so every capability
can opt into the same behaviour: ``to_metric(gdf, crs_meters)`` reprojects
only when the input is angular, and ``suggest_metric_crs(gdf)`` picks a
sensible projected CRS from the data extent (Lambert-93 for France,
local UTM zone otherwise).
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd


FRANCE_METROPOLITAN_BBOX = (-5.5, 41.0, 10.0, 51.5)  # lon_min, lat_min, lon_max, lat_max
LAMBERT_93 = "EPSG:2154"
WEB_MERCATOR = "EPSG:3857"


def is_angular(gdf: gpd.GeoDataFrame | None) -> bool:
    """Return True when the GDF has a geographic (angular) CRS.

    A missing CRS is treated as non-angular (we have no basis to reproject).
    """
    if gdf is None or gdf.crs is None:
        return False
    return not bool(getattr(gdf.crs, "is_projected", False))


def to_metric(
    gdf: gpd.GeoDataFrame,
    crs_meters: str = WEB_MERCATOR,
) -> tuple[gpd.GeoDataFrame, Any]:
    """Reproject ``gdf`` to a metric CRS when its CRS is angular.

    Returns ``(gdf_metric, original_crs)``. If the GDF is already
    projected or has no CRS, the input is returned unchanged and
    ``original_crs`` reflects that state. Callers should reproject
    results back with ``result.to_crs(original_crs)`` when needed.
    """
    if gdf is None or gdf.empty:
        return gdf, gdf.crs if gdf is not None else None

    original_crs = gdf.crs
    if is_angular(gdf):
        return gdf.to_crs(crs_meters), original_crs
    return gdf, original_crs


def suggest_metric_crs(gdf: gpd.GeoDataFrame) -> str:
    """Pick a sensible metric CRS from the GDF extent.

    - If the bounds fall within metropolitan France, return EPSG:2154
      (Lambert-93 — official French CRS, <1 m error nationwide).
    - Otherwise, compute the local UTM zone from the centroid and
      return the matching EPSG code (northern or southern hemisphere).
    - Falls back to EPSG:3857 when no bounds are available.

    The chosen CRS is a *recommendation*; capabilities that already
    accept ``crs_meters`` can use this as their auto-default.
    """
    if gdf is None or gdf.empty:
        return WEB_MERCATOR

    if is_angular(gdf):
        bounds = gdf.total_bounds
    else:
        try:
            bounds = gdf.to_crs("EPSG:4326").total_bounds
        except Exception:
            return WEB_MERCATOR

    lon_min, lat_min, lon_max, lat_max = bounds
    if any(v != v for v in (lon_min, lat_min, lon_max, lat_max)):  # NaN check
        return WEB_MERCATOR

    fl_lon_min, fl_lat_min, fl_lon_max, fl_lat_max = FRANCE_METROPOLITAN_BBOX
    if (
        lon_min >= fl_lon_min and lon_max <= fl_lon_max
        and lat_min >= fl_lat_min and lat_max <= fl_lat_max
    ):
        return LAMBERT_93

    # Local UTM zone from centroid
    cx = (lon_min + lon_max) / 2.0
    cy = (lat_min + lat_max) / 2.0
    zone = int((cx + 180) // 6) + 1
    zone = max(1, min(60, zone))
    epsg = 32600 + zone if cy >= 0 else 32700 + zone
    return f"EPSG:{epsg}"
