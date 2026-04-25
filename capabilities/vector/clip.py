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
# Clip strategies
# ---------------------------------------------------------------------------


def _resolve_clip_mask(params: dict, target_crs) -> gpd.GeoDataFrame | None:
    """Return the mask GeoDataFrame for clip/intersects, reprojected to target_crs."""
    mask = params.get("ref_gdf")
    if mask is None:
        mask = params.get("mask_gdf")
    if mask is None:
        return None
    if target_crs is not None and mask.crs is not None and mask.crs != target_crs:
        mask = mask.to_crs(target_crs)
    return mask


class _ClipPythonStrategy(ExecutionStrategy):
    """GeoPandas fallback — always available."""

    mode = StrategyMode.PYTHON

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return True

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        mask = _resolve_clip_mask(ctx.params, gdf.crs)
        if mask is None:
            raise ValueError(
                "ClipCapability requires a reference layer. "
                "Use 'ref_layer' in rule config or pass 'ref_gdf' directly."
            )
        return gpd.clip(gdf, mask).reset_index(drop=True)

    @property
    def priority(self) -> int:
        return 10


class _ClipDuckDBStrategy(ExecutionStrategy):
    """DuckDB spatial strategy — server-side ST_Intersection.

    Engaged for large datasets to offload the per-feature clip work to the
    vectorized DuckDB spatial executor. Falls back to Python when no mask
    is provided or when DuckDB is not the active engine.
    """

    mode = StrategyMode.DUCKDB

    def can_execute(self, ctx: ExecutionContext) -> bool:
        mask = ctx.params.get("ref_gdf")
        if mask is None:
            mask = ctx.params.get("mask_gdf")
        return (
            ctx.engine.backend_name == "duckdb"
            and ctx.feature_count > 10_000
            and mask is not None
        )

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        mask = _resolve_clip_mask(ctx.params, gdf.crs)
        if mask is None:
            raise ValueError("ClipCapability requires a reference layer.")

        # Union the mask in Python so the SQL stays simple.
        mask_union = mask.geometry.union_all()
        mask_gdf = gpd.GeoDataFrame(geometry=[mask_union], crs=gdf.crs)

        ctx.engine.register("_clip_input", gdf)
        ctx.engine.register("_clip_mask", mask_gdf)
        result = ctx.engine.sql_to_gdf(
            "SELECT i.* REPLACE ("
            "  ST_Intersection(ST_GeomFromWKB(i.__wkb), ST_GeomFromWKB(m.__wkb)) "
            "  AS __wkb"
            ") "
            "FROM _clip_input i, _clip_mask m "
            "WHERE ST_Intersects(ST_GeomFromWKB(i.__wkb), ST_GeomFromWKB(m.__wkb))"
        )
        return result.reset_index(drop=True)

    @property
    def priority(self) -> int:
        return 80


class _ClipPostGISStrategy(ExecutionStrategy):
    """PostGIS strategy — server-side ST_Intersection with index usage."""

    mode = StrategyMode.POSTGIS

    def can_execute(self, ctx: ExecutionContext) -> bool:
        mask = ctx.params.get("ref_gdf")
        if mask is None:
            mask = ctx.params.get("mask_gdf")
        return ctx.engine.backend_name == "postgis" and mask is not None

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        mask = _resolve_clip_mask(ctx.params, gdf.crs)
        if mask is None:
            raise ValueError("ClipCapability requires a reference layer.")

        mask_union = mask.geometry.union_all()
        mask_gdf = gpd.GeoDataFrame(geometry=[mask_union], crs=gdf.crs)

        ctx.engine.register("_clip_input", gdf)
        ctx.engine.register("_clip_mask", mask_gdf)
        result = ctx.engine.sql_to_gdf(
            "SELECT i.*, ST_Intersection(i.geometry::geometry, m.geometry::geometry) "
            "AS geometry_clip "
            "FROM _clip_input i, _clip_mask m "
            "WHERE ST_Intersects(i.geometry::geometry, m.geometry::geometry)"
        )
        if "geometry_clip" in result.columns:
            result = result.set_geometry("geometry_clip").drop(
                columns=["geometry"], errors="ignore"
            ).rename_geometry("geometry")
        return result.reset_index(drop=True)

    @property
    def priority(self) -> int:
        return 100


@register
class ClipCapability(Capability):
    """Clips a layer to the extent of a reference layer."""

    name = "clip"
    description = "Clips a layer to the boundaries of a reference layer (via ref_layer in rules)."

    _strategies = [
        _ClipPostGISStrategy(),
        _ClipDuckDBStrategy(),
        _ClipPythonStrategy(),
    ]

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        mask_gdf: gpd.GeoDataFrame | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:      Input GeoDataFrame to clip.
            ref_gdf:  Reference layer (injected by engine from ref_layer).
            mask_gdf: Legacy parameter — alias for ref_gdf.
        """
        mask = ref_gdf if ref_gdf is not None else mask_gdf
        if mask is None:
            raise ValueError(
                "ClipCapability requires a reference layer. "
                "Use 'ref_layer' in rule config or pass 'ref_gdf' directly."
            )
        if gdf.crs != mask.crs:
            mask = mask.to_crs(gdf.crs)
        return gpd.clip(gdf, mask).reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Name of the reference layer to clip against.",
                },
            },
            # ``ref_layer`` is pipeline plumbing (stripped by
            # rules.validation._PLUMBING_KEYS before validation) so it cannot
            # appear in ``required`` — the runtime raises a clear ValueError
            # when ``ref_gdf`` is None, which preserves the contract.
        }


