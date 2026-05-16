"""FilterMate capabilities — advanced spatial filtering and attribute preparation."""

from __future__ import annotations

import geopandas as gpd

from gispulse.plugins.api import Capability
from gispulse.plugins.api import register_capability as register


@register
class SpatialFilterCapability(Capability):
    """Filter features by spatial relationship with a mask geometry."""

    name = "spatial_filter"
    description = "Filter features by spatial relationship (intersects, within, contains) with a mask."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "predicate": {
                    "type": "string",
                    "enum": ["intersects", "within", "contains", "crosses", "touches"],
                    "default": "intersects",
                    "description": "Spatial predicate for the filter",
                },
                "mask_wkt": {
                    "type": "string",
                    "description": "WKT geometry to use as filter mask",
                },
            },
            "required": ["mask_wkt"],
            "additionalProperties": False,
        }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        *,
        predicate: str = "intersects",
        mask_wkt: str | None = None,
        mask_gdf: gpd.GeoDataFrame | None = None,
        **_kw,
    ) -> gpd.GeoDataFrame:
        if mask_gdf is not None:
            mask = mask_gdf
        elif mask_wkt:
            from shapely import wkt

            mask_geom = wkt.loads(mask_wkt)
            mask = gpd.GeoDataFrame(geometry=[mask_geom], crs=gdf.crs)
        else:
            return gdf.copy()

        return gpd.sjoin(gdf, mask, how="inner", predicate=predicate).drop(
            columns=["index_right"], errors="ignore"
        )


@register
class AttributePrepCapability(Capability):
    """Prepare and clean attribute columns."""

    name = "attribute_prep"
    description = "Rename, cast, drop, or reorder attribute columns."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "rename": {
                    "type": "object",
                    "description": "Mapping of old_name -> new_name",
                    "additionalProperties": {"type": "string"},
                },
                "drop": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Columns to drop",
                },
                "keep_only": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "If set, only keep these columns (plus geometry)",
                },
            },
            "additionalProperties": False,
        }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        *,
        rename: dict[str, str] | None = None,
        drop: list[str] | None = None,
        keep_only: list[str] | None = None,
        **_kw,
    ) -> gpd.GeoDataFrame:
        result = gdf.copy()
        if rename:
            result = result.rename(columns=rename)
        if drop:
            result = result.drop(columns=[c for c in drop if c in result.columns])
        if keep_only:
            cols = [c for c in keep_only if c in result.columns]
            if result.geometry.name not in cols:
                cols.append(result.geometry.name)
            result = result[cols]
        return result
