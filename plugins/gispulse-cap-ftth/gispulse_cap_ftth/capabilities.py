"""FTTH network design capabilities.

Provides NRO/SRO/PBO placement and cable routing on road networks.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from shapely.geometry import MultiPoint, Point

from capabilities.base import Capability
from capabilities.registry import register


@register
class FTTHCoverageCapability(Capability):
    """Compute FTTH coverage zones from point infrastructure."""

    name = "ftth_coverage"
    description = "Generate coverage polygons around FTTH infrastructure points (NRO/SRO/PBO)."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "radius_m": {
                    "type": "number",
                    "description": "Coverage radius in meters",
                    "default": 500,
                },
                "infrastructure_type": {
                    "type": "string",
                    "enum": ["NRO", "SRO", "PBO", "all"],
                    "default": "all",
                    "description": "Type of infrastructure to generate coverage for",
                },
            },
            "additionalProperties": False,
        }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        *,
        radius_m: float = 500,
        infrastructure_type: str = "all",
        **_kw,
    ) -> gpd.GeoDataFrame:
        result = gdf.copy()
        if infrastructure_type != "all" and "type" in result.columns:
            result = result[result["type"] == infrastructure_type].copy()
        if result.crs and not result.crs.is_projected:
            result = result.to_crs(epsg=3857)
            was_geographic = True
        else:
            was_geographic = False
        result["geometry"] = result.geometry.buffer(radius_m)
        if was_geographic:
            result = result.to_crs(epsg=4326)
        return result


@register
class FTTHNearestNodeCapability(Capability):
    """Snap points to nearest road network node for FTTH routing."""

    name = "ftth_nearest_node"
    description = "Snap subscriber points to the nearest road network node."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "max_distance_m": {
                    "type": "number",
                    "description": "Maximum snap distance in meters",
                    "default": 100,
                },
            },
            "additionalProperties": False,
        }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        *,
        max_distance_m: float = 100,
        road_network: gpd.GeoDataFrame | None = None,
        **_kw,
    ) -> gpd.GeoDataFrame:
        result = gdf.copy()
        if road_network is None:
            result["snap_status"] = "no_network"
            return result

        from shapely.ops import nearest_points

        snapped = []
        statuses = []
        for _, row in result.iterrows():
            pt = row.geometry
            nearest = nearest_points(pt, road_network.union_all())[1]
            dist = pt.distance(nearest)
            if dist <= max_distance_m:
                snapped.append(nearest)
                statuses.append("snapped")
            else:
                snapped.append(pt)
                statuses.append("too_far")

        result["geometry"] = snapped
        result["snap_status"] = statuses
        return result
