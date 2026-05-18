from __future__ import annotations


import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register




@register
class ConcaveHullCapability(Capability):
    """Concave hull — tighter than convex hull, controlled by ratio."""

    name = "concave_hull"
    description = (
        "Computes a concave hull (k-nearest / ratio-based). "
        "Tighter than convex hull — follows the shape of point clouds."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ratio: float = 0.3,
        allow_holes: bool = False,
        by_group: str | None = None,
        dissolve: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:         Input GeoDataFrame.
            ratio:       Concavity in [0..1]. 0 = most concave, 1 = convex hull.
                         Typical values: 0.2 – 0.5.
            allow_holes: Permit interior holes in the hull (shapely ≥2.0).
            by_group:    Column to group by — one hull per group.
            dissolve:    Return a single hull over all features when True.
        """
        from shapely import concave_hull

        if gdf.empty:
            return gdf.copy()
        if not (0.0 <= ratio <= 1.0):
            raise ValueError("ratio must be in [0.0, 1.0].")

        def _hull(geoms):
            union = gpd.GeoSeries(geoms, crs=gdf.crs).union_all()
            return concave_hull(union, ratio=ratio, allow_holes=allow_holes)

        if by_group and by_group in gdf.columns:
            rows: list[dict] = []
            for group, sub in gdf.groupby(by_group):
                rows.append({by_group: group, "geometry": _hull(sub.geometry)})
            return gpd.GeoDataFrame(rows, crs=gdf.crs)

        if dissolve:
            return gpd.GeoDataFrame(geometry=[_hull(gdf.geometry)], crs=gdf.crs)

        result = gdf.copy()
        result["geometry"] = [
            concave_hull(g, ratio=ratio, allow_holes=allow_holes) for g in gdf.geometry
        ]
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ratio": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.3,
                    "description": "Concavity (0=concave, 1=convex). Typical 0.2-0.5.",
                },
                "allow_holes": {
                    "type": "boolean",
                    "default": False,
                    "description": "Allow interior holes in the hull.",
                },
                "by_group": {
                    "type": ["string", "null"],
                    "description": "Column to group by — one hull per group.",
                },
                "dissolve": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return a single hull over all features.",
                },
            },
        }


