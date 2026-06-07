"""Split lines at points (capability C).

Where :class:`~gispulse.capabilities.network_topology.PlanarizeCapability`
splits a line network at its *self*-intersections, this splits a line layer
at the locations of a **separate** point layer — the classic "split road at
access points / mileposts / events" operation.

Each input line is cut at every reference point lying within ``tolerance_m``
of it (``0`` = points exactly on the line). Source attributes are copied
onto every resulting segment and the originating feature is recorded in
``parent_edge_id`` (configurable via ``parent_id_col``). Output is a flat,
single-type GeoDataFrame of LineString segments (no ``feature_type`` tag —
unlike ``planarize`` there are no node rows).

Candidate points are found through a
:class:`~gispulse.core.spatial_index.SpatialIndex` (no O(n·m) scan) and the
cutting reuses the shared ``_cut`` / ``_collect_measures`` helpers of
:mod:`gispulse.capabilities.network_topology`. Processing happens in a
metric CRS so ``tolerance_m`` is in meters; the result is reprojected to the
input CRS.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.network_topology import _cut, _work_in_metric, _restore_crs
from gispulse.capabilities.registry import register
from gispulse.core.spatial_index import SpatialIndex


@register
class SplitLinesAtPointsCapability(Capability):
    """Splits each line at the reference points lying within *tolerance_m*."""

    name = "split_lines_at_points"
    description = (
        "Splits each line at every reference point within tolerance_m "
        "(0 = points exactly on the line), copying source attributes onto "
        "each segment and recording the source feature in parent_edge_id. "
        "Returns a flat GeoDataFrame of LineString segments."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        tolerance_m: float = 0.0,
        id_col: str | None = None,
        parent_id_col: str = "parent_edge_id",
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           Line layer (LineString / MultiLineString).
            ref_gdf:       Point layer whose locations cut the lines
                           (injected via ``ref_layer``). Non-point geometries
                           contribute their centroid.
            tolerance_m:   Max distance (m) between a point and a line for the
                           point to split it. ``0`` splits only at points that
                           touch the line exactly.
            id_col:        Column on ``gdf`` seeding ``parent_edge_id``.
                           Defaults to the row position; multi-part rows get a
                           ``<id>#<part>`` suffix.
            parent_id_col: Output column recording the source feature id.
            crs_meters:    Metric CRS for ``tolerance_m`` / projection. The
                           result is reprojected to the input CRS.

        Returns:
            Flat GeoDataFrame of LineString segments, one row per cut piece,
            in the input CRS.
        """
        from shapely.geometry import LineString, MultiLineString
        from shapely.ops import substring

        if tolerance_m < 0:
            raise ValueError("tolerance_m must be >= 0.")
        if ref_gdf is None or ref_gdf.empty:
            # Nothing to cut at — return the lines unchanged but tagged with
            # their parent id so the schema is stable across runs.
            return self._passthrough(gdf, id_col, parent_id_col)
        if gdf.empty:
            return gdf.copy()

        work, original_crs = _work_in_metric(gdf, crs_meters)
        ref = (
            ref_gdf.to_crs(crs_meters)
            if ref_gdf.crs is not None and str(ref_gdf.crs) != crs_meters
            else ref_gdf.copy()
        )

        # Reference points (centroid for non-points), indexed once.
        pts = []
        for g in ref.geometry:
            if g is None or g.is_empty:
                continue
            pts.append(g if g.geom_type == "Point" else g.centroid)
        if not pts:
            return _restore_crs(
                self._passthrough(work, id_col, parent_id_col), original_crs
            )
        index = SpatialIndex(pts)

        attr_cols = [
            c
            for c in work.columns
            if c != work.geometry.name and c != parent_id_col
        ]

        # Count parts per row so we know when to suffix multi-part ids.
        part_counts: dict[int, int] = {}
        for row_pos, geom in enumerate(work.geometry):
            if geom is None or geom.is_empty:
                continue
            members = geom.geoms if isinstance(geom, MultiLineString) else [geom]
            part_counts[row_pos] = sum(
                1 for m in members if isinstance(m, LineString) and len(m.coords) >= 2
            )

        out_rows: list[dict[str, Any]] = []
        for row_pos, geom in enumerate(work.geometry):
            if geom is None or geom.is_empty:
                continue
            members = geom.geoms if isinstance(geom, MultiLineString) else [geom]
            base_id = (
                work.iloc[row_pos][id_col]
                if id_col is not None and id_col in work.columns
                else row_pos
            )
            part_idx = 0
            for line in members:
                if not isinstance(line, LineString) or len(line.coords) < 2:
                    continue
                measures: set[float] = set()
                for pos in index.query_radius(line, tolerance_m):
                    measures.add(line.project(pts[pos]))

                parent_id: Any = base_id
                if part_counts.get(row_pos, 1) > 1:
                    parent_id = f"{base_id}#{part_idx}"
                part_idx += 1

                for seg in _cut(line, measures, substring):
                    row: dict[str, Any] = {parent_id_col: parent_id, "geometry": seg}
                    for c in attr_cols:
                        row[c] = work.iloc[row_pos][c]
                    out_rows.append(row)

        if not out_rows:
            return _restore_crs(
                self._passthrough(work, id_col, parent_id_col), original_crs
            )

        out = gpd.GeoDataFrame(out_rows, geometry="geometry", crs=work.crs)
        return _restore_crs(out, original_crs).reset_index(drop=True)

    @staticmethod
    def _passthrough(
        gdf: gpd.GeoDataFrame, id_col: str | None, parent_id_col: str
    ) -> gpd.GeoDataFrame:
        """Return *gdf* with a stable ``parent_id_col`` and no geometry change."""
        out = gdf.copy()
        if id_col is not None and id_col in out.columns:
            out[parent_id_col] = out[id_col].tolist()
        else:
            out[parent_id_col] = list(range(len(out)))
        return out.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Reference point layer whose locations cut the lines.",
                },
                "tolerance_m": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.0,
                    "description": "Max point-to-line distance (m) to split; 0 = exact.",
                },
                "id_col": {
                    "type": ["string", "null"],
                    "description": "Column seeding parent_edge_id; defaults to row position.",
                },
                "parent_id_col": {
                    "type": "string",
                    "default": "parent_edge_id",
                    "description": "Output column recording the source feature id.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                    "description": "Metric CRS for tolerance / projection.",
                },
            },
            # ``ref_layer`` is pipeline plumbing (stripped before validation);
            # a missing reference is handled as a no-op passthrough.
        }


__all__ = ["SplitLinesAtPointsCapability"]
