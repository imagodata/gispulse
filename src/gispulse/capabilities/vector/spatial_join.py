from __future__ import annotations


import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.capabilities.vector.calculate import _validate_query_expression



@register
class SpatialJoinCapability(Capability):
    """Joins attributes from a reference layer based on spatial relationship."""

    name = "spatial_join"
    description = "Joins attributes from a reference layer to features based on spatial relationship (ref_layer)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        how: str = "inner",
        predicate: str = "intersects",
        columns: list[str] | None = None,
        ref_filter: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame (left side of the join).
            ref_gdf:    Reference layer (injected by engine from ref_layer).
            how:        Join type: 'inner', 'left', or 'right'.
            predicate:  Spatial predicate: 'intersects', 'within', 'contains'.
            columns:    Columns to keep from the reference layer. None = all.
            ref_filter: Pandas query applied to *ref_gdf* before the join.

        Returns:
            GeoDataFrame with attributes joined from the reference layer.
        """
        if ref_gdf is None:
            raise ValueError(
                "SpatialJoinCapability requires a reference layer. "
                "Use 'ref_layer' in rule config."
            )

        if ref_filter:
            _validate_query_expression(ref_filter)
            ref_gdf = ref_gdf.query(ref_filter).reset_index(drop=True)

        if gdf.crs != ref_gdf.crs:
            ref_gdf = ref_gdf.to_crs(gdf.crs)

        # Select only requested columns from reference (+ geometry for join)
        if columns is not None:
            keep_cols = [c for c in columns if c in ref_gdf.columns]
            if ref_gdf.geometry.name not in keep_cols:
                keep_cols.append(ref_gdf.geometry.name)
            ref_gdf = ref_gdf[keep_cols]

        result = gpd.sjoin(gdf, ref_gdf, how=how, predicate=predicate)

        # Clean up join artifacts
        if "index_right" in result.columns:
            result = result.drop(columns=["index_right"])

        return result.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Name of the reference layer to join from.",
                },
                "how": {
                    "type": "string",
                    "default": "inner",
                    "description": "Join type: 'inner', 'left', or 'right'.",
                    "enum": ["inner", "left", "right"],
                },
                "predicate": {
                    "type": "string",
                    "default": "intersects",
                    "description": "Spatial predicate for the join.",
                    "enum": ["intersects", "within", "contains"],
                },
                "columns": {
                    "type": ["array", "null"],
                    "description": "Columns to keep from reference layer. Null = all.",
                    "items": {"type": "string"},
                },
                "ref_filter": {
                    "type": ["string", "null"],
                    "description": "Pandas query applied to ref_layer before the join (e.g. \"importance in ['5','6']\").",
                },
            },
            # ``ref_layer`` is pipeline plumbing (stripped by
            # rules.validation._PLUMBING_KEYS before validation) so it cannot
            # appear in ``required`` — the runtime raises a clear ValueError
            # when ``ref_gdf`` is None, which preserves the contract.
        }


# ---------------------------------------------------------------------------
# ELT Lot 3 (#246) — DuckDB / PostGIS SQL push-down strategy
# ---------------------------------------------------------------------------

from gispulse.capabilities import _geometry_sql as _gsql  # noqa: E402
from gispulse.capabilities.sql_pushdown import attach_sql_pushdown  # noqa: E402

attach_sql_pushdown(
    SpatialJoinCapability,
    _gsql.build_spatial_join,
    gate=lambda p: p.get("ref_gdf") is not None,
    extra_inputs={"ref": "ref_gdf"},
)

