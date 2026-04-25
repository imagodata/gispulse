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
# Extract holes — interior rings of polygons as polygons
# ---------------------------------------------------------------------------


@register
class ExtractHolesCapability(Capability):
    """Extracts interior rings (holes) of polygons as polygon features.

    Each hole becomes a separate Polygon feature; the parent polygon's
    attributes are duplicated. Polygons without holes contribute zero rows.

    Example::

        {"parent_id_col": "parent_id"}
    """

    name = "extract_holes"
    description = "Extracts interior rings (holes) of polygons as polygon features."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        parent_id_col: str | None = None,
        hole_index_col: str = "hole_index",
        **_,
    ) -> gpd.GeoDataFrame:
        from shapely.geometry import Polygon as _Polygon

        if gdf.empty:
            return gdf.copy()

        rows: list[dict] = []
        geom_col = gdf.geometry.name
        for idx, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
            hole_idx = 0
            for poly in polys:
                if poly.geom_type != "Polygon":
                    continue
                for interior in poly.interiors:
                    base = row.to_dict()
                    base[geom_col] = _Polygon(interior)
                    base[hole_index_col] = hole_idx
                    if parent_id_col:
                        base[parent_id_col] = idx
                    rows.append(base)
                    hole_idx += 1

        if not rows:
            return gpd.GeoDataFrame(columns=list(gdf.columns), geometry=geom_col, crs=gdf.crs)
        return gpd.GeoDataFrame(rows, geometry=geom_col, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "parent_id_col": {
                    "type": ["string", "null"],
                    "description": "Optional column receiving the parent polygon's index.",
                },
                "hole_index_col": {
                    "type": "string",
                    "default": "hole_index",
                    "description": "Column name for the per-parent hole index.",
                },
            },
        }

