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
class CentroidCapability(Capability):
    """Replaces geometries with their centroids."""

    name = "centroid"
    description = "Replaces each feature's geometry with its centroid point."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        **_,
    ) -> gpd.GeoDataFrame:
        result = gdf.copy()
        result["geometry"] = gdf.geometry.centroid
        return result

    def get_schema(self) -> dict:
        return {"type": "object", "properties": {}}


@register
class AreaLengthCapability(Capability):
    """Adds area_m2 and/or length_m columns computed in a metric CRS."""

    name = "area_length"
    description = "Computes area (m2) and/or length (m) and adds them as columns."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        crs_meters: str = "EPSG:3857",
        area_col: str = "area_m2",
        length_col: str = "length_m",
        compute_area: bool = True,
        compute_length: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:            Input GeoDataFrame.
            crs_meters:     Metric CRS for computation.
            area_col:       Column name for area results.
            length_col:     Column name for length results.
            compute_area:   Whether to compute area.
            compute_length: Whether to compute length.
        """
        if gdf.empty:
            return gdf.copy()
        result = gdf.copy()
        if gdf.crs is None:
            # No CRS — compute in native units
            source = gdf
        else:
            source = gdf.to_crs(crs_meters)
        if compute_area:
            result[area_col] = source.geometry.area
        if compute_length:
            result[length_col] = source.geometry.length
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                    "description": "Metric CRS for area/length computation.",
                },
                "area_col": {
                    "type": "string",
                    "default": "area_m2",
                    "description": "Column name for area.",
                },
                "length_col": {
                    "type": "string",
                    "default": "length_m",
                    "description": "Column name for length.",
                },
                "compute_area": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to compute area.",
                },
                "compute_length": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to compute length.",
                },
            },
        }

