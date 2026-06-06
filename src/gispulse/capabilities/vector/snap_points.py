"""Snap points to a line network with stable edge ids (capability B).

Where ``line_locate_point`` returns only a measure plus a *positional*
``ref_index``, this capability returns everything needed to attach an event
to a routable edge:

* ``edge_id``         — the matched line's id (from ``ref_id_col``), stable
  across runs (not a row position);
* ``measure``         — distance along the matched line, in meters;
* ``offset_distance`` — perpendicular distance from the point to the line;
* ``snapped``         — False when no line lies within ``max_distance_m``;
* ``geometry``        — replaced by the **projected** point on the line
  (the original point is kept for unsnapped rows).

Candidate lines are found through a :class:`~gispulse.core.spatial_index.SpatialIndex`
(no O(n·m) scan). Processing happens in a metric CRS so ``measure`` /
``offset_distance`` / ``max_distance_m`` are in meters; the result is
reprojected to the input CRS.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.core.spatial_index import SpatialIndex


@register
class SnapPointsToLinesCapability(Capability):
    """Projects points onto a line network, tagging each with its edge id."""

    name = "snap_points_to_lines"
    description = (
        "Snaps each point to the nearest line within max_distance_m, adding "
        "edge_id (from ref_id_col), measure, offset_distance and a snapped "
        "flag; geometry becomes the projected point."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        ref_id_col: str | None = None,
        max_distance_m: float | None = None,
        edge_id_col: str = "edge_id",
        measure_col: str = "measure",
        offset_col: str = "offset_distance",
        snapped_col: str = "snapped",
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:            Input points (non-points use their centroid).
            ref_gdf:        Reference line layer (injected via ``ref_layer``).
            ref_id_col:     Column on ``ref_gdf`` providing ``edge_id``.
                            Defaults to the line's row position.
            max_distance_m: Max snap distance in meters. Points with no line
                            within it are left unsnapped (``snapped=False``,
                            original geometry kept). ``None`` always snaps to
                            the nearest line.
            edge_id_col:    Output column for the matched edge id.
            measure_col:    Output column for the along-line measure (m).
            offset_col:     Output column for the perpendicular offset (m).
            snapped_col:    Output boolean column.
            crs_meters:     Metric CRS used for all distances.

        Returns:
            Copy of ``gdf`` with the four columns added and ``geometry``
            replaced by the projected point (snapped rows), in the input CRS.
        """
        if ref_gdf is None or ref_gdf.empty:
            raise ValueError("snap_points_to_lines requires a reference line layer.")

        original_crs = gdf.crs
        left = gdf.to_crs(crs_meters) if original_crs is not None else gdf.copy()
        right = (
            ref_gdf.to_crs(crs_meters)
            if ref_gdf.crs is not None and str(ref_gdf.crs) != crs_meters
            else ref_gdf.copy()
        )
        right = right.reset_index(drop=True)

        has_id = ref_id_col is not None and ref_id_col in right.columns
        line_geoms = list(right.geometry)
        index = SpatialIndex(line_geoms)

        n = len(left)
        edge_ids: list[object] = [None] * n
        measures = np.full(n, np.nan, dtype=float)
        offsets = np.full(n, np.nan, dtype=float)
        snapped = np.zeros(n, dtype=bool)
        new_geoms = list(left.geometry)

        for row_i, geom in enumerate(left.geometry):
            if geom is None or geom.is_empty:
                continue
            pt = geom if geom.geom_type == "Point" else geom.centroid

            best = self._best_line(index, line_geoms, pt, max_distance_m)
            if best is None:
                # Record how far the truly nearest line is (informative).
                near = index.nearest(pt)
                if near:
                    offsets[row_i] = float(line_geoms[near[0]].distance(pt))
                continue

            pos, dist = best
            line = line_geoms[pos]
            measure = line.project(pt)
            measures[row_i] = float(measure)
            offsets[row_i] = float(dist)
            snapped[row_i] = True
            edge_ids[row_i] = right.iloc[pos][ref_id_col] if has_id else pos
            new_geoms[row_i] = line.interpolate(measure)

        out = left.copy()
        out[edge_id_col] = edge_ids
        out[measure_col] = measures
        out[offset_col] = offsets
        out[snapped_col] = snapped
        out = out.set_geometry(
            gpd.GeoSeries(new_geoms, index=out.index, crs=crs_meters)
        )
        if original_crs is not None:
            out = out.to_crs(original_crs)
        return out.reset_index(drop=True)

    @staticmethod
    def _best_line(
        index: SpatialIndex,
        line_geoms: list,
        pt,
        max_distance_m: float | None,
    ) -> "tuple[int, float] | None":
        """Return (line position, distance) of the best match, or None."""
        if max_distance_m is None:
            near = index.nearest(pt)
            if not near:
                return None
            pos = near[0]
            return pos, float(line_geoms[pos].distance(pt))

        candidates = index.query_radius(pt, max_distance_m)
        best_pos, best_dist = -1, float("inf")
        for pos in candidates:
            d = line_geoms[pos].distance(pt)
            if d < best_dist:
                best_pos, best_dist = pos, d
        if best_pos < 0 or best_dist > max_distance_m:
            return None
        return best_pos, best_dist

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Reference line layer.",
                },
                "ref_id_col": {
                    "type": ["string", "null"],
                    "description": "Column on the lines providing edge_id.",
                },
                "max_distance_m": {
                    "type": ["number", "null"],
                    "description": "Max snap distance (m). None = nearest line always.",
                },
                "edge_id_col": {"type": "string", "default": "edge_id"},
                "measure_col": {"type": "string", "default": "measure"},
                "offset_col": {"type": "string", "default": "offset_distance"},
                "snapped_col": {"type": "string", "default": "snapped"},
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
            # ``ref_layer`` is pipeline plumbing (stripped before validation)
            # so it cannot be in ``required``; the runtime raises a clear
            # ValueError when ``ref_gdf`` is None.
        }


__all__ = ["SnapPointsToLinesCapability"]
