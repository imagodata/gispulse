"""Snap points to a line network with stable edge ids (capability B).

Where ``line_locate_point`` returns only a measure plus a *positional*
``ref_index``, this capability returns everything needed to attach an event
to a routable edge:

* ``edge_id``         -- the matched line's id (from ``ref_id_col``), stable
  across runs (not a row position);
* ``measure``         -- distance along the matched line, in meters, from its
  start (in ``[0, line_length]``); null when unsnapped;
* ``offset_distance`` -- perpendicular distance from the point to its nearest
  line (always reported, snapped or not);
* ``snapped``         -- ``True`` when ``offset_distance <= max_distance_m``
  (or ``max_distance_m is None``), ``False`` otherwise;
* ``rank``            -- candidate rank, nearest first;
* ``geometry``        -- for snapped rows, the **projected** point on the
  candidate line; for unsnapped rows the original point is kept.

A point beyond ``max_distance_m`` is reported with ``snapped=False``,
``edge_id``/``measure`` null and its original geometry -- only
``offset_distance`` records how far its nearest line lies, so the consumer
can decide what to do. A null/empty input geometry has no nearest line and
stays ``edge_id=None``, ``snapped=False`` with its geometry untouched.

Candidate lines are found through a :class:`~gispulse.core.spatial_index.SpatialIndex`
(STRtree, no O(n*m) scan); each point projects directly onto its segment.
Ties -- several lines exactly equidistant from one point -- break
deterministically on the smallest ``edge_id`` so the same inputs always
produce the same outputs.

All metric quantities (``measure``, ``offset_distance``, ``max_distance_m``)
are computed in ``crs_meters`` (a projected CRS); inputs in another CRS are
reprojected in and the result is reprojected back to the points' original
CRS. ``crs_meters`` must be a metric/projected CRS -- never feed it degrees.

This capability is deliberately **generic**: it carries no business notion
(site, fibre edge, river reach...). Accidents on a road network, meters on a
utility line and discharges on a watercourse are all the same operation --
project points onto identified lines -- and none is special-cased here.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.core.spatial_index import SpatialIndex

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
    if isinstance(value, bool):  # bool is an int subclass -- keep it on the str side
        return (1, str(value))
    if isinstance(value, (int, float)):
        return (0, value)
    return (1, str(value))


@register
class SnapPointsToLinesCapability(Capability):
    """Projects points onto a line network, tagging each with its edge id."""

    name = "snap_points_to_lines"
    description = (
        "Snaps each point to the nearest candidate line(s), adding edge_id "
        "(from ref_id_col), measure, offset_distance, rank and a snapped flag; "
        "geometry becomes the projected point on the candidate line."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        ref_id_col: str | None = None,
        k: int = 1,
        max_distance_m: float | None = None,
        crs_meters: str = "EPSG:3857",
        edge_id_col: str = "edge_id",
        measure_col: str = "measure",
        offset_col: str = "offset_distance",
        snapped_col: str = "snapped",
        rank_col: str = "rank",
        **_,
    ) -> gpd.GeoDataFrame:
        """Project each point onto its nearest reference line candidates.

        Args:
            gdf:            Input points (non-points use their centroid). All
                            input columns are preserved on the output.
            ref_gdf:        Reference line layer (injected via ``ref_layer``).
            ref_id_col:     Column on ``ref_gdf`` providing the stable
                            ``edge_id``. **Required** -- its absence raises a
                            clear ``ValueError`` rather than silently falling
                            back to a positional index.
            k:              Number of candidate lines to keep per input point.
                            ``k=1`` returns one row per input point; ``k>1``
                            duplicates rows only for snapped candidates.
            max_distance_m: Snap threshold in meters. A point whose nearest
                            line is farther than this is left unsnapped:
                            ``snapped=False``, ``edge_id``/``measure`` null
                            and the original geometry kept, with only
                            ``offset_distance`` reporting the true nearest
                            distance. ``None`` snaps every point to its
                            nearest candidate lines.
            crs_meters:     Metric (projected) CRS used for all distances.
            edge_id_col:    Output column for the matched edge id.
            measure_col:    Output column for the along-line measure (m).
            offset_col:     Output column for the perpendicular offset (m).
            snapped_col:    Output boolean column.
            rank_col:       Output column for candidate rank, nearest first.

        Returns:
            Copy of ``gdf`` (input columns intact) with output columns added
            and ``geometry`` replaced by projected points, in the input CRS.

        Raises:
            ValueError: if ``ref_gdf`` is missing/empty, if ``ref_id_col`` is
                not a column of ``ref_gdf``, or if ``k < 1``.
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
        if k < 1:
            raise ValueError("k must be >= 1.")

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
        geom_col = left.geometry.name

        rows: list[dict] = []

        def add_unsnapped(row_i: int, offset: float = np.nan) -> None:
            row = left.iloc[row_i].to_dict()
            row[edge_id_col] = None
            row[measure_col] = np.nan
            row[offset_col] = offset
            row[snapped_col] = False
            row[rank_col] = 1
            rows.append(row)

        def add_snapped(row_i: int, pt, pos: int, dist: float, rank: int) -> None:
            line = line_geoms[pos]
            measure = line.project(pt)
            row = left.iloc[row_i].to_dict()
            row[edge_id_col] = ref_ids[pos]
            row[measure_col] = float(measure)
            row[offset_col] = float(dist)
            row[snapped_col] = True
            row[rank_col] = rank
            row[geom_col] = line.interpolate(measure)
            rows.append(row)

        for row_i, geom in enumerate(left.geometry):
            if geom is None or geom.is_empty:
                add_unsnapped(row_i)
                continue
            pt = geom if geom.geom_type == "Point" else geom.centroid

            candidates = self._candidate_lines(index, line_geoms, ref_ids, pt, k, max_distance_m)
            if not candidates:
                near = index.nearest(pt)
                offset = np.nan
                if near:
                    offset = float(line_geoms[near[0]].distance(pt))
                add_unsnapped(row_i, offset)
                continue

            for rank, (pos, dist) in enumerate(candidates, start=1):
                add_snapped(row_i, pt, pos, dist, rank)

        if rows:
            out = gpd.GeoDataFrame(rows, geometry=geom_col, crs=crs_meters)
        else:
            out = left.copy()
            out[edge_id_col] = np.array([], dtype=object)
            out[measure_col] = np.array([], dtype=float)
            out[offset_col] = np.array([], dtype=float)
            out[snapped_col] = np.array([], dtype=bool)
            out[rank_col] = np.array([], dtype=int)
        if original_crs is not None:
            out = out.to_crs(original_crs)
        return out.reset_index(drop=True)

    @staticmethod
    def _candidate_lines(
        index: SpatialIndex,
        line_geoms: list,
        ref_ids: list,
        pt,
        k: int,
        max_distance_m: float | None,
    ) -> "list[tuple[int, float]]":
        """Return up to k (line position, distance) matches, nearest first."""
        if max_distance_m is None:
            nearest = index.nearest(pt, k=min(k, len(line_geoms)))
            if not nearest:
                return []
            cutoff = max(float(line_geoms[pos].distance(pt)) for pos in nearest)
            radius = cutoff + _TIE_TOL_M
            candidates = [
                (pos, float(line_geoms[pos].distance(pt))) for pos in index.query_radius(pt, radius)
            ]
            candidates = [(pos, dist) for pos, dist in candidates if dist <= radius]
        else:
            candidates = [
                (pos, float(line_geoms[pos].distance(pt)))
                for pos in index.query_radius(pt, max_distance_m)
            ]
            candidates = [(pos, dist) for pos, dist in candidates if dist <= max_distance_m]

        if not candidates:
            return []

        candidates.sort(key=lambda item: (item[1], _id_sort_key(ref_ids[item[0]])))
        if k == 1:
            best_dist = candidates[0][1]
            tied = [item for item in candidates if item[1] <= best_dist + _TIE_TOL_M]
            return [min(tied, key=lambda item: _id_sort_key(ref_ids[item[0]]))]
        return candidates[:k]

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
                "k": {
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "description": "Number of candidate lines to keep per input point.",
                },
                "max_distance_m": {
                    "type": ["number", "null"],
                    "description": (
                        "Snap threshold (m). Beyond it, snapped=False with "
                        "edge_id/measure null and the original geometry kept "
                        "(only offset_distance reported). None snaps every "
                        "point to its nearest line."
                    ),
                },
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
                "edge_id_col": {"type": "string", "default": "edge_id"},
                "measure_col": {"type": "string", "default": "measure"},
                "offset_col": {"type": "string", "default": "offset_distance"},
                "snapped_col": {"type": "string", "default": "snapped"},
                "rank_col": {
                    "type": "string",
                    "default": "rank",
                    "description": "Output column for candidate rank, nearest first.",
                },
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
