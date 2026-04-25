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
class LineMergeCapability(Capability):
    """Merges MultiLineString features into single LineStrings when endpoints touch."""

    name = "line_merge"
    description = (
        "Merges touching line segments into single LineStrings. "
        "Useful to reconnect a fragmented road / river network."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        directed: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:      Input GeoDataFrame (lines / multilines).
            directed: When True, respect line direction. When False (default),
                      merge regardless of direction.
        """
        from shapely import line_merge

        if gdf.empty:
            return gdf.copy()

        result = gdf.copy()
        result["geometry"] = [line_merge(g, directed=directed) for g in gdf.geometry]
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "directed": {
                    "type": "boolean",
                    "default": False,
                    "description": "Respect line direction when merging.",
                },
            },
        }


