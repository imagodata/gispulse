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

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register


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


def _collect_measures(line, inter, measures: set[float]) -> None:
    """Add the along-*line* measures of every point implied by *inter*.

    Point/MultiPoint crossings contribute their projection; overlapping
    line parts contribute the projections of their boundary endpoints
    (so a shared run still produces clean break points). Shared by
    :class:`PlanarizeCapability` and ``split_lines_at_points``.
    """
    if inter.is_empty:
        return
    gt = inter.geom_type
    if gt == "Point":
        measures.add(line.project(inter))
    elif gt == "MultiPoint":
        for p in inter.geoms:
            measures.add(line.project(p))
    elif gt == "LineString":
        for p in inter.boundary.geoms:
            measures.add(line.project(p))
    elif gt in ("MultiLineString", "GeometryCollection"):
        for g in inter.geoms:
            _collect_measures(line, g, measures)


def _cut(line, measures: set[float], substring) -> list:
    """Cut *line* at the given along-line *measures*, return sub-segments."""
    length = line.length
    eps = 1e-9
    cuts = sorted(m for m in measures if eps < m < length - eps)
    if not cuts:
        return [line]
    bounds = [0.0, *cuts, length]
    out = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a > eps:
            seg = substring(line, a, b)
            if seg is not None and not seg.is_empty and seg.length > eps:
                out.append(seg)
    return out or [line]


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


# ---------------------------------------------------------------------------
# PlanarizeCapability
# ---------------------------------------------------------------------------


class _UnionFind:
    """Minimal union-find for clustering coincident endpoints."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


@register
class PlanarizeCapability(Capability):
    """Planar noding of a line network, preserving attributes and parentage.

    Unlike :class:`NodeLinesCapability` (a bare ``unary_union`` that drops
    every attribute), this splits *each* line at its intersections with the
    others while keeping the source attributes on every resulting segment
    and recording the originating feature in ``parent_edge_id``. It also
    emits the planar graph's nodes.

    Output is a single GeoDataFrame tagged ``feature_type`` (the standing
    multi-output contract):

    * segment rows: ``feature_type="segment"``, ``parent_edge_id``, the
      source attributes, a LineString ``geometry``;
    * node rows: ``feature_type="node"``, ``node_id``, ``degree``, a Point.

    Quasi-intersections (lines that pass within ``snap_tolerance_m`` without
    truly crossing) are also split, and segment endpoints within that
    distance are fused into a single node — so a slightly mis-digitised
    network still planarises cleanly. Use ``split_planar_graph`` to recover
    ``(nodes_gdf, segments_gdf)``.
    """

    name = "planarize"
    description = (
        "Planar noding of a line network: splits each line at every "
        "intersection (and near-intersection within snap_tolerance_m), "
        "preserving attributes + parent_edge_id and emitting graph nodes. "
        "Returns a GeoDataFrame tagged feature_type=node/segment."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        id_col: str | None = None,
        snap_tolerance_m: float = 0.0,
        crs_meters: str | None = "EPSG:3857",
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:              Line network (LineString / MultiLineString).
            id_col:           Column seeding ``parent_edge_id``. Defaults to
                              the row position; multi-part rows get a
                              ``<id>#<part>`` suffix.
            snap_tolerance_m: Split lines that pass within this metric
                              distance of each other, and fuse segment
                              endpoints within it into one node. ``0`` uses
                              exact intersections only.
            crs_meters:       Metric CRS for splitting / tolerance. Result
                              is reprojected to the input CRS.

        Returns:
            One GeoDataFrame tagged ``feature_type`` (node|segment).
        """
        from shapely.geometry import LineString, MultiLineString, Point
        from shapely.ops import nearest_points, substring

        if gdf.empty:
            return self._empty(gdf.crs)

        work, original_crs = _work_in_metric(gdf, crs_meters)

        # Explode to (row_pos, part_idx, LineString) keeping the source row.
        parts: list[tuple[int, int, "LineString"]] = []
        part_counts: dict[int, int] = {}
        for row_pos, geom in enumerate(work.geometry):
            if geom is None or geom.is_empty:
                continue
            members = geom.geoms if isinstance(geom, MultiLineString) else [geom]
            pc = 0
            for line in members:
                if isinstance(line, LineString) and len(line.coords) >= 2:
                    parts.append((row_pos, pc, line))
                    pc += 1
            if pc:
                part_counts[row_pos] = pc

        if not parts:
            return self._empty(original_crs)

        part_geoms = [p[2] for p in parts]
        from shapely import STRtree

        tree = STRtree(part_geoms)
        tol = max(snap_tolerance_m, 0.0)

        attr_cols = [
            c
            for c in work.columns
            if c != work.geometry.name
            and c not in {"feature_type", "node_id", "degree",
                          "parent_edge_id"}
        ]

        segment_rows: list[dict[str, Any]] = []
        for pi, (row_pos, part_idx, line) in enumerate(parts):
            query_geom = line.buffer(tol) if tol > 0 else line
            measures: set[float] = set()
            for qj in tree.query(query_geom):
                pj = int(qj)
                if pj == pi:
                    continue
                other = part_geoms[pj]
                inter = line.intersection(other)
                _collect_measures(line, inter, measures)
                if tol > 0 and (inter.is_empty):
                    if line.distance(other) <= tol:
                        on_line, _ = nearest_points(line, other)
                        measures.add(line.project(on_line))

            base_id = work.iloc[row_pos][id_col] if (
                id_col is not None and id_col in work.columns
            ) else row_pos
            parent_id: Any = base_id
            if part_counts.get(row_pos, 1) > 1:
                parent_id = f"{base_id}#{part_idx}"

            for seg in _cut(line, measures, substring):
                row: dict[str, Any] = {
                    "feature_type": "segment",
                    "parent_edge_id": parent_id,
                    "geometry": seg,
                }
                for c in attr_cols:
                    row[c] = work.iloc[row_pos][c]
                segment_rows.append(row)

        node_rows = self._build_nodes(segment_rows, tol, Point)

        out = gpd.GeoDataFrame(
            node_rows + segment_rows, geometry="geometry", crs=work.crs
        )
        return _restore_crs(out, original_crs).reset_index(drop=True)

    def _build_nodes(self, segment_rows, tol: float, Point) -> list[dict[str, Any]]:
        """Build fused node rows from segment endpoints."""
        endpoints: list[tuple[float, float]] = []
        seg_ends: list[tuple[int, int]] = []
        for seg in segment_rows:
            coords = list(seg["geometry"].coords)
            s = len(endpoints)
            endpoints.append((coords[0][0], coords[0][1]))
            e = len(endpoints)
            endpoints.append((coords[-1][0], coords[-1][1]))
            seg_ends.append((s, e))

        node_key = self._fuse(endpoints, tol)
        members: dict[int, list[int]] = {}
        for ep_idx, key in enumerate(node_key):
            members.setdefault(key, []).append(ep_idx)

        node_id_of_key: dict[int, int] = {}
        degree: dict[int, int] = {}
        for new_id, key in enumerate(sorted(members)):
            node_id_of_key[key] = new_id
        for s, e in seg_ends:
            for ep in (s, e):
                nid = node_id_of_key[node_key[ep]]
                degree[nid] = degree.get(nid, 0) + 1

        rows: list[dict[str, Any]] = []
        for key in sorted(members):
            nid = node_id_of_key[key]
            xs = [endpoints[ep][0] for ep in members[key]]
            ys = [endpoints[ep][1] for ep in members[key]]
            rows.append(
                {
                    "feature_type": "node",
                    "node_id": nid,
                    "degree": degree.get(nid, 0),
                    "geometry": Point(sum(xs) / len(xs), sum(ys) / len(ys)),
                }
            )
        return rows

    @staticmethod
    def _fuse(endpoints: list[tuple[float, float]], tol: float) -> list[int]:
        if tol <= 0:
            key_of: dict[tuple[float, float], int] = {}
            out = []
            for x, y in endpoints:
                key = (round(x, 6), round(y, 6))
                if key not in key_of:
                    key_of[key] = len(key_of)
                out.append(key_of[key])
            return out

        from shapely import STRtree
        from shapely.geometry import Point as _P

        pts = [_P(x, y) for x, y in endpoints]
        tree = STRtree(pts)
        uf = _UnionFind(len(pts))
        for i, p in enumerate(pts):
            for j in tree.query(p.buffer(tol)):
                jj = int(j)
                if jj != i and pts[jj].distance(p) <= tol:
                    uf.union(i, jj)
        return [uf.find(i) for i in range(len(pts))]

    def _empty(self, crs: Any) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {
                "feature_type": [],
                "node_id": [],
                "degree": [],
                "parent_edge_id": [],
                "geometry": [],
            },
            geometry="geometry",
            crs=crs,
        )

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "id_col": {
                    "type": ["string", "null"],
                    "description": "Column seeding parent_edge_id; defaults to row position.",
                },
                "snap_tolerance_m": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.0,
                    "description": "Split near-intersections and fuse nodes within this metric distance.",
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": "EPSG:3857",
                    "description": "Metric CRS for splitting/tolerance.",
                },
            },
        }


def split_planar_graph(
    graph_gdf: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Split a ``planarize`` result into ``(nodes_gdf, segments_gdf)``."""
    nodes = graph_gdf[graph_gdf["feature_type"] == "node"].dropna(axis=1, how="all")
    segments = graph_gdf[graph_gdf["feature_type"] == "segment"].dropna(
        axis=1, how="all"
    )
    return nodes.reset_index(drop=True), segments.reset_index(drop=True)
