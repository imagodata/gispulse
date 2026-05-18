from __future__ import annotations


import geopandas as gpd
import numpy as np

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register




@register
class LineLocatePointCapability(Capability):
    """Linear referencing — projects each point onto the nearest line."""

    name = "line_locate_point"
    description = (
        "Projects each input point onto the nearest reference line and adds "
        "a measure (distance along the line) + ref_index column."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        normalized: bool = False,
        measure_col: str = "measure",
        ref_index_col: str = "ref_index",
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:          Input points (or centroids of other geometries).
            ref_gdf:      Reference linear layer (injected via ref_layer).
            normalized:   When True, the measure is in [0, 1] relative to
                          the line length; otherwise it is in crs_meters.
            measure_col:  Name of the output measure column.
            ref_index_col: Name of the output column holding the index of
                          the matched reference feature (0-based).
            crs_meters:   Metric CRS for distance computation.
        """

        if ref_gdf is None or ref_gdf.empty:
            raise ValueError("line_locate_point requires a reference layer.")
        if gdf.empty:
            out = gdf.copy()
            out[measure_col] = np.array([], dtype=float)
            out[ref_index_col] = np.array([], dtype=np.int64)
            return out

        original_crs = gdf.crs
        left = gdf.to_crs(crs_meters) if original_crs is not None else gdf.copy()
        right = (
            ref_gdf.to_crs(crs_meters)
            if ref_gdf.crs is not None and str(ref_gdf.crs) != crs_meters
            else ref_gdf.copy()
        )

        sindex = right.sindex
        measures = np.zeros(len(left), dtype=float)
        ref_indices = np.zeros(len(left), dtype=np.int64)

        for row_i, (pt_idx, pt_row) in enumerate(left.iterrows()):
            pt = pt_row.geometry
            if pt is None or pt.is_empty:
                measures[row_i] = np.nan
                ref_indices[row_i] = -1
                continue
            if pt.geom_type != "Point":
                pt = pt.centroid

            # Find candidate lines within a reasonable bbox → pick closest.
            best_dist = float("inf")
            best_idx = -1
            for cand in sindex.intersection(pt.buffer(1.0).bounds):
                other = right.geometry.iloc[cand]
                if other is None or other.is_empty:
                    continue
                d = pt.distance(other)
                if d < best_dist:
                    best_dist = d
                    best_idx = int(cand)

            if best_idx == -1:
                # Full scan fallback for sparse networks
                for cand in range(len(right)):
                    other = right.geometry.iloc[cand]
                    if other is None or other.is_empty:
                        continue
                    d = pt.distance(other)
                    if d < best_dist:
                        best_dist = d
                        best_idx = cand

            if best_idx == -1:
                measures[row_i] = np.nan
                ref_indices[row_i] = -1
                continue

            line = right.geometry.iloc[best_idx]
            measures[row_i] = line.project(pt, normalized=normalized)
            ref_indices[row_i] = best_idx

        out = left.copy()
        out[measure_col] = measures
        out[ref_index_col] = ref_indices
        if original_crs is not None:
            out = out.to_crs(original_crs)
        return out

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Reference line layer.",
                },
                "normalized": {
                    "type": "boolean",
                    "default": False,
                    "description": "If True, measure is in [0, 1].",
                },
                "measure_col": {"type": "string", "default": "measure"},
                "ref_index_col": {"type": "string", "default": "ref_index"},
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
            # ``ref_layer`` is pipeline plumbing (stripped by
            # rules.validation._PLUMBING_KEYS before validation) so it cannot
            # appear in ``required`` — the runtime raises a clear ValueError
            # when ``ref_gdf`` is None, which preserves the contract.
        }


@register
class LineSubstringCapability(Capability):
    """Extracts a sub-segment of each line between two measures."""

    name = "line_substring"
    description = (
        "Returns the substring of each line between start_measure and "
        "end_measure (linear referencing)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        start_measure: float = 0.0,
        end_measure: float = 1.0,
        normalized: bool = True,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           Input linestring GeoDataFrame.
            start_measure: Starting measure.
            end_measure:   Ending measure.
            normalized:    If True, measures are in [0, 1]; otherwise in
                           the native CRS units.
        """
        from shapely.ops import substring

        if end_measure <= start_measure:
            raise ValueError("end_measure must be > start_measure.")
        if gdf.empty:
            return gdf.copy()

        result = gdf.copy()
        result["geometry"] = [
            substring(g, start_measure, end_measure, normalized=normalized)
            if g is not None and not g.is_empty and g.geom_type == "LineString"
            else g
            for g in gdf.geometry
        ]
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "start_measure": {"type": "number", "default": 0.0},
                "end_measure": {"type": "number", "default": 1.0},
                "normalized": {"type": "boolean", "default": True},
            },
            "required": ["start_measure", "end_measure"],
        }
