from __future__ import annotations

import ast as _ast
import re as _re

import geopandas as gpd
import numpy as np
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register
from capabilities.strategy import ExecutionContext, ExecutionStrategy, StrategyMode
from capabilities.vector.buffer import _BUFFER_JOIN_STYLES


@register
class OffsetCurveCapability(Capability):
    """Offset curve — parallel line at given distance (signed)."""

    name = "offset_curve"
    description = (
        "Creates a parallel line at a given signed distance. "
        "Positive = left, negative = right. Works on LineStrings only."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        distance: float = 1.0,
        quad_segs: int = 8,
        join_style: str = "round",
        mitre_limit: float = 5.0,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:         Input GeoDataFrame (must contain LineStrings).
            distance:    Signed distance. Positive offsets to the left,
                         negative to the right of the line direction.
            quad_segs:   Segments per quadrant for round joins.
            join_style:  'round' | 'mitre' | 'bevel'.
            mitre_limit: Ratio limit for mitre joins.
            crs_meters:  Metric CRS used to interpret distance.
        """
        from shapely import offset_curve

        if gdf.empty:
            return gdf.copy()
        if join_style not in _BUFFER_JOIN_STYLES:
            raise ValueError(
                f"Invalid join_style '{join_style}'. "
                f"Expected {list(_BUFFER_JOIN_STYLES)}."
            )

        original_crs = gdf.crs
        work = gdf.to_crs(crs_meters) if original_crs is not None else gdf.copy()

        work["geometry"] = [
            offset_curve(
                g,
                distance,
                quad_segs=int(quad_segs),
                join_style=_BUFFER_JOIN_STYLES[join_style],
                mitre_limit=float(mitre_limit),
            )
            for g in work.geometry
        ]

        return work.to_crs(original_crs) if original_crs is not None else work

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "distance": {
                    "type": "number",
                    "description": "Signed offset distance (positive=left).",
                },
                "quad_segs": {
                    "type": "integer",
                    "default": 8,
                    "minimum": 1,
                    "description": "Segments per quadrant for round joins.",
                },
                "join_style": {
                    "type": "string",
                    "default": "round",
                    "enum": ["round", "mitre", "bevel"],
                    "description": "Segment join style.",
                },
                "mitre_limit": {
                    "type": "number",
                    "default": 5.0,
                    "description": "Mitre ratio limit.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                    "description": "Metric CRS.",
                },
            },
            "required": ["distance"],
        }


