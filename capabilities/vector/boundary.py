from __future__ import annotations


import geopandas as gpd

from capabilities.base import Capability
from capabilities.registry import register


# Boundary — extract polygon outlines as lines
# ---------------------------------------------------------------------------


@register
class BoundaryCapability(Capability):
    """Replaces each geometry with its boundary (polygon → line, line → endpoints)."""

    name = "boundary"
    description = "Replaces each geometry with its topological boundary."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        drop_empty: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        if gdf.empty:
            return gdf.copy()
        result = gdf.copy()
        result["geometry"] = gdf.geometry.boundary
        if drop_empty:
            result = result[~result.geometry.is_empty].reset_index(drop=True)
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "drop_empty": {
                    "type": "boolean",
                    "default": True,
                    "description": "Drop features whose boundary is empty (e.g. points).",
                },
            },
        }

