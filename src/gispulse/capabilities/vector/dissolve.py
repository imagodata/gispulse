from __future__ import annotations


import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register



@register
class DissolveCapability(Capability):
    """Dissolves features, optionally grouped by an attribute."""

    name = "dissolve"
    description = "Dissolves features, optionally grouped by an attribute column."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        by: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf: Input GeoDataFrame.
            by:  Column name to group by before dissolving.
                 If None, all features are dissolved into one.

        Returns:
            Dissolved GeoDataFrame.
        """
        dissolved = gdf.dissolve(by=by)
        return dissolved.reset_index()

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "by": {
                    "type": ["string", "null"],
                    "description": "Column to group by. Null dissolves everything.",
                }
            },
        }


# ---------------------------------------------------------------------------
# ELT Lot 3 (#246) — DuckDB / PostGIS SQL push-down strategy
# ---------------------------------------------------------------------------

from gispulse.capabilities import _geometry_sql as _gsql  # noqa: E402
from gispulse.capabilities.sql_pushdown import attach_sql_pushdown  # noqa: E402

attach_sql_pushdown(
    DissolveCapability,
    _gsql.build_dissolve,
    # by=None falls back to Python because gpd.dissolve().reset_index()
    # then appends a spurious 'index' column from the default RangeIndex —
    # not a meaningful difference, but reproducing it in SQL has no value.
    gate=lambda p: bool(p.get("by")),
)

