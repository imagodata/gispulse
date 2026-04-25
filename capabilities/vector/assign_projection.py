from __future__ import annotations

import ast as _ast
import re as _re

import geopandas as gpd
import numpy as np
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register
from capabilities.strategy import ExecutionContext, ExecutionStrategy, StrategyMode



# ---------------------------------------------------------------------------
# Assign projection — declare the CRS without reprojecting coordinates
# ---------------------------------------------------------------------------


@register
class AssignProjectionCapability(Capability):
    """Sets the layer CRS *without* reprojecting coordinates.

    Distinct from ``reproject``: this only updates the metadata, leaving the
    XY values untouched. Use to fix a layer whose CRS is wrong/missing.
    """

    name = "assign_projection"
    description = "Sets the layer CRS without transforming coordinates."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        crs: str = "",
        allow_override: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        if not crs:
            raise ValueError("assign_projection requires 'crs'.")
        result = gdf.copy()
        result.set_crs(crs, allow_override=allow_override, inplace=True)
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "crs": {
                    "type": "string",
                    "description": "Target CRS, e.g. 'EPSG:2154'.",
                },
                "allow_override": {
                    "type": "boolean",
                    "default": True,
                    "description": "Replace any existing CRS metadata.",
                },
            },
            "required": ["crs"],
        }


