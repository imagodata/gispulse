from __future__ import annotations


import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register


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


# ---------------------------------------------------------------------------
# ELT Lot 3 (#246) — DuckDB / PostGIS SQL push-down strategy
# ---------------------------------------------------------------------------

from gispulse.capabilities import _geometry_sql as _gsql  # noqa: E402
from gispulse.capabilities.sql_pushdown import attach_sql_pushdown  # noqa: E402

attach_sql_pushdown(UnionCapability, _gsql.build_union)


