"""Network topology repair capabilities — consolidate / clean line networks.

Inspired by QGIS' *v.clean* (GRASS) and *osmnx.consolidate_intersections*.
Provides five building blocks to clean a line network before routing or
any downstream graph analysis:

1. :class:`SnapEndpointsCapability`       — snap near-coincident endpoints
   together (tolerance-based).
2. :class:`RemovePseudoNodesCapability`   — dissolve degree-2 nodes by
   merging the two incident lines into one.
3. :class:`NodeLinesCapability`           — split every line at every
   intersection with another line (creates a planar graph).
4. :class:`ExtendDanglesCapability`       — extend dangling endpoints to
   the nearest line within a tolerance.
5. :class:`RemoveDuplicateEdgesCapability` — drop edges whose geometries
   equal an existing edge (within a tolerance).

All capabilities work in the input CRS unless ``crs_meters`` is provided,
in which case processing happens in that metric CRS and the result is
reprojected back to the original.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd

from capabilities.base import Capability
from capabilities.registry import register


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _work_in_metric(
    gdf: gpd.GeoDataFrame,
    crs_meters: str | None,
) -> tuple[gpd.GeoDataFrame, Any]:
    """Return (projected gdf, original CRS) or (copy, None)."""
    if crs_meters and gdf.crs is not None and str(gdf.crs) != crs_meters:
        return gdf.to_crs(crs_meters), gdf.crs
    return gdf.copy(), gdf.crs


def _restore_crs(gdf: gpd.GeoDataFrame, original_crs: Any) -> gpd.GeoDataFrame:
    """Reproject back to *original_crs* if they differ."""
    if original_crs is not None and gdf.crs is not None and str(gdf.crs) != str(original_crs):
        return gdf.to_crs(original_crs)
    return gdf


# ---------------------------------------------------------------------------
# SnapEndpointsCapability
# ---------------------------------------------------------------------------


@register
class SnapEndpointsCapability(Capability):
    """Snaps line endpoints that are within *tolerance* to a shared position."""

    name = "network_snap_endpoints"
    description = (
        "Snaps near-coincident endpoints of a line network together. "
        "Eliminates gaps shorter than *tolerance* meters."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        tolerance: float = 0.5,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame of LineStrings.
            tolerance:  Max distance between two endpoints to merge them
                        (in units of *crs_meters*).
            crs_meters: Metric CRS used for *tolerance* interpretation.
        """
        from shapely.geometry import LineString

        if tolerance <= 0:
            raise ValueError("tolerance must be > 0.")
        if gdf.empty:
            return gdf.copy()

        work, original_crs = _work_in_metric(gdf, crs_meters)

        # Collect all endpoints with an index per feature/end.
        endpoints: list[tuple[int, int, float, float]] = []
        for i, geom in enumerate(work.geometry):
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type != "LineString":
                continue
            cs = list(geom.coords)
            if len(cs) < 2:
                continue
            endpoints.append((i, 0, cs[0][0], cs[0][1]))
            endpoints.append((i, -1, cs[-1][0], cs[-1][1]))

        # Snap each endpoint to the round-toleranced grid and pick a canonical
        # coordinate per group (mean of the group).
        tol = tolerance
        # Use an integer grid of size tolerance to bucket points; canonical
        # position = centroid of each bucket.
        buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
        key_of: dict[tuple[int, int], tuple[int, int]] = {}
        for idx, end, x, y in endpoints:
            bx, by = round(x / tol), round(y / tol)
            # Check neighbours (3x3) for an existing bucket — tolerance is a
            # radius, not a grid cell, so points near a boundary would miss
            # their group otherwise.
            chosen_key: tuple[int, int] | None = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    cand = (bx + dx, by + dy)
                    if cand in buckets:
                        # Accept only if centroid distance is within tolerance
                        cx = sum(p[0] for p in buckets[cand]) / len(buckets[cand])
                        cy = sum(p[1] for p in buckets[cand]) / len(buckets[cand])
                        if ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5 <= tol:
                            chosen_key = cand
                            break
                if chosen_key is not None:
                    break
            if chosen_key is None:
                chosen_key = (bx, by)
                buckets[chosen_key] = []
            buckets[chosen_key].append((x, y))
            key_of[(idx, end)] = chosen_key

        canonical: dict[tuple[int, int], tuple[float, float]] = {
            k: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            for k, pts in buckets.items()
        }

        # Rebuild geometries with snapped endpoints.
        new_geoms = []
        for i, geom in enumerate(work.geometry):
            if geom is None or geom.is_empty or geom.geom_type != "LineString":
                new_geoms.append(geom)
                continue
            cs = list(geom.coords)
            if len(cs) < 2:
                new_geoms.append(geom)
                continue
            start = canonical.get(key_of.get((i, 0), ()))
            end = canonical.get(key_of.get((i, -1), ()))
            new_coords = list(cs)
            if start is not None:
                new_coords[0] = (start[0], start[1]) + tuple(cs[0][2:])
            if end is not None:
                new_coords[-1] = (end[0], end[1]) + tuple(cs[-1][2:])
            new_geoms.append(LineString(new_coords))

        work["geometry"] = new_geoms
        return _restore_crs(work, original_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tolerance": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.5,
                    "description": "Max distance to merge endpoints, in crs_meters units.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                },
            },
            "required": ["tolerance"],
        }


# ---------------------------------------------------------------------------
# RemovePseudoNodesCapability
# ---------------------------------------------------------------------------


@register
class RemovePseudoNodesCapability(Capability):
    """Dissolves degree-2 nodes of the network by concatenating incident lines.

    A *pseudo-node* is a node connecting exactly two line segments that are
    not otherwise needed to break the topology (no attribute change, no
    ramification). This capability walks the graph, finds such nodes, and
    merges the two incident lines into a single one.
    """

    name = "network_remove_pseudo_nodes"
    description = (
        "Merges consecutive line segments meeting at degree-2 (pseudo) nodes "
        "so the network graph becomes minimal."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        crs_meters: str | None = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame of LineStrings. Attribute values
                        are *lost* on merged edges (keep only geometry).
            crs_meters: Work in this metric CRS (optional).
        """
        from shapely.ops import linemerge
        from shapely.geometry import MultiLineString

        if gdf.empty:
            return gdf.copy()

        work, original_crs = _work_in_metric(gdf, crs_meters)
        geoms = [g for g in work.geometry if g is not None and not g.is_empty]

        if not geoms:
            return _restore_crs(work, original_crs)

        merged = linemerge(MultiLineString(geoms))
        if merged.is_empty:
            out_geoms = []
        elif merged.geom_type == "LineString":
            out_geoms = [merged]
        else:
            out_geoms = list(merged.geoms)

        out = gpd.GeoDataFrame(
            {"id": list(range(len(out_geoms))), "geometry": out_geoms},
            crs=work.crs,
        )
        return _restore_crs(out, original_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": "EPSG:3857",
                },
            },
        }


# ---------------------------------------------------------------------------
# NodeLinesCapability
# ---------------------------------------------------------------------------


@register
class NodeLinesCapability(Capability):
    """Splits each line at every intersection with another line (planar noding)."""

    name = "network_node_lines"
    description = (
        "Produces a planar graph by splitting every line at each intersection "
        "with another line of the same layer."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        crs_meters: str | None = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame of LineStrings.
            crs_meters: Work in this metric CRS (optional).
        """
        from shapely.ops import unary_union

        if gdf.empty:
            return gdf.copy()

        work, original_crs = _work_in_metric(gdf, crs_meters)
        geoms = [g for g in work.geometry if g is not None and not g.is_empty]

        if not geoms:
            return _restore_crs(work, original_crs)

        # unary_union on a collection of lines computes the noded multiline:
        # every intersection vertex becomes an endpoint of both lines.
        noded = unary_union(geoms)
        if noded.geom_type == "LineString":
            out_geoms = [noded]
        elif noded.geom_type == "MultiLineString":
            out_geoms = list(noded.geoms)
        else:
            out_geoms = [noded]

        out = gpd.GeoDataFrame(
            {"id": list(range(len(out_geoms))), "geometry": out_geoms},
            crs=work.crs,
        )
        return _restore_crs(out, original_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": "EPSG:3857",
                },
            },
        }


# ---------------------------------------------------------------------------
# ExtendDanglesCapability
# ---------------------------------------------------------------------------


@register
class ExtendDanglesCapability(Capability):
    """Extends dangling endpoints to the nearest line within *tolerance*."""

    name = "network_extend_dangles"
    description = (
        "Extends dangling (unconnected) line endpoints up to *tolerance* meters "
        "to the nearest line so small gaps are closed."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        tolerance: float = 1.0,
        crs_meters: str = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame of LineStrings.
            tolerance:  Max extension distance, in units of *crs_meters*.
            crs_meters: Metric CRS for tolerance interpretation.
        """
        from shapely.geometry import LineString, Point
        from shapely.ops import nearest_points

        if tolerance <= 0:
            raise ValueError("tolerance must be > 0.")
        if gdf.empty:
            return gdf.copy()

        work, original_crs = _work_in_metric(gdf, crs_meters)

        # Build a spatial index of the line network for fast endpoint queries.
        sindex = work.sindex

        new_geoms = []
        for idx, geom in enumerate(work.geometry):
            if geom is None or geom.is_empty or geom.geom_type != "LineString":
                new_geoms.append(geom)
                continue
            coords = list(geom.coords)
            if len(coords) < 2:
                new_geoms.append(geom)
                continue

            for end_idx in (0, -1):
                pt = Point(coords[end_idx])

                # Candidate lines within tolerance — exclude self.
                bbox = pt.buffer(tolerance).bounds
                cand = list(sindex.intersection(bbox))
                cand = [c for c in cand if c != idx]
                if not cand:
                    continue

                best_dist = tolerance + 1.0
                best_proj: Point | None = None
                for c in cand:
                    other = work.geometry.iloc[c]
                    if other is None or other.is_empty:
                        continue
                    # nearest_points returns (point on pt, point on other)
                    _, near = nearest_points(pt, other)
                    d = pt.distance(near)
                    if d < best_dist:
                        best_dist = d
                        best_proj = near

                if best_proj is not None and 0 < best_dist <= tolerance:
                    new_coord = (best_proj.x, best_proj.y) + tuple(coords[end_idx][2:])
                    coords[end_idx] = new_coord

            new_geoms.append(LineString(coords))

        work["geometry"] = new_geoms
        return _restore_crs(work, original_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tolerance": {
                    "type": "number",
                    "minimum": 0,
                    "default": 1.0,
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                },
            },
            "required": ["tolerance"],
        }


# ---------------------------------------------------------------------------
# RemoveDuplicateEdgesCapability
# ---------------------------------------------------------------------------


@register
class RemoveDuplicateEdgesCapability(Capability):
    """Removes duplicate / coincident edges from a line network."""

    name = "network_remove_duplicates"
    description = (
        "Drops line features whose geometry equals an earlier feature "
        "(within *tolerance*, ignoring direction)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        tolerance: float = 0.01,
        crs_meters: str | None = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input GeoDataFrame of LineStrings.
            tolerance:  Max Hausdorff distance between two geometries to be
                        treated as duplicates (in units of *crs_meters*).
            crs_meters: Metric CRS for tolerance (optional).
        """
        if tolerance < 0:
            raise ValueError("tolerance must be >= 0.")
        if gdf.empty:
            return gdf.copy()

        work, original_crs = _work_in_metric(gdf, crs_meters)

        keep_indices: list[int] = []
        kept_geoms = []
        for i, geom in enumerate(work.geometry):
            if geom is None or geom.is_empty:
                continue
            duplicate = False
            for existing in kept_geoms:
                if geom.equals_exact(existing, tolerance=tolerance):
                    duplicate = True
                    break
                # Hausdorff handles direction-independent equality for lines.
                if geom.geom_type == existing.geom_type == "LineString":
                    if geom.hausdorff_distance(existing) <= tolerance:
                        duplicate = True
                        break
            if not duplicate:
                keep_indices.append(i)
                kept_geoms.append(geom)

        out = work.iloc[keep_indices].reset_index(drop=True)
        return _restore_crs(out, original_crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tolerance": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.01,
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": "EPSG:3857",
                },
            },
        }
