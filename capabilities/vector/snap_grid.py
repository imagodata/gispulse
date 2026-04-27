from __future__ import annotations


import geopandas as gpd

from capabilities.base import Capability
from capabilities.registry import register


@register
class SnapToGridCapability(Capability):
    """Snaps geometry vertices to a regular grid — useful to remove noise."""

    name = "snap_to_grid"
    description = (
        "Snaps vertex coordinates to a regular grid. "
        "Reduces noise and ensures topological equality between adjacent layers."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        grid_size: float = 0.001,
        crs_meters: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame.
            grid_size:  Grid cell size in the CRS units (or *crs_meters* if set).
            crs_meters: If set, reproject to this metric CRS before snapping,
                        then project back. Use when grid_size is in meters.
        """
        from shapely import set_precision

        if grid_size <= 0:
            raise ValueError("grid_size must be > 0.")
        if gdf.empty:
            return gdf.copy()

        if crs_meters is not None and gdf.crs is not None:
            original_crs = gdf.crs
            work = gdf.to_crs(crs_meters)
            work["geometry"] = [set_precision(g, grid_size) for g in work.geometry]
            return work.to_crs(original_crs)

        result = gdf.copy()
        result["geometry"] = [set_precision(g, grid_size) for g in gdf.geometry]
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "grid_size": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Grid cell size (in native CRS units, or meters if crs_meters is set).",
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "description": "Optional metric CRS for grid_size interpretation.",
                },
            },
            "required": ["grid_size"],
        }


