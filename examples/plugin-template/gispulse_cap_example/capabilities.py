"""Example capability — centroid calculation.

This shows how to implement a GISPulse capability as an external plugin.
"""

from __future__ import annotations

import geopandas as gpd

from gispulse.plugins.api import Capability, register_capability


@register_capability
class CentroidCapability(Capability):
    """Compute the centroid of each geometry."""

    name = "centroid"
    description = "Replace geometries with their centroid."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def execute(self, gdf: gpd.GeoDataFrame, **_params: object) -> gpd.GeoDataFrame:
        result = gdf.copy()
        result["geometry"] = result.geometry.centroid
        return result
