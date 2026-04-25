from __future__ import annotations

import ast as _ast
import re as _re

import geopandas as gpd
import numpy as np
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register
from capabilities.strategy import ExecutionContext, ExecutionStrategy, StrategyMode
from capabilities.vector.extract_ops import _iter_coords


@register
class DelaunayTriangulationCapability(Capability):
    """Delaunay triangulation of the input vertices."""

    name = "delaunay_triangulation"
    description = (
        "Computes the Delaunay triangulation of the vertices. "
        "Dual of the Voronoi diagram."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        tolerance: float = 0.0,
        only_edges: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame.
            tolerance:  Snapping tolerance to resolve coincident vertices.
            only_edges: Return the edges (LineStrings) instead of triangles.
        """
        from shapely import delaunay_triangles

        if gdf.empty:
            return gdf.copy()

        union = gdf.geometry.union_all()
        result_geom = delaunay_triangles(union, tolerance=tolerance, only_edges=only_edges)
        tris = list(result_geom.geoms) if hasattr(result_geom, "geoms") else [result_geom]
        return gpd.GeoDataFrame(geometry=tris, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tolerance": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.0,
                    "description": "Snapping tolerance.",
                },
                "only_edges": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return edges instead of triangles.",
                },
            },
        }


# ---------------------------------------------------------------------------
# Advanced geometry — min bounding circle, oriented bbox, alpha shape,
# Chaikin smoothing, linear referencing, symmetric difference.
# ---------------------------------------------------------------------------


@register
class MinBoundingCircleCapability(Capability):
    """Replaces each geometry with its minimum bounding circle."""

    name = "min_bounding_circle"
    description = (
        "Computes the minimum bounding circle of each feature "
        "(Shapely ≥2.1). Useful for impact radius and morphometry."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        dissolve: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:      Input GeoDataFrame.
            dissolve: When True, returns a single row holding the MBC of the
                      union of all inputs.
        """
        from shapely import minimum_bounding_circle

        if gdf.empty:
            return gdf.copy()

        if dissolve:
            hull = minimum_bounding_circle(gdf.geometry.union_all())
            return gpd.GeoDataFrame(geometry=[hull], crs=gdf.crs)

        result = gdf.copy()
        result["geometry"] = [minimum_bounding_circle(g) for g in gdf.geometry]
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "dissolve": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return a single MBC over all features.",
                },
            },
        }


@register
class OrientedBBoxCapability(Capability):
    """Minimum **rotated** rectangle — tighter than envelope for angled shapes."""

    name = "oriented_bbox"
    description = (
        "Computes the minimum rotated rectangle of each feature. "
        "Tighter than envelope when shapes are angled (buildings, parcels)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        dissolve: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:      Input GeoDataFrame.
            dissolve: When True, returns the oriented bbox of the union.
        """
        from shapely import oriented_envelope

        if gdf.empty:
            return gdf.copy()

        if dissolve:
            return gpd.GeoDataFrame(
                geometry=[oriented_envelope(gdf.geometry.union_all())],
                crs=gdf.crs,
            )

        result = gdf.copy()
        result["geometry"] = [oriented_envelope(g) for g in gdf.geometry]
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "dissolve": {"type": "boolean", "default": False},
            },
        }


@register
class AlphaShapeCapability(Capability):
    """Alpha shape — tighter generalisation of the convex hull.

    The alpha shape is computed from the Delaunay triangulation of the
    input vertices, keeping only triangles whose circumradius is smaller
    than ``1/alpha``. Small alpha → convex hull; large alpha → tight shape.
    """

    name = "alpha_shape"
    description = (
        "Alpha shape (generalised concave hull) from vertex cloud, "
        "parametrised by alpha (1/max circumradius)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        alpha: float = 0.05,
        by_group: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:      Input GeoDataFrame (vertices used as seed cloud).
            alpha:    Shape tightness (> 0). Small = convex, large = tight.
            by_group: Column to group by — one shape per group.
        """
        from shapely.ops import polygonize, unary_union
        from shapely.geometry import MultiPoint, LineString

        if alpha <= 0:
            raise ValueError("alpha must be > 0.")
        if gdf.empty:
            return gdf.copy()

        def _alpha_shape(geoms: list) -> Any:
            pts = []
            for g in geoms:
                if g is None or g.is_empty:
                    continue
                pts.extend(_iter_coords(g))
            if len(pts) < 4:
                return MultiPoint(pts).convex_hull
            from scipy.spatial import Delaunay

            coords = np.array(pts)
            tri = Delaunay(coords)
            edges = set()
            for ia, ib, ic in tri.simplices:
                pa, pb, pc = coords[ia], coords[ib], coords[ic]
                # Triangle circumradius
                a = np.linalg.norm(pa - pb)
                b = np.linalg.norm(pb - pc)
                c = np.linalg.norm(pc - pa)
                s = (a + b + c) / 2.0
                area_sq = max(s * (s - a) * (s - b) * (s - c), 0.0)
                if area_sq == 0:
                    continue
                r = (a * b * c) / (4.0 * np.sqrt(area_sq))
                if r < 1.0 / alpha:
                    for i, j in ((ia, ib), (ib, ic), (ic, ia)):
                        a_i, b_i = (i, j) if i < j else (j, i)
                        edges.add((a_i, b_i))
            if not edges:
                return MultiPoint(pts).convex_hull
            lines = [LineString([coords[i], coords[j]]) for i, j in edges]
            polys = list(polygonize(lines))
            if not polys:
                return MultiPoint(pts).convex_hull
            return unary_union(polys)

        if by_group and by_group in gdf.columns:
            rows = []
            for group_value, sub in gdf.groupby(by_group):
                rows.append(
                    {by_group: group_value, "geometry": _alpha_shape(list(sub.geometry))}
                )
            return gpd.GeoDataFrame(rows, crs=gdf.crs)

        shape = _alpha_shape(list(gdf.geometry))
        return gpd.GeoDataFrame(geometry=[shape], crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "alpha": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.05,
                    "description": "Shape tightness (small=convex, large=tight).",
                },
                "by_group": {"type": ["string", "null"]},
            },
        }

