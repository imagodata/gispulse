from __future__ import annotations

import ast as _ast
import re as _re

import geopandas as gpd
import numpy as np
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register
from capabilities.strategy import ExecutionContext, ExecutionStrategy, StrategyMode




# Supported spatial predicates for aggregation
_SPATIAL_PREDICATES = {"intersects", "within", "contains", "crosses", "overlaps", "touches"}

# Supported aggregate functions
_AGG_FUNCTIONS: dict[str, str] = {
    "count": "count",
    "sum": "sum",
    "mean": "mean",
    "min": "min",
    "max": "max",
    "median": "median",
    "std": "std",
}


@register
class SpatialAggregateCapability(Capability):
    """Aggregates values from a reference layer based on geometric predicates.

    For each feature in the input layer, finds matching features in the
    reference layer using a spatial predicate (intersects, within, etc.),
    then computes aggregate statistics on specified columns.

    Examples::

        # Count buildings per parcel
        {"ref_layer": "buildings", "predicate": "contains",
         "agg": {"building_count": ("id", "count")}}

        # Sum population within buffer zones
        {"ref_layer": "population", "predicate": "intersects",
         "agg": {"total_pop": ("population", "sum"),
                 "avg_pop": ("population", "mean")}}
    """

    name = "spatial_aggregate"
    description = (
        "Aggregates values from a reference layer using geometric predicates "
        "(count, sum, mean, min, max per feature)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        predicate: str = "intersects",
        agg: dict[str, tuple[str, str] | list] | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:       Input GeoDataFrame (each feature gets aggregate columns).
            ref_gdf:   Reference layer to aggregate from (injected via ref_layer).
            predicate: Spatial predicate: 'intersects', 'within', 'contains',
                       'crosses', 'overlaps', 'touches'.
            agg:       Mapping of {result_col: (source_col, agg_func)}.
                       agg_func is one of: count, sum, mean, min, max, median, std.
                       For count, source_col can be any column.

        Returns:
            GeoDataFrame with new aggregate columns added.
        """
        if ref_gdf is None:
            raise ValueError(
                "SpatialAggregateCapability requires a reference layer. "
                "Use 'ref_layer' in rule config."
            )
        if not agg:
            raise ValueError(
                "SpatialAggregateCapability requires 'agg' parameter. "
                "Example: {\"count_b\": [\"id\", \"count\"]}"
            )
        if predicate not in _SPATIAL_PREDICATES:
            raise ValueError(
                f"Unknown spatial predicate '{predicate}'. "
                f"Supported: {sorted(_SPATIAL_PREDICATES)}"
            )

        if gdf.crs != ref_gdf.crs:
            ref_gdf = ref_gdf.to_crs(gdf.crs)

        # Normalize agg values: accept both tuples and lists from JSON
        normalized_agg: dict[str, tuple[str, str]] = {}
        for result_col, spec in agg.items():
            if isinstance(spec, (list, tuple)) and len(spec) == 2:
                src_col, func = spec
            else:
                raise ValueError(
                    f"agg['{result_col}'] must be [source_col, agg_func], "
                    f"got {spec!r}"
                )
            if func not in _AGG_FUNCTIONS:
                raise ValueError(
                    f"Unknown agg function '{func}'. "
                    f"Supported: {sorted(_AGG_FUNCTIONS)}"
                )
            normalized_agg[result_col] = (src_col, func)

        # Spatial join to find matching pairs
        joined = gpd.sjoin(gdf, ref_gdf, how="left", predicate=predicate)

        # The join duplicates left rows for each matching right feature.
        # Group by original index to aggregate.
        result = gdf.copy()
        for result_col, (src_col, func) in normalized_agg.items():
            # The right-side column may get a suffix from sjoin
            actual_col = src_col
            if src_col not in joined.columns and f"{src_col}_right" in joined.columns:
                actual_col = f"{src_col}_right"

            if actual_col not in joined.columns:
                raise ValueError(
                    f"Column '{src_col}' not found in reference layer. "
                    f"Available: {[c for c in ref_gdf.columns if c != ref_gdf.geometry.name]}"
                )

            grouped = joined.groupby(joined.index)[actual_col]

            if func == "count":
                # count non-NaN (NaN = no match from left join)
                result[result_col] = grouped.count().reindex(gdf.index, fill_value=0)
            elif func == "sum":
                result[result_col] = grouped.sum().reindex(gdf.index, fill_value=0)
            elif func == "mean":
                result[result_col] = grouped.mean().reindex(gdf.index)
            elif func == "min":
                result[result_col] = grouped.min().reindex(gdf.index)
            elif func == "max":
                result[result_col] = grouped.max().reindex(gdf.index)
            elif func == "median":
                result[result_col] = grouped.median().reindex(gdf.index)
            elif func == "std":
                result[result_col] = grouped.std().reindex(gdf.index)

        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Name of the reference layer to aggregate from.",
                },
                "predicate": {
                    "type": "string",
                    "default": "intersects",
                    "description": "Spatial predicate for matching features.",
                    "enum": sorted(_SPATIAL_PREDICATES),
                },
                "agg": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "description": (
                        "Mapping of {result_col: [source_col, agg_func]}. "
                        "agg_func: count, sum, mean, min, max, median, std."
                    ),
                },
            },
            # ``ref_layer`` is pipeline plumbing (stripped by
            # rules.validation._PLUMBING_KEYS before validation) so it cannot
            # appear in ``required`` — the runtime raises a clear ValueError
            # when ``ref_gdf`` is None, which preserves the contract.
            "required": ["agg"],
        }

