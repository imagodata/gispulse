"""Polygon topology repair capabilities.

Analogues of the *consolidate_network* family for polygon coverages.
A polygon coverage (cadastre, zoning, land cover) is supposed to tile the
plane without gaps or overlaps. These capabilities detect and fix the
common issues introduced by digitising errors:

- :class:`FixGapsCapability`          — fills tiny gaps (slivers between
  polygons) by allocating them to the neighbouring polygon with the
  longest shared border.
- :class:`FixOverlapsCapability`      — resolves overlaps (two polygons
  covering the same area) by giving the conflict zone to one polygon
  deterministically (smallest, largest, or first).
- :class:`RemoveSliversCapability`    — drops long thin polygons below an
  area threshold and (optionally) a minimum shape-index threshold.
- :class:`SnapBordersCapability`      — snaps coverage boundaries to a
  regular grid so shared borders become exactly coincident.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd

from capabilities.base import Capability
from capabilities.registry import register


def _work_in_metric(
    gdf: gpd.GeoDataFrame,
    crs_meters: str | None,
) -> tuple[gpd.GeoDataFrame, Any]:
    if crs_meters and gdf.crs is not None and str(gdf.crs) != crs_meters:
        return gdf.to_crs(crs_meters), gdf.crs
    return gdf.copy(), gdf.crs


def _restore_crs(gdf: gpd.GeoDataFrame, original_crs: Any) -> gpd.GeoDataFrame:
    if original_crs is not None and gdf.crs is not None and str(gdf.crs) != str(original_crs):
        return gdf.to_crs(original_crs)
    return gdf


# ---------------------------------------------------------------------------
# FixGapsCapability
# ---------------------------------------------------------------------------


@register
class FixGapsCapability(Capability):
    """Fills gaps below *max_gap_area* by allocating them to the best neighbour."""

    name = "polygon_fix_gaps"
    description = (
        "Detects gaps (holes in the polygon coverage) below a max area and "
        "merges each into the neighbour sharing the longest border."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        max_gap_area: float = 100.0,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           Input polygon coverage.
            max_gap_area:  Gaps with an area <= this are filled (in
                           *crs_meters* units²). Larger ones are left alone.
            crs_meters:    Metric CRS used for area comparisons.
        """
        from shapely.geometry import Polygon, MultiPolygon
        from shapely.ops import unary_union

        if max_gap_area <= 0:
            raise ValueError("max_gap_area must be > 0.")
        if gdf.empty:
            return gdf.copy()

        work, original_crs = _work_in_metric(gdf, crs_meters)

        # Compute the coverage envelope + its difference = gap polygons.
        union = unary_union(list(work.geometry))
        envelope = union.envelope
        holes_geom = envelope.difference(union)
        holes: list[Polygon] = []
        if holes_geom.is_empty:
            return _restore_crs(work, original_crs)
        if holes_geom.geom_type == "Polygon":
            holes = [holes_geom]
        elif holes_geom.geom_type == "MultiPolygon":
            holes = list(holes_geom.geoms)

        # Keep only holes that (a) are fully enclosed in the coverage (not
        # border holes of the envelope) and (b) below the threshold.
        union_bounds = list(union.bounds)
        inner_holes: list[Polygon] = []
        for h in holes:
            if h.area > max_gap_area:
                continue
            # Touching the envelope's edge means it's outside the coverage;
            # skip it to avoid dragging the coverage outward.
            hb = h.bounds
            on_edge = (
                abs(hb[0] - union_bounds[0]) < 1e-9
                or abs(hb[1] - union_bounds[1]) < 1e-9
                or abs(hb[2] - union_bounds[2]) < 1e-9
                or abs(hb[3] - union_bounds[3]) < 1e-9
            )
            if not on_edge:
                inner_holes.append(h)

        if not inner_holes:
            return _restore_crs(work, original_crs)

        out = work.copy().reset_index(drop=True)
        geoms = list(out.geometry)
        sindex = out.sindex

        for hole in inner_holes:
            best_idx: int | None = None
            best_shared = -1.0
            for cand_idx in sindex.intersection(hole.buffer(1e-6).bounds):
                other = geoms[cand_idx]
                if other is None or other.is_empty:
                    continue
                shared = hole.intersection(other.buffer(1e-6))
                length = shared.length if not shared.is_empty else 0.0
                if length > best_shared:
                    best_shared = length
                    best_idx = int(cand_idx)

            if best_idx is not None:
                geoms[best_idx] = unary_union([geoms[best_idx], hole])

        out = gpd.GeoDataFrame(
            out.drop(columns=["geometry"]).reset_index(drop=True),
            geometry=geoms,
            crs=out.crs,
        )
        return _restore_crs(out, original_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "max_gap_area": {
                    "type": "number",
                    "minimum": 0,
                    "default": 100.0,
                    "description": "Max gap area (crs_meters²) to fix automatically.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                },
            },
            "required": ["max_gap_area"],
        }


# ---------------------------------------------------------------------------
# FixOverlapsCapability
# ---------------------------------------------------------------------------


_OVERLAP_RULES = {"smallest", "largest", "first"}


@register
class FixOverlapsCapability(Capability):
    """Resolves polygon overlaps by giving the shared area to one polygon."""

    name = "polygon_fix_overlaps"
    description = (
        "Removes overlaps in a polygon coverage. "
        "Allocation rule: keep 'smallest', 'largest' or 'first' polygon."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        rule: str = "smallest",
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input polygon coverage.
            rule:       'smallest' — the smallest polygon keeps the overlap,
                        others are clipped; 'largest' — inverse; 'first' —
                        the polygon encountered first in the GeoDataFrame
                        order keeps the overlap.
            crs_meters: Metric CRS used for area comparison.
        """
        if rule not in _OVERLAP_RULES:
            raise ValueError(
                f"Invalid rule '{rule}'. Expected {sorted(_OVERLAP_RULES)}."
            )
        if gdf.empty or len(gdf) < 2:
            return gdf.copy()

        work, original_crs = _work_in_metric(gdf, crs_meters)
        out = work.copy()
        n = len(out)
        sindex = out.sindex

        # Pre-compute areas for smallest/largest rule; otherwise unused.
        areas = out.geometry.area.to_numpy() if rule != "first" else None

        # Iterate over pairs; keep track of already-modified geometries.
        geoms = list(out.geometry)
        for i in range(n):
            gi = geoms[i]
            if gi is None or gi.is_empty:
                continue
            for j in sindex.intersection(gi.bounds):
                if j <= i:
                    continue
                gj = geoms[j]
                if gj is None or gj.is_empty:
                    continue
                if not gi.intersects(gj):
                    continue
                inter = gi.intersection(gj)
                if inter.is_empty or inter.area < 1e-12:
                    continue

                if rule == "first":
                    # Keep gi intact; trim gj.
                    loser = j
                elif rule == "smallest":
                    loser = i if areas[i] > areas[j] else j
                else:  # largest
                    loser = i if areas[i] < areas[j] else j

                if loser == i:
                    geoms[i] = gi.difference(inter)
                    gi = geoms[i]
                    if gi is None or gi.is_empty:
                        break
                else:
                    geoms[j] = gj.difference(inter)

        out = gpd.GeoDataFrame(
            out.drop(columns=["geometry"]).reset_index(drop=True),
            geometry=geoms,
            crs=out.crs,
        )
        return _restore_crs(out, original_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "rule": {
                    "type": "string",
                    "enum": sorted(_OVERLAP_RULES),
                    "default": "smallest",
                    "description": "Which polygon keeps the overlap.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                },
            },
        }


# ---------------------------------------------------------------------------
# RemoveSliversCapability
# ---------------------------------------------------------------------------


@register
class RemoveSliversCapability(Capability):
    """Drops sliver polygons below an area and/or shape-index threshold."""

    name = "polygon_remove_slivers"
    description = (
        "Removes long/thin sliver polygons below *min_area* or above "
        "*max_shape_index* (= perimeter² / 4π area)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        min_area: float = 1.0,
        max_shape_index: float | None = None,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:              Input polygon coverage.
            min_area:         Minimum feature area to keep (crs_meters²).
            max_shape_index:  When set, drop features whose shape index
                              (perimeter² / 4π·area) exceeds this bound.
                              1.0 = circle, very large = long thin.
            crs_meters:       Metric CRS for area/perimeter.
        """
        import math

        if min_area < 0:
            raise ValueError("min_area must be >= 0.")
        if gdf.empty:
            return gdf.copy()

        work, original_crs = _work_in_metric(gdf, crs_meters)

        keep_mask = work.geometry.area >= min_area
        if max_shape_index is not None:
            perim = work.geometry.length
            area = work.geometry.area.replace(0, pd.NA)
            si = (perim ** 2) / (4 * math.pi * area)
            keep_mask &= si.fillna(float("inf")) <= max_shape_index

        filtered = work[keep_mask].reset_index(drop=True)
        return _restore_crs(filtered, original_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "min_area": {
                    "type": "number",
                    "minimum": 0,
                    "default": 1.0,
                    "description": "Minimum feature area in crs_meters² to keep.",
                },
                "max_shape_index": {
                    "type": ["number", "null"],
                    "description": "Optional shape index threshold (1=circle, ∞=line).",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                },
            },
        }


# ---------------------------------------------------------------------------
# SnapBordersCapability
# ---------------------------------------------------------------------------


@register
class SnapBordersCapability(Capability):
    """Snaps polygon borders to a common grid to align adjacent polygons."""

    name = "polygon_snap_borders"
    description = (
        "Snaps all vertex coordinates to a regular grid of size *grid_size*. "
        "Adjacent polygons' borders land on the same vertices — eliminating "
        "slivers at the source."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        grid_size: float = 0.01,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame.
            grid_size:  Grid cell size in units of *crs_meters*.
            crs_meters: Metric CRS for interpretation.
        """
        from shapely import set_precision

        if grid_size <= 0:
            raise ValueError("grid_size must be > 0.")
        if gdf.empty:
            return gdf.copy()

        work, original_crs = _work_in_metric(gdf, crs_meters)
        work["geometry"] = [set_precision(g, grid_size) for g in work.geometry]
        return _restore_crs(work, original_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "grid_size": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.01,
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                },
            },
            "required": ["grid_size"],
        }
