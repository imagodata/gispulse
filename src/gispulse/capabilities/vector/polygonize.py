from __future__ import annotations


import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register


@register
class PolygonizeCapability(Capability):
    """Turns noded linework into polygons (GEOS polygonize)."""

    name = "polygonize"
    description = (
        "Builds polygons from a set of noded linestrings. "
        "Inputs must share exact endpoints — use snap_to_grid first if needed."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        keep_attrs: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame of LineStrings.
            keep_attrs: Unused for now — polygonized output does not map 1:1
                        to input rows. Kept for forward compatibility.
        """
        from shapely.ops import polygonize as _polygonize

        if gdf.empty:
            return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)

        polys = list(_polygonize(list(gdf.geometry)))
        return gpd.GeoDataFrame(geometry=polys, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keep_attrs": {
                    "type": "boolean",
                    "default": False,
                    "description": "Reserved for future use.",
                },
            },
        }


