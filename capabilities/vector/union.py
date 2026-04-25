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
class UnionCapability(Capability):
    """Dissolves all geometries in the layer into a single geometry."""

    name = "union"
    description = "Dissolves all features into a single unioned geometry."

    def execute(self, gdf: gpd.GeoDataFrame, **_) -> gpd.GeoDataFrame:
        """
        Args:
            gdf: Input GeoDataFrame.

        Returns:
            Single-row GeoDataFrame with the union of all geometries.
        """
        unioned = gdf.geometry.union_all()
        return gpd.GeoDataFrame(geometry=[unioned], crs=gdf.crs)

    def get_schema(self) -> dict:
        return {"type": "object", "properties": {}}


