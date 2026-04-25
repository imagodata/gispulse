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
# Intersects strategies
# ---------------------------------------------------------------------------


def _resolve_intersects_ref(
    params: dict, target_crs
):
    """Return the reference geometry for the intersects filter."""
    from shapely import wkt as shapely_wkt

    ref = params.get("ref_gdf")
    if ref is None:
        ref = params.get("mask_gdf")
    if ref is not None:
        if target_crs is not None and ref.crs is not None and ref.crs != target_crs:
            ref = ref.to_crs(target_crs)
        return ref.union_all()
    wkt = params.get("wkt")
    if wkt is not None:
        return shapely_wkt.loads(wkt)
    return None


class _IntersectsPythonStrategy(ExecutionStrategy):
    """GeoPandas fallback for intersects filter."""

    mode = StrategyMode.PYTHON

    def can_execute(self, ctx: ExecutionContext) -> bool:
        return True

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        ref_geom = _resolve_intersects_ref(ctx.params, gdf.crs)
        if ref_geom is None:
            raise ValueError(
                "IntersectsCapability requires 'wkt', 'ref_layer', or 'ref_gdf'."
            )
        hits = gdf.geometry.intersects(ref_geom)
        return gdf[hits].reset_index(drop=True)

    @property
    def priority(self) -> int:
        return 10


class _IntersectsDuckDBStrategy(ExecutionStrategy):
    """DuckDB strategy — ST_Intersects on WKB column."""

    mode = StrategyMode.DUCKDB

    def can_execute(self, ctx: ExecutionContext) -> bool:
        has_ref = (
            ctx.params.get("ref_gdf") is not None
            or ctx.params.get("mask_gdf") is not None
            or ctx.params.get("wkt") is not None
        )
        return (
            ctx.engine.backend_name == "duckdb"
            and ctx.feature_count > 10_000
            and has_ref
        )

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        ref_geom = _resolve_intersects_ref(ctx.params, gdf.crs)
        if ref_geom is None:
            raise ValueError(
                "IntersectsCapability requires 'wkt', 'ref_layer', or 'ref_gdf'."
            )
        ref_gdf = gpd.GeoDataFrame(geometry=[ref_geom], crs=gdf.crs)
        ctx.engine.register("_is_input", gdf)
        ctx.engine.register("_is_ref", ref_gdf)
        return ctx.engine.sql_to_gdf(
            "SELECT i.* FROM _is_input i, _is_ref r "
            "WHERE ST_Intersects(ST_GeomFromWKB(i.__wkb), ST_GeomFromWKB(r.__wkb))"
        ).reset_index(drop=True)

    @property
    def priority(self) -> int:
        return 80


class _IntersectsPostGISStrategy(ExecutionStrategy):
    """PostGIS strategy — index-accelerated ST_Intersects."""

    mode = StrategyMode.POSTGIS

    def can_execute(self, ctx: ExecutionContext) -> bool:
        has_ref = (
            ctx.params.get("ref_gdf") is not None
            or ctx.params.get("mask_gdf") is not None
            or ctx.params.get("wkt") is not None
        )
        return ctx.engine.backend_name == "postgis" and has_ref

    def execute(self, gdf: gpd.GeoDataFrame, ctx: ExecutionContext) -> gpd.GeoDataFrame:
        ref_geom = _resolve_intersects_ref(ctx.params, gdf.crs)
        if ref_geom is None:
            raise ValueError(
                "IntersectsCapability requires 'wkt', 'ref_layer', or 'ref_gdf'."
            )
        ref_gdf = gpd.GeoDataFrame(geometry=[ref_geom], crs=gdf.crs)
        ctx.engine.register("_is_input", gdf)
        ctx.engine.register("_is_ref", ref_gdf)
        return ctx.engine.sql_to_gdf(
            "SELECT i.* FROM _is_input i, _is_ref r "
            "WHERE ST_Intersects(i.geometry::geometry, r.geometry::geometry)"
        ).reset_index(drop=True)

    @property
    def priority(self) -> int:
        return 100


@register
class IntersectsCapability(Capability):
    """Filters features that intersect a reference geometry or layer."""

    name = "intersects"
    description = "Filters features that spatially intersect a reference geometry (WKT) or layer (ref_layer)."

    _strategies = [
        _IntersectsPostGISStrategy(),
        _IntersectsDuckDBStrategy(),
        _IntersectsPythonStrategy(),
    ]

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        wkt: str | None = None,
        ref_gdf: gpd.GeoDataFrame | None = None,
        mask_gdf: gpd.GeoDataFrame | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:      Input GeoDataFrame.
            wkt:      WKT string of the reference geometry.
            ref_gdf:  Reference layer (injected by engine from ref_layer).
            mask_gdf: Legacy parameter — alias for ref_gdf.
        """
        from shapely import wkt as shapely_wkt

        ref = ref_gdf if ref_gdf is not None else mask_gdf
        if ref is not None:
            if gdf.crs != ref.crs:
                ref = ref.to_crs(gdf.crs)
            ref_geom = ref.union_all()
        elif wkt is not None:
            ref_geom = shapely_wkt.loads(wkt)
        else:
            raise ValueError(
                "IntersectsCapability requires 'wkt', 'ref_layer', or 'ref_gdf'."
            )

        hits = gdf.geometry.intersects(ref_geom)
        return gdf[hits].reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "wkt": {
                    "type": "string",
                    "description": "WKT geometry string for the intersection test.",
                },
                "ref_layer": {
                    "type": "string",
                    "description": "Name of the reference layer to test intersection against.",
                },
            },
        }

