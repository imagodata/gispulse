from __future__ import annotations


import geopandas as gpd
import numpy as np

from capabilities.base import Capability
from capabilities.registry import register



@register
class ChaikinSmoothCapability(Capability):
    """Chaikin corner-cutting smoother for lines and polygon boundaries."""

    name = "chaikin_smooth"
    description = (
        "Iterative corner-cutting smoother (Chaikin). Each pass replaces "
        "each segment with two new vertices at the 1/4 and 3/4 points."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        iterations: int = 2,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame.
            iterations: Number of refinement passes (>=1). Each pass roughly
                        doubles the vertex count.
        """
        from shapely.geometry import LineString, Polygon, MultiPolygon, MultiLineString

        if iterations < 1:
            raise ValueError("iterations must be >= 1.")
        if gdf.empty:
            return gdf.copy()

        def _chaikin_ring(coords: list[tuple]) -> list[tuple]:
            if len(coords) < 3:
                return coords
            new_coords = []
            for i in range(len(coords) - 1):
                p0 = np.array(coords[i])
                p1 = np.array(coords[i + 1])
                q = 0.75 * p0 + 0.25 * p1
                r = 0.25 * p0 + 0.75 * p1
                new_coords.append(tuple(q))
                new_coords.append(tuple(r))
            return new_coords

        def _chaikin_open(coords: list[tuple]) -> list[tuple]:
            if len(coords) < 2:
                return coords
            new_coords = [coords[0]]
            for i in range(len(coords) - 1):
                p0 = np.array(coords[i])
                p1 = np.array(coords[i + 1])
                q = 0.75 * p0 + 0.25 * p1
                r = 0.25 * p0 + 0.75 * p1
                new_coords.append(tuple(q))
                new_coords.append(tuple(r))
            new_coords.append(coords[-1])
            return new_coords

        def _smooth(geom):
            if geom is None or geom.is_empty:
                return geom
            gt = geom.geom_type
            if gt == "LineString":
                coords = list(geom.coords)
                for _ in range(iterations):
                    coords = _chaikin_open(coords)
                return LineString(coords)
            if gt == "Polygon":
                ext = list(geom.exterior.coords)
                for _ in range(iterations):
                    # Close the ring for corner-cutting, reopen at the end.
                    if ext[0] != ext[-1]:
                        ext = ext + [ext[0]]
                    ext = _chaikin_ring(ext)
                    ext = ext + [ext[0]]
                return Polygon(
                    ext,
                    holes=[_smooth(LineString(list(hole.coords))).coords for hole in geom.interiors],
                )
            if gt == "MultiPolygon":
                return MultiPolygon([_smooth(p) for p in geom.geoms])
            if gt == "MultiLineString":
                return MultiLineString([_smooth(ln) for ln in geom.geoms])
            return geom

        result = gdf.copy()
        result["geometry"] = [_smooth(g) for g in gdf.geometry]
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "iterations": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 2,
                    "description": "Number of Chaikin passes.",
                },
            },
        }
