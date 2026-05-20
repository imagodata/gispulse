from __future__ import annotations


import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register


@register
class ReprojectCapability(Capability):
    """Reprojects a layer to a target CRS."""

    name = "reproject"
    description = "Reprojects a layer to a target CRS (EPSG string)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        target_crs: str = "EPSG:4326",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame.
            target_crs: Destination CRS as an EPSG string, e.g. 'EPSG:2154'.
        """
        return gdf.to_crs(target_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_crs": {
                    "type": "string",
                    "default": "EPSG:4326",
                    "description": "Target CRS, e.g. 'EPSG:2154'.",
                }
            },
            "required": ["target_crs"],
        }


# ---------------------------------------------------------------------------
# ELT Lot 3 (#246) — DuckDB / PostGIS SQL push-down strategy
# ---------------------------------------------------------------------------

from gispulse.capabilities import _geometry_sql as _gsql  # noqa: E402
from gispulse.capabilities.sql_pushdown import attach_sql_pushdown  # noqa: E402

attach_sql_pushdown(
    ReprojectCapability, _gsql.build_reproject, post=_gsql.reproject_post
)


