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
class MakeValidCapability(Capability):
    """Repairs invalid geometries using shapely.make_valid."""

    name = "make_valid"
    description = (
        "Repairs invalid geometries (self-intersections, "
        "duplicate rings, etc.) using shapely.make_valid."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        drop_empty: bool = True,
        keep_geom_type: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:             Input GeoDataFrame.
            drop_empty:      If True, remove features whose repaired geometry
                             is empty (cannot be rescued).
            keep_geom_type:  If True, drop features whose repaired geometry
                             type no longer matches the original (e.g. a
                             polygon that degenerated into a linestring).
        """
        from shapely import make_valid
        from shapely.geometry.base import BaseGeometry

        if gdf.empty:
            return gdf.copy()

        originals = gdf.geometry
        repaired: list[BaseGeometry | None] = []
        for geom in originals:
            if geom is None or geom.is_empty:
                repaired.append(geom)
                continue
            if geom.is_valid:
                repaired.append(geom)
                continue
            try:
                repaired.append(make_valid(geom))
            except Exception:
                repaired.append(None)

        result = gdf.copy()
        result["geometry"] = repaired

        if keep_geom_type:
            original_types = originals.geom_type
            new_types = result.geometry.geom_type
            mask = (new_types == original_types) | result.geometry.isna()
            result = result[mask].reset_index(drop=True)

        if drop_empty:
            mask = ~result.geometry.is_empty & result.geometry.notna()
            result = result[mask].reset_index(drop=True)

        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "drop_empty": {
                    "type": "boolean",
                    "default": True,
                    "description": "Drop features whose repaired geometry is empty.",
                },
                "keep_geom_type": {
                    "type": "boolean",
                    "default": False,
                    "description": "Drop features whose repaired type changed.",
                },
            },
        }


@register
class ConvexHullCapability(Capability):
    """Replaces each geometry with its convex hull, or computes a global hull."""

    name = "convex_hull"
    description = (
        "Replaces geometries with their convex hull. "
        "Use by_group or dissolve to compute a hull per cluster."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        by_group: str | None = None,
        dissolve: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:       Input GeoDataFrame.
            by_group:  Column name to group by before computing the hull.
                       When set, produces one hull per group (all other
                       group members are aggregated by ``first``).
            dissolve:  When True and by_group is None, returns a single-row
                       GeoDataFrame holding the convex hull of the entire
                       collection.
        """
        if gdf.empty:
            return gdf.copy()

        if by_group and by_group in gdf.columns:
            merged = gdf.dissolve(by=by_group, aggfunc="first").reset_index()
            merged["geometry"] = merged.geometry.convex_hull
            return merged

        if dissolve:
            hull = gdf.geometry.union_all().convex_hull
            return gpd.GeoDataFrame(geometry=[hull], crs=gdf.crs)

        result = gdf.copy()
        result["geometry"] = gdf.geometry.convex_hull
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "by_group": {
                    "type": ["string", "null"],
                    "description": "Column to group by before computing one hull per group.",
                },
                "dissolve": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return a single hull over all features.",
                },
            },
        }


@register
class EnvelopeCapability(Capability):
    """Replaces each geometry with its bounding-box envelope."""

    name = "envelope"
    description = "Replaces each geometry with its axis-aligned bounding box."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        by_group: str | None = None,
        dissolve: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:       Input GeoDataFrame.
            by_group:  Column name to group by — produces one envelope per group.
            dissolve:  Return a single envelope over the whole layer when True
                       and by_group is None.
        """
        if gdf.empty:
            return gdf.copy()

        if by_group and by_group in gdf.columns:
            merged = gdf.dissolve(by=by_group, aggfunc="first").reset_index()
            merged["geometry"] = merged.geometry.envelope
            return merged

        if dissolve:
            env = gdf.geometry.union_all().envelope
            return gpd.GeoDataFrame(geometry=[env], crs=gdf.crs)

        result = gdf.copy()
        result["geometry"] = gdf.geometry.envelope
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "by_group": {
                    "type": ["string", "null"],
                    "description": "Column to group by — one envelope per group.",
                },
                "dissolve": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return a single envelope over all features.",
                },
            },
        }
