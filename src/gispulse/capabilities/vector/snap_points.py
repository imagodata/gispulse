"""Snap points to a line network with stable edge ids (capability B).

Where ``line_locate_point`` returns only a measure plus a *positional*
``ref_index``, this capability returns everything needed to attach an event
to a routable edge:

* ``edge_id``         — the matched line's id (from ``ref_id_col``), stable
  across runs (not a row position);
* ``measure``         — distance along the matched line, in meters, from its
  start (in ``[0, line_length]``);
* ``offset_distance`` — perpendicular distance from the point to the line;
* ``snapped``         — ``True`` when ``offset_distance <= max_distance_m``
  (or ``max_distance_m is None``), ``False`` otherwise;
* ``geometry``        — replaced by the **projected** point on the nearest
  line.

The nearest line is *always* resolved, so ``edge_id`` and the projected
``geometry`` are populated even for unsnapped rows: a point beyond
``max_distance_m`` still carries its nearest edge and its projection, only
with ``snapped=False``. Filtering is therefore left to the consumer through
the ``snapped`` flag — no information is discarded. The single exception is a
null/empty input geometry, which has no nearest line and stays
``edge_id=None``, ``snapped=False`` with its (empty) geometry untouched.

Candidate lines are found through a :class:`~gispulse.core.spatial_index.SpatialIndex`
(STRtree, no O(n·m) scan); each point projects directly onto its segment.
Ties — several lines exactly equidistant from one point — break
deterministically on the smallest ``edge_id`` so the same inputs always
produce the same outputs.

All metric quantities (``measure``, ``offset_distance``, ``max_distance_m``)
are computed in ``crs_meters`` (a projected CRS); inputs in another CRS are
reprojected in and the result is reprojected back to the points' original
CRS. ``crs_meters`` must be a metric/projected CRS — never feed it degrees.

This capability is deliberately **generic**: it carries no business notion
(site, fibre edge, river reach…). Accidents on a road network, meters on a
utility line and discharges on a watercourse are all the same operation —
project points onto identified lines — and none is special-cased here.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.core.spatial_index import SpatialIndex

# How many nearest lines to inspect per point when breaking ties. Genuine
# geometric ties (several lines exactly equidistant from one point) are rare;
# 8 candidates is ample headroom while keeping each lookup O(k·log n) — far
# cheaper (and bounded) than a radius query whose buffer could match the whole
# network for a far-off point.
_TIE_CANDIDATES = 8
# Distances within this many CRS units (meters) are treated as equal for the
# purpose of tie-breaking on edge_id.
_TIE_TOL_M = 1e-9


def _id_sort_key(value: object) -> "tuple[int, object]":
    """Deterministic, total ordering key for tie-breaking on edge ids.

    Real ids in a single ``ref_id_col`` are homogeneous (all ints or all
    strings), where this reduces to natural order. The two-level key only
    guards the pathological mixed-type column so ``min`` never raises and the
    outcome stays reproducible.
    """
    if isinstance(value, bool):  # bool is an int subclass — keep it on the str side
        return (1, str(value))
    if isinstance(value, (int, float)):
        return (0, value)
    return (1, str(value))


@register
class SnapPointsToLinesCapability(Capability):
    """Projects points onto a line network, tagging each with its edge id."""

    name = "snap_points_to_lines"
    description = (
        "Snaps each point to the nearest line, adding edge_id (from "
        "ref_id_col), measure, offset_distance and a snapped flag; geometry "
        "becomes the projected point on the nearest line."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        ref_id_col: str | None = None,
        max_distance_m: float | None = None,
        crs_meters: str = "EPSG:3857",
        edge_id_col: str = "edge_id",
        measure_col: str = "measure",
        offset_col: str = "offset_distance",
        snapped_col: str = "snapped",
        **_,
    ) -> gpd.GeoDataFrame:
        """Project each point onto its nearest reference line.

        Args:
            gdf:            Input points (non-points use their centroid). All
                            input columns are preserved on the output.
            ref_gdf:        Reference line layer (injected via ``ref_layer``).
            ref_id_col:     Column on ``ref_gdf`` providing the stable
                            ``edge_id``. **Required** — its absence raises a
                            clear ``ValueError`` rather than silently falling
                            back to a positional index.
            max_distance_m: Snap threshold in meters. A point whose nearest
                            line is farther than this is reported with
                            ``snapped=False`` but **still** carries that
                            nearest line's ``edge_id`` and its projected
                            geometry — filtering is the consumer's call.
                            ``None`` snaps every point to its nearest line.
            crs_meters:     Metric (projected) CRS used for all distances.
            edge_id_col:    Output column for the matched edge id.
            measure_col:    Output column for the along-line measure (m).
            offset_col:     Output column for the perpendicular offset (m).
            snapped_col:    Output boolean column.

        Returns:
            Copy of ``gdf`` (input columns intact) with the four columns added
            and ``geometry`` replaced by the projected point, in the input CRS.

        Raises:
            ValueError: if ``ref_gdf`` is missing/empty, or if ``ref_id_col``
                is not a column of ``ref_gdf``.
        """
        if ref_gdf is None or ref_gdf.empty:
            raise ValueError(
                "snap_points_to_lines requires a non-empty reference line layer "
                "(ref_gdf). Pass the lines to project the points onto."
            )
        if ref_id_col is None or ref_id_col not in ref_gdf.columns:
            raise ValueError(
                "snap_points_to_lines: ref_id_col="
                f"{ref_id_col!r} is not a column of ref_gdf "
                f"(available columns: {list(ref_gdf.columns)}). "
                "Pass ref_id_col=<the stable id column on your lines> so every "
                "snapped point can carry a durable edge_id."
            )

        original_crs = gdf.crs
        left = gdf.to_crs(crs_meters) if original_crs is not None else gdf.copy()
        right = (
            ref_gdf.to_crs(crs_meters)
            if ref_gdf.crs is not None and str(ref_gdf.crs) != str(crs_meters)
            else ref_gdf.copy()
        )
        right = right.reset_index(drop=True)

        line_geoms = list(right.geometry)
        ref_ids = list(right[ref_id_col])
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

            pos = self._nearest_line(index, line_geoms, ref_ids, pt)
            if pos is None:  # ref layer had no usable geometry
                continue
            line = line_geoms[pos]
            measure = line.project(pt)
            offset = line.distance(pt)
            measures[row_i] = float(measure)
            offsets[row_i] = float(offset)
            edge_ids[row_i] = ref_ids[pos]
            new_geoms[row_i] = line.interpolate(measure)
            snapped[row_i] = max_distance_m is None or offset <= max_distance_m

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
    def _nearest_line(
        index: SpatialIndex,
        line_geoms: list,
        ref_ids: list,
        pt,
    ) -> "int | None":
        """Position of the nearest line to ``pt``, ties broken by smallest id.

        Inspects the ``_TIE_CANDIDATES`` nearest lines (O(k·log n)), keeps
        those within ``_TIE_TOL_M`` of the minimum distance, and returns the
        one with the smallest ``edge_id`` — a stable, deterministic choice.
        """
        near = index.nearest(pt, k=min(_TIE_CANDIDATES, len(line_geoms)))
        if not near:
            return None
        dists = {pos: line_geoms[pos].distance(pt) for pos in near}
        best_dist = min(dists.values())
        tied = [pos for pos, d in dists.items() if d <= best_dist + _TIE_TOL_M]
        return min(tied, key=lambda pos: _id_sort_key(ref_ids[pos]))

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Reference line layer to snap onto.",
                },
                "ref_id_col": {
                    "type": "string",
                    "description": (
                        "Required. Column on the lines providing the stable "
                        "edge_id; its absence raises a ValueError."
                    ),
                },
                "max_distance_m": {
                    "type": ["number", "null"],
                    "description": (
                        "Snap threshold (m). Beyond it, snapped=False but the "
                        "nearest edge_id and projection are still returned. "
                        "None snaps every point to its nearest line."
                    ),
                },
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
                "edge_id_col": {"type": "string", "default": "edge_id"},
                "measure_col": {"type": "string", "default": "measure"},
                "offset_col": {"type": "string", "default": "offset_distance"},
                "snapped_col": {"type": "string", "default": "snapped"},
            },
            # ``ref_id_col`` is genuinely required and is not plumbing, so it
            # can validate early. ``ref_layer`` must NOT appear in ``required``:
            # it is pipeline plumbing (resolved to ``ref_gdf`` and stripped
            # before schema validation), so requiring it would fail every v2
            # call before the capability runs. The runtime still raises a clear
            # ValueError when ``ref_gdf`` itself is missing.
            "required": ["ref_id_col"],
        }


__all__ = ["SnapPointsToLinesCapability"]
