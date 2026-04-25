from __future__ import annotations

import ast as _ast
import re as _re

import geopandas as gpd
import numpy as np
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register
from capabilities.strategy import ExecutionContext, ExecutionStrategy, StrategyMode


@register
class VoronoiPolygonsCapability(Capability):
    """Voronoi diagram of input points."""

    name = "voronoi_polygons"
    description = (
        "Computes the Voronoi polygons of the input points. "
        "Each cell contains the region closest to one input point."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        tolerance: float = 0.0,
        extend_to_wkt: str | None = None,
        only_edges: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           Input GeoDataFrame (points or any geometry; vertices
                           are used as input seeds).
            tolerance:     Snapping tolerance to resolve co-circular points.
            extend_to_wkt: Optional WKT geometry defining the clip envelope.
            only_edges:    Return the dual edges instead of the polygons.
        """
        from shapely import voronoi_polygons
        from shapely import wkt as _wkt

        if gdf.empty:
            return gdf.copy()

        union = gdf.geometry.union_all()
        extend_to = _wkt.loads(extend_to_wkt) if extend_to_wkt else None
        result_geom = voronoi_polygons(
            union,
            tolerance=tolerance,
            extend_to=extend_to,
            only_edges=only_edges,
        )
        cells = list(result_geom.geoms) if hasattr(result_geom, "geoms") else [result_geom]
        return gpd.GeoDataFrame(geometry=cells, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tolerance": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.0,
                    "description": "Snapping tolerance for co-circular points.",
                },
                "extend_to_wkt": {
                    "type": ["string", "null"],
                    "description": "Optional WKT envelope to clip the diagram.",
                },
                "only_edges": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return edges instead of polygons.",
                },
            },
        }


