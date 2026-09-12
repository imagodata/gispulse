"""Consolidate-networks capabilities — port of the QGIS *Consolidate Networks*
plugin (https://github.com/sducournau/consolidate_networks) to pure
shapely/GeoPandas GISPulse capabilities.

Eight line-network topology cleaning operations, ported faithfully from the
PyQGIS processing algorithms to backend-independent capabilities so they run
in every GISPulse surface (CLI, MCP, portal, DuckDB/PostGIS) without QGIS:

1. :class:`CnCalculateDbscanCapability`            — ``cn_calculate_dbscan``
2. :class:`CnConsolidateWithDbscanCapability`      — ``cn_consolidate_with_dbscan``
3. :class:`CnMakeIntersectionsVertexesCapability`  — ``cn_make_intersections_vertexes``
4. :class:`CnEndpointsTrimExtendCapability`        — ``cn_endpoints_trim_extend``
5. :class:`CnEndpointsSnappingCapability`          — ``cn_endpoints_snapping``
6. :class:`CnHubSnappingCapability`                — ``cn_hub_snapping``
7. :class:`CnSnapHubsToLayerCapability`            — ``cn_snap_hubs_to_layer``
8. :class:`CnSnapEndpointsToLayerCapability`       — ``cn_snap_endpoints_to_layer``

All capabilities work in the input CRS unless ``crs_meters`` is provided, in
which case processing happens in that metric CRS and the result is reprojected
back. Input/output geometries are line layers; ``gdf`` is never mutated
in-place.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register

# ---------------------------------------------------------------------------
# Shared helpers (the porting contract every cn_* capability relies on)
# ---------------------------------------------------------------------------


def _to_metric(gdf: gpd.GeoDataFrame, crs_meters: str | None) -> tuple[gpd.GeoDataFrame, Any]:
    """Return ``(projected copy in metres, original CRS)``.

    Never mutates ``gdf``; the returned frame is always a fresh object.
    """
    if crs_meters and gdf.crs is not None and str(gdf.crs) != str(crs_meters):
        return gdf.to_crs(crs_meters), gdf.crs
    return gdf.copy(), gdf.crs


def _restore(gdf: gpd.GeoDataFrame, original_crs: Any) -> gpd.GeoDataFrame:
    """Reproject back to ``original_crs`` if it differs from the current CRS."""
    if (
        original_crs is not None
        and gdf.crs is not None
        and str(gdf.crs) != str(original_crs)
    ):
        return gdf.to_crs(original_crs)
    return gdf


def _fix_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """make_valid every geometry and drop null/empty rows (qgis:fixgeometries)."""
    from shapely.validation import make_valid

    out = gdf.copy()
    col = out.geometry.name
    fixed = []
    for g in out.geometry.values:
        if g is None or g.is_empty:
            fixed.append(None)
            continue
        try:
            fixed.append(g if g.is_valid else make_valid(g))
        except Exception:
            fixed.append(g)
    out[col] = gpd.GeoSeries(fixed, index=out.index, crs=out.crs)
    mask = out[col].notna() & (~out[col].is_empty)
    return out[mask].reset_index(drop=True)


def _to_singleparts(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Explode multipart geometries to singleparts (qgis:multiparttosingleparts).

    Tags every row with a stable ``__cn_eid__`` (the *original* feature id)
    assigned before the explode, so parts coming from the same source feature
    can later be dissolved/merged back together by :func:`_gather_lines`.
    """
    out = gdf.copy()
    if "__cn_eid__" not in out.columns:
        out = out.reset_index(drop=True)
        out["__cn_eid__"] = range(len(out))
    out = out.explode(index_parts=False, ignore_index=True)
    return out


def _explode_lines(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Split each LineString into its 2-point segments (qgis:explodelines).

    Attribute rows (including ``__cn_eid__``) are repeated for every segment.
    Non-line / empty geometries pass through unchanged.
    """
    from shapely.geometry import LineString

    col = gdf.geometry.name
    parent_pos = []
    seg_geoms = []
    for pos, g in enumerate(gdf.geometry.values):
        if g is None or g.is_empty or g.geom_type != "LineString":
            parent_pos.append(pos)
            seg_geoms.append(g)
            continue
        coords = list(g.coords)
        if len(coords) < 2:
            parent_pos.append(pos)
            seg_geoms.append(g)
            continue
        for i in range(len(coords) - 1):
            parent_pos.append(pos)
            seg_geoms.append(LineString([coords[i], coords[i + 1]]))
    out = gdf.iloc[parent_pos].copy().reset_index(drop=True)
    out[col] = gpd.GeoSeries(seg_geoms, index=out.index, crs=gdf.crs)
    return out


def _gather_lines(gdf: gpd.GeoDataFrame, id_fields) -> gpd.GeoDataFrame:
    """Dissolve + linemerge segments back by ``id_fields`` (qgis:dissolve).

    ``id_fields`` falsy -> use the internal ``__cn_eid__`` tag if present,
    otherwise merge everything into a single geometry.
    """
    from shapely.ops import linemerge, unary_union

    col = gdf.geometry.name
    if not id_fields:
        id_fields = ["__cn_eid__"] if "__cn_eid__" in gdf.columns else []

    def _merge(geom_list):
        valid = [g for g in geom_list if g is not None and not g.is_empty]
        if not valid:
            return None
        u = unary_union(valid)
        try:
            return linemerge(u)
        except Exception:
            return u

    if not id_fields:
        merged = _merge(list(gdf.geometry.values))
        out = gdf.iloc[[0]].copy().reset_index(drop=True)
        out[col] = gpd.GeoSeries([merged], index=out.index, crs=gdf.crs)
        return out.drop(columns=["__cn_eid__"], errors="ignore")

    rows = []
    geoms = []
    for _key, grp in gdf.groupby(list(id_fields), sort=False):
        rows.append(grp.iloc[0])
        geoms.append(_merge(list(grp.geometry.values)))
    out = gpd.GeoDataFrame(rows, crs=gdf.crs).reset_index(drop=True)
    out[col] = gpd.GeoSeries(geoms, index=out.index, crs=gdf.crs)
    return out.drop(columns=["__cn_eid__"], errors="ignore")


def _extend_end(p_prev, p_end, distance):
    """Extrapolate beyond ``p_end`` along ``p_prev -> p_end`` by ``distance``."""
    import math

    dx = p_end[0] - p_prev[0]
    dy = p_end[1] - p_prev[1]
    n = math.hypot(dx, dy)
    if n == 0:
        return (p_end[0], p_end[1])
    return (p_end[0] + dx / n * distance, p_end[1] + dy / n * distance)


def _seg_angle_deg(p0, p1):
    """Angle of segment ``p0 -> p1`` in degrees, folded into ``[0, 180)``."""
    import math

    return math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0])) % 180


# ===========================================================================
# 1. CalculateDbscan
# ===========================================================================


@register
class CnCalculateDbscanCapability(Capability):
    name = "cn_calculate_dbscan"
    description = (
        "Cluster lines with DBSCAN over points sampled along them, tagging each "
        "line with CLUSTER_ID and CLUSTER_SIZE."
    )

    def execute(
        self,
        gdf,
        fix_geometries=True,
        points_dbscan_threshold_distance=0.1,
        dbscan_star=False,
        crs_meters="EPSG:3857",
        **_,
    ):
        import numpy as np
        from shapely.strtree import STRtree
        from sklearn.cluster import DBSCAN

        if gdf is None or gdf.empty:
            return gdf.copy() if gdf is not None else gdf

        work, original_crs = _to_metric(gdf, crs_meters)
        if fix_geometries:
            work = _fix_geometries(work)
        work = _to_singleparts(work)
        if work.empty:
            return _restore(work.drop(columns=["__cn_eid__"], errors="ignore"), original_crs)

        # qgis:pointsalonglines — sample points every `step` metres along lines.
        step = float(points_dbscan_threshold_distance)
        sample_pts = []
        sample_coords = []
        for geom in work.geometry:
            if geom is None or geom.is_empty:
                continue
            length = geom.length
            if length <= 0 or step <= 0:
                pt = geom.interpolate(0.0)
                sample_pts.append(pt)
                sample_coords.append((pt.x, pt.y))
                continue
            d = 0.0
            while d <= length:
                pt = geom.interpolate(d)
                sample_pts.append(pt)
                sample_coords.append((pt.x, pt.y))
                d += step

        out = work.copy()
        if not sample_coords:
            out["CLUSTER_ID"] = -1
            out["CLUSTER_SIZE"] = 0
            return _restore(out.drop(columns=["__cn_eid__"], errors="ignore"), original_crs)

        coords = np.asarray(sample_coords, dtype=float)
        # qgis:dbscanclustering — EPS=1, MIN_SIZE=5 are constants in the source.
        labels = DBSCAN(eps=1, min_samples=5).fit_predict(coords)

        cluster_size: dict[int, int] = {}
        for lab in labels:
            if lab != -1:
                cluster_size[lab] = cluster_size.get(lab, 0) + 1
        # QGIS emits 1-based cluster ids in order of appearance.
        remap: dict[int, int] = {}
        next_id = 1
        for lab in labels:
            if lab != -1 and lab not in remap:
                remap[lab] = next_id
                next_id += 1

        clustered_cid = [(-1 if lab == -1 else remap[lab]) for lab in labels]
        clustered_csize = [(0 if lab == -1 else cluster_size[lab]) for lab in labels]

        # qgis:joinattributesbylocation (intersects, METHOD=2 = first matched).
        tree = STRtree(sample_pts)
        cids = np.full(len(work), -1, dtype=int)
        csizes = np.full(len(work), 0, dtype=int)
        for i, geom in enumerate(work.geometry):
            if geom is None or geom.is_empty:
                continue
            for j in tree.query(geom):
                j = int(j)
                if geom.intersects(sample_pts[j]):
                    cids[i] = clustered_cid[j]
                    csizes[i] = clustered_csize[j]
                    break

        out["CLUSTER_ID"] = cids
        out["CLUSTER_SIZE"] = csizes
        return _restore(out.drop(columns=["__cn_eid__"], errors="ignore"), original_crs)

    def get_schema(self):
        return {
            "type": "object",
            "properties": {
                "fix_geometries": {"type": "boolean", "default": True},
                "points_dbscan_threshold_distance": {
                    "type": "number",
                    "default": 0.1,
                    "minimum": 0.0,
                    "description": "Spacing (m) between points sampled along each line.",
                },
                "dbscan_star": {
                    "type": "boolean",
                    "default": False,
                    "description": "DBSCAN* (border points as noise); see module notes.",
                },
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
        }


# ===========================================================================
# 2. ConsolidateWithDbscan
# ===========================================================================


def _cn_consolidate_nearest_vertex(point, candidates, tolerance):
    """Nearest vertex among ``candidates`` within ``tolerance``, or None."""
    from shapely.geometry import Point

    best = None
    best_d = None
    for geom in candidates:
        if geom is None or geom.is_empty:
            continue
        if point.distance(geom) > tolerance:
            continue
        for c in geom.coords:
            d = point.distance(Point(c[0], c[1]))
            if best_d is None or d < best_d:
                best_d = d
                best = (c[0], c[1])
    return best


@register
class CnConsolidateWithDbscanCapability(Capability):
    name = "cn_consolidate_with_dbscan"
    description = (
        "Snap line endpoints onto the nearest vertex of neighbouring lines "
        "belonging to a different DBSCAN cluster, within a buffer radius."
    )

    def execute(
        self,
        gdf,
        buffer_dbscan=5.0,
        fix_geometries_before_processing=True,
        crs_meters="EPSG:3857",
        **_,
    ):
        from shapely.geometry import LineString, Point

        if gdf is None or len(gdf) == 0:
            return gdf
        if "CLUSTER_ID" not in gdf.columns:
            raise ValueError(
                "cn_consolidate_with_dbscan requires a 'CLUSTER_ID' column "
                "(run cn_calculate_dbscan upstream)."
            )

        work, original_crs = _to_metric(gdf, crs_meters)
        if fix_geometries_before_processing:
            work = _fix_geometries(work)
        work = _to_singleparts(work).reset_index(drop=True)

        col = work.geometry.name
        geoms = list(work.geometry.values)
        clusters = list(work["CLUSTER_ID"])
        n = len(geoms)

        if "CLUSTER_SIZE" in work.columns:
            sizes = list(work["CLUSTER_SIZE"])
            order = sorted(range(n), key=lambda i: sizes[i])
        else:
            order = list(range(n))

        for i in order:
            g = geoms[i]
            if g is None or g.is_empty or g.geom_type != "LineString":
                continue
            coords = list(g.coords)
            if len(coords) < 2:
                continue
            own_cluster = clusters[i]
            # Reference set rebuilt against the CURRENT state (mutating layer).
            cand = [
                geoms[j]
                for j in range(n)
                if j != i
                and clusters[j] != own_cluster
                and geoms[j] is not None
                and not geoms[j].is_empty
            ]
            tgt = _cn_consolidate_nearest_vertex(Point(coords[0]), cand, buffer_dbscan)
            if tgt is not None:
                coords[0] = tgt
            tgt = _cn_consolidate_nearest_vertex(Point(coords[-1]), cand, buffer_dbscan)
            if tgt is not None:
                coords[-1] = tgt
            geoms[i] = LineString(coords)

        out = work.copy()
        out[col] = gpd.GeoSeries(geoms, index=out.index, crs=work.crs)
        out = out.drop(columns=["__cn_eid__"], errors="ignore")
        return _restore(out, original_crs)

    def get_schema(self):
        return {
            "type": "object",
            "properties": {
                "buffer_dbscan": {"type": "number", "minimum": 0.0, "default": 5.0},
                "fix_geometries_before_processing": {"type": "boolean", "default": True},
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
        }


# ===========================================================================
# 3. MakeIntersectionsVertexes
# ===========================================================================


def _cn_makeinter_iter_lines(geom):
    from shapely.geometry import LineString, MultiLineString

    if geom is None or geom.is_empty:
        return
    if isinstance(geom, LineString):
        yield geom
    elif isinstance(geom, MultiLineString):
        for part in geom.geoms:
            if part is not None and not part.is_empty:
                yield part


def _cn_makeinter_extract_points(geom):
    from shapely.geometry import (
        GeometryCollection,
        LineString,
        MultiLineString,
        MultiPoint,
        Point,
    )

    pts = []
    if geom is None or geom.is_empty:
        return pts
    if isinstance(geom, Point):
        pts.append(geom)
    elif isinstance(geom, MultiPoint):
        pts.extend(list(geom.geoms))
    elif isinstance(geom, (LineString, MultiLineString)):
        for line in _cn_makeinter_iter_lines(geom):
            coords = list(line.coords)
            if coords:
                pts.append(Point(coords[0]))
                pts.append(Point(coords[-1]))
    elif isinstance(geom, GeometryCollection):
        for sub in geom.geoms:
            pts.extend(_cn_makeinter_extract_points(sub))
    return pts


def _cn_makeinter_insert_points(line, points, tol=1e-9):
    from shapely.geometry import LineString, Point

    coords = list(line.coords)
    if len(coords) < 2 or not points:
        return line
    existing = [Point(c) for c in coords]
    base = [(line.project(p), c) for p, c in zip(existing, coords)]

    additions = []
    for pt in points:
        if any(pt.distance(ev) <= tol for ev in existing):
            continue
        if line.distance(pt) > tol:
            continue
        additions.append((line.project(pt), (pt.x, pt.y)))

    if not additions:
        return line
    merged = sorted(base + additions, key=lambda t: t[0])
    out_coords = []
    for _, c in merged:
        if not out_coords or out_coords[-1] != c:
            out_coords.append(c)
    if len(out_coords) < 2:
        return line
    return LineString(out_coords)


def _cn_makeinter_process_geom(geom, others_union):
    from shapely.geometry import MultiLineString

    if geom is None or geom.is_empty or others_union is None or others_union.is_empty:
        return geom
    new_parts = []
    for line in _cn_makeinter_iter_lines(geom):
        inter = line.intersection(others_union)
        points = _cn_makeinter_extract_points(inter)
        new_parts.append(_cn_makeinter_insert_points(line, points))
    if not new_parts:
        return geom
    if len(new_parts) == 1:
        return new_parts[0]
    return MultiLineString(new_parts)


@register
class CnMakeIntersectionsVertexesCapability(Capability):
    name = "cn_make_intersections_vertexes"
    description = (
        "Insert missing vertices into network lines at every point where they "
        "cross or touch another line without already having a vertex there."
    )

    def execute(
        self,
        gdf,
        entity_identification_fields=None,
        fix_geometries_before_processing=True,
        crs_meters="EPSG:3857",
        **_,
    ):
        from shapely.ops import unary_union

        if gdf is None or len(gdf) == 0:
            return gdf

        work, original_crs = _to_metric(gdf, crs_meters)
        if fix_geometries_before_processing:
            work = _fix_geometries(work)
        work = _to_singleparts(work)
        if len(work) == 0:
            return _restore(work.drop(columns=["__cn_eid__"], errors="ignore"), original_crs)

        col = work.geometry.name
        geoms = list(work.geometry.values)

        processed = []
        for i, g in enumerate(geoms):
            others = [
                og
                for j, og in enumerate(geoms)
                if j != i and og is not None and not og.is_empty
            ]
            if not others:
                processed.append(g)
                continue
            processed.append(_cn_makeinter_process_geom(g, unary_union(others)))

        out = work.copy()
        out[col] = gpd.GeoSeries(processed, index=out.index, crs=work.crs)

        # dissolve by entity identification fields (recombine split parts).
        if entity_identification_fields:
            dissolve_fields = list(entity_identification_fields)
        else:
            dissolve_fields = [c for c in gdf.columns if c != col]

        if dissolve_fields:
            dissolved = out.dissolve(by=dissolve_fields, as_index=False)
            if "__cn_eid__" in dissolved.columns:
                dissolved = dissolved.drop(columns="__cn_eid__")
        else:
            merged = unary_union(list(out.geometry.values))
            dissolved = out.iloc[[0]].copy().reset_index(drop=True)
            dissolved[col] = gpd.GeoSeries([merged], index=dissolved.index, crs=work.crs)
            dissolved = dissolved.drop(columns="__cn_eid__", errors="ignore")

        dissolved = dissolved.set_geometry(col)
        if dissolved.crs is None:
            dissolved = dissolved.set_crs(work.crs, allow_override=True)
        return _restore(dissolved, original_crs)

    def get_schema(self):
        return {
            "type": "object",
            "properties": {
                "entity_identification_fields": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "default": None,
                    "description": (
                        "Attribute columns identifying an entity; split parts "
                        "sharing the same values are dissolved back together. "
                        "Empty -> use all attribute columns."
                    ),
                },
                "fix_geometries_before_processing": {"type": "boolean", "default": True},
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
        }


# ===========================================================================
# 4. EndpointsStrimmingExtending (trim / extend)
# ===========================================================================


def _cn_trimext_unit(dx, dy):
    import math

    norm = math.hypot(dx, dy)
    if norm == 0:
        return None
    return dx / norm, dy / norm


def _cn_trimext_line_intersection(p, vp, q, vq):
    """Intersection of two infinite lines (point p dir vp, point q dir vq)."""
    denom = vp[0] * (-vq[1]) - vp[1] * (-vq[0])
    if abs(denom) < 1e-12:
        return False, None
    rhs0 = q[0] - p[0]
    rhs1 = q[1] - p[1]
    t = (rhs0 * (-vq[1]) - rhs1 * (-vq[0])) / denom
    return True, (p[0] + t * vp[0], p[1] + t * vp[1])


def _cn_trimext_interpolate(p_from, p_to, dist):
    import math

    dx = p_to[0] - p_from[0]
    dy = p_to[1] - p_from[1]
    seglen = math.hypot(dx, dy)
    if seglen == 0 or dist >= seglen:
        return p_to
    r = dist / seglen
    return (p_from[0] + dx * r, p_from[1] + dy * r)


def _cn_trimext_closest_segment(coords, pt):
    best = None
    for i in range(1, len(coords)):
        a = coords[i - 1]
        b = coords[i]
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        seg2 = dx * dx + dy * dy
        if seg2 == 0:
            t = 0.0
        else:
            t = ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / seg2
            t = max(0.0, min(1.0, t))
        projx = a[0] + t * dx
        projy = a[1] + t * dy
        sq = (pt[0] - projx) ** 2 + (pt[1] - projy) ** 2
        if best is None or sq < best[0]:
            best = (sq, (a, b))
    return best


def _cn_trimext_endpoint_candidates(
    base_coords,
    probe_point,
    feat_dir_vector,
    neighbor_geoms,
    buffer_radius,
    code,
    angular_limit,
    hausdorff_distance_limit,
):
    import math

    from shapely.geometry import LineString, Point

    base_geom = LineString(base_coords)
    feature_angle = _seg_angle_deg(base_coords[0], base_coords[-1])

    out = []
    for ng in neighbor_geoms:
        try:
            ncoords = list(ng.coords)
            if len(ncoords) < 2:
                continue
            cs = _cn_trimext_closest_segment(ncoords, probe_point)
            if cs is None:
                continue
            sq_dist, (seg_a, seg_b) = cs
            segment_geom = LineString([seg_a, seg_b])

            hausdorff_distance = base_geom.hausdorff_distance(segment_geom)
            nnfeature_segment_angle = _seg_angle_deg(seg_a, seg_b)
            if not (
                abs(feature_angle - nnfeature_segment_angle) > angular_limit
                and hausdorff_distance > hausdorff_distance_limit
            ):
                continue
            if sq_dist > buffer_radius ** 2:
                continue

            nndir = _cn_trimext_unit(seg_b[0] - seg_a[0], seg_b[1] - seg_a[1])
            if nndir is None:
                continue
            nnfeature_vector = (nndir[0] * math.sqrt(2), nndir[1] * math.sqrt(2))
            origin = base_coords[0]
            ok, inter = _cn_trimext_line_intersection(
                origin, feat_dir_vector, seg_a, nnfeature_vector
            )
            if not ok or inter is None:
                continue
            dist_inter = Point(origin).distance(Point(inter))
            intersects_segment = base_geom.intersects(segment_geom)
            if dist_inter <= buffer_radius:
                out.append((inter, dist_inter, code, intersects_segment))
        except Exception:
            continue
    return out


def _cn_trimext_query_neighbors(tree, geoms, idx_self, probe_geom, radius):
    from shapely.geometry import box

    minx, miny, maxx, maxy = probe_geom.bounds
    res = tree.query(box(minx - radius, miny - radius, maxx + radius, maxy + radius))
    out = []
    for hit in res:
        i = int(hit)
        if i == idx_self:
            continue
        g = geoms[i]
        if g is not None and not g.is_empty and g.geom_type == "LineString":
            out.append(g)
    return out


def _cn_trimext_sort(candidates, behaviour):
    if behaviour == "Extend":
        return sorted(candidates, key=lambda k: (k[2] * -1, not k[3], k[1]))
    if behaviour == "Trim":
        return sorted(candidates, key=lambda k: (k[2], not k[3], k[1]))
    return sorted(candidates, key=lambda k: (not k[3], k[1]))


@register
class CnEndpointsTrimExtendCapability(Capability):
    name = "cn_endpoints_trim_extend"
    description = (
        "Trim or extend line endpoints to the nearest non-parallel neighbour "
        "line within a buffer (filtered by angular and Hausdorff limits)."
    )

    def execute(
        self,
        gdf,
        buffer_trim=3.0,
        buffer_extend=5.0,
        preferred_behavior_for_starting_extremities=1,
        preferred_behavior_for_ending_extremities=0,
        hausdorff_distance_limit=5.0,
        angular_limit_of_parallel_geometries=15.0,
        explode_and_gather=False,
        entity_identification_fields=None,
        fix_geometries_before_processing=True,
        crs_meters="EPSG:3857",
        **_,
    ):
        import math

        from shapely import STRtree
        from shapely.geometry import LineString, Point

        if gdf is None or len(gdf) == 0:
            return gdf

        behaviors = ["Trim", "Extend", "None"]
        behaviour_start = behaviors[int(preferred_behavior_for_starting_extremities) % 3]
        behaviour_end = behaviors[int(preferred_behavior_for_ending_extremities) % 3]

        work, original_crs = _to_metric(gdf, crs_meters)
        if fix_geometries_before_processing:
            work = _fix_geometries(work)
        work = _to_singleparts(work)
        if explode_and_gather:
            work = _explode_lines(work)
        work = work.reset_index(drop=True)

        col = work.geometry.name
        geoms = list(work.geometry.values)
        order = sorted(
            range(len(geoms)),
            key=lambda i: geoms[i].length if geoms[i] is not None and not geoms[i].is_empty else 0.0,
        )

        def _extend_probe(p_near, p_end, dist):
            return _extend_end(p_near, p_end, dist)

        def _dir_vec(p_a, p_b):
            u = _cn_trimext_unit(p_a[0] - p_b[0], p_a[1] - p_b[1])
            if u is None:
                return (0.0, 0.0)
            return (u[0] * math.sqrt(2), u[1] * math.sqrt(2))

        def _rot_pi(v):
            return (-v[0], -v[1])

        def _process_end(idx, coords, geom, geom_length, tree, is_start, behaviour):
            polyline_end = coords[0] if is_start else coords[-1]
            polyline_near = coords[1] if is_start else coords[-2]
            endpoint_pt = Point(polyline_end)

            out_probe = _extend_probe(polyline_near, polyline_end, buffer_extend / 2.0)
            out_coords = [polyline_end, out_probe]
            if LineString([polyline_end, polyline_near]).length >= buffer_trim / 2.0:
                in_coords = [
                    polyline_end,
                    _cn_trimext_interpolate(polyline_end, polyline_near, buffer_trim / 2.0),
                ]
            else:
                in_coords = [polyline_end, polyline_near]

            vector = _dir_vec(polyline_end, polyline_near)
            # start uses vector as-is for trim, rotated for extend; end mirrors.
            trim_vec = vector if is_start else _rot_pi(vector)
            ext_vec = _rot_pi(vector) if is_start else vector

            candidates = []
            # --- TRIM regime ---
            candidates += _cn_trimext_endpoint_candidates(
                base_coords=in_coords,
                probe_point=in_coords[-1],
                feat_dir_vector=trim_vec,
                neighbor_geoms=_cn_trimext_query_neighbors(
                    tree, geoms, idx, Point(in_coords[-1]).buffer(buffer_trim / 2.0),
                    buffer_trim / 2.0,
                ),
                buffer_radius=buffer_trim / 2.0,
                code=-1,
                angular_limit=angular_limit_of_parallel_geometries,
                hausdorff_distance_limit=hausdorff_distance_limit,
            )

            # break_update_step: a neighbour endpoint already touches this point.
            break_update_step = False
            micro = endpoint_pt.buffer(0.001)
            for ng in _cn_trimext_query_neighbors(tree, geoms, idx, micro, 0.0):
                nc = list(ng.coords)
                if not nc:
                    continue
                if micro.intersects(Point(nc[0])) or micro.intersects(Point(nc[-1])):
                    break_update_step = True
                    break

            # --- EXTEND regime ---
            if not break_update_step:
                candidates += _cn_trimext_endpoint_candidates(
                    base_coords=out_coords,
                    probe_point=out_coords[-1],
                    feat_dir_vector=ext_vec,
                    neighbor_geoms=_cn_trimext_query_neighbors(
                        tree, geoms, idx, Point(out_coords[-1]).buffer(buffer_extend / 2.0),
                        buffer_extend / 2.0,
                    ),
                    buffer_radius=buffer_extend / 2.0,
                    code=1,
                    angular_limit=angular_limit_of_parallel_geometries,
                    hausdorff_distance_limit=hausdorff_distance_limit,
                )

            if candidates and not break_update_step:
                for ci in _cn_trimext_sort(candidates, behaviour):
                    new_coords = list(coords)
                    if is_start:
                        new_coords[0] = (ci[0][0], ci[0][1])
                    else:
                        new_coords[-1] = (ci[0][0], ci[0][1])
                    new_geom = LineString(new_coords)
                    if new_geom.is_empty:
                        continue
                    if (ci[2] == 1 and new_geom.length >= geom_length) or (
                        ci[2] == -1 and geom_length >= new_geom.length
                    ):
                        return new_coords, new_geom, new_geom.length
            return coords, geom, geom_length

        for idx in order:
            geom = geoms[idx]
            if geom is None or geom.is_empty or geom.geom_type != "LineString":
                continue
            coords = list(geom.coords)
            if len(coords) < 2:
                continue
            try:
                tree = STRtree(geoms)
                geom_length = geom.length
                if behaviour_start != "None":
                    coords, geom, geom_length = _process_end(
                        idx, coords, geom, geom_length, tree, True, behaviour_start
                    )
                    geoms[idx] = geom
                if behaviour_end != "None":
                    coords, geom, geom_length = _process_end(
                        idx, coords, geom, geom_length, tree, False, behaviour_end
                    )
                    geoms[idx] = geom
            except Exception:
                continue

        result = work.copy()
        result[col] = gpd.GeoSeries(geoms, index=result.index, crs=work.crs)
        if explode_and_gather:
            result = _gather_lines(result, entity_identification_fields)
        else:
            result = result.drop(columns=["__cn_eid__"], errors="ignore")
        return _restore(result, original_crs)

    def get_schema(self):
        return {
            "type": "object",
            "properties": {
                "buffer_trim": {"type": "number", "default": 3.0, "minimum": 0.0},
                "buffer_extend": {"type": "number", "default": 5.0, "minimum": 0.0},
                "preferred_behavior_for_starting_extremities": {
                    "type": "integer", "enum": [0, 1, 2], "default": 1,
                    "description": "0=Trim, 1=Extend, 2=None.",
                },
                "preferred_behavior_for_ending_extremities": {
                    "type": "integer", "enum": [0, 1, 2], "default": 0,
                    "description": "0=Trim, 1=Extend, 2=None.",
                },
                "hausdorff_distance_limit": {"type": "number", "default": 5.0, "minimum": 0.0},
                "angular_limit_of_parallel_geometries": {
                    "type": "number", "default": 15.0, "minimum": 0.0, "maximum": 180.0,
                },
                "explode_and_gather": {"type": "boolean", "default": False},
                "entity_identification_fields": {
                    "type": ["array", "null"], "items": {"type": "string"}, "default": None,
                },
                "fix_geometries_before_processing": {"type": "boolean", "default": True},
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
        }


# ===========================================================================
# 5. EndpointsSnapping
# ===========================================================================


_CN_ENDSNAP_BEHAVIORS = (
    "Nearest, Minimum angular variation",
    "Farest, Minimum angular variation",
    "Nearest, Maximum angular variation",
    "Farest, Maximum angular variation",
)


def _cn_endsnap_sort_key(behavior, same_direction):
    """Return a sort-key callable over ``(point, distance, angle, endpoint_type)``.

    ``distance`` is negated for "Farest", ``angle`` for "Maximum"; when
    same-direction is preferred, ``endpoint_type`` becomes the primary key.
    """
    far = behavior in (_CN_ENDSNAP_BEHAVIORS[1], _CN_ENDSNAP_BEHAVIORS[3])
    maxang = behavior in (_CN_ENDSNAP_BEHAVIORS[2], _CN_ENDSNAP_BEHAVIORS[3])

    def key(k):
        dist = -k[1] if far else k[1]
        ang = -k[2] if maxang else k[2]
        if same_direction:
            return (-k[3], dist, ang)
        return (dist, ang)

    return key


@register
class CnEndpointsSnappingCapability(Capability):
    name = "cn_endpoints_snapping"
    description = (
        "Snap line endpoints onto nearby endpoints of other lines within a "
        "buffer, with angular and Hausdorff preferences."
    )

    def execute(
        self,
        gdf,
        buffer_endpoints_snapping=5.0,
        preferred_behavior_for_starting_extremities=1,
        preferred_behavior_for_ending_extremities=0,
        hausdorff_distance_limit=5.0,
        min_angular_limit_of_parallel_geometries=0.0,
        max_angular_limit_of_parallel_geometries=180.0,
        prefers_same_geometry_direction=True,
        explode_and_gather=False,
        entity_identification_fields=None,
        fix_geometries_before_processing=True,
        crs_meters="EPSG:3857",
        **_,
    ):
        import shapely
        from shapely.geometry import LineString, Point

        if gdf is None or len(gdf) == 0:
            return gdf

        work, original_crs = _to_metric(gdf, crs_meters)
        if fix_geometries_before_processing:
            work = _fix_geometries(work)
        work = _to_singleparts(work)
        if explode_and_gather:
            work = _explode_lines(work)
        work = work.reset_index(drop=True)

        buffer_snap = float(buffer_endpoints_snapping)
        hausdorff_limit = float(hausdorff_distance_limit)
        min_ang = float(min_angular_limit_of_parallel_geometries)
        max_ang = float(max_angular_limit_of_parallel_geometries)
        behavior_start = _CN_ENDSNAP_BEHAVIORS[int(preferred_behavior_for_starting_extremities) % 4]
        behavior_end = _CN_ENDSNAP_BEHAVIORS[int(preferred_behavior_for_ending_extremities) % 4]
        same_dir = bool(prefers_same_geometry_direction)

        col = work.geometry.name
        geoms = list(work.geometry.values)
        n = len(geoms)
        order = sorted(
            (i for i in range(n) if geoms[i] is not None and not geoms[i].is_empty),
            key=lambda i: geoms[i].length,
        )

        def line_coords(g):
            return [tuple(c[:2]) for c in g.coords]

        for i in order:
            g = geoms[i]
            if g is None or g.is_empty or g.geom_type != "LineString":
                continue
            try:
                coords = line_coords(g)
                if len(coords) < 2:
                    continue
                other_idx = [
                    j for j in range(n)
                    if j != i and geoms[j] is not None and not geoms[j].is_empty
                    and geoms[j].geom_type == "LineString"
                ]
                if not other_idx:
                    continue
                other_geoms = [geoms[j] for j in other_idx]
                tree = shapely.STRtree(other_geoms)

                def snap_extremity(is_start, behavior):
                    nonlocal coords
                    if is_start:
                        ext_point, seg_p0, seg_p1 = coords[0], coords[0], coords[1]
                    else:
                        ext_point, seg_p0, seg_p1 = coords[-1], coords[-2], coords[-1]
                    ext_geom = Point(ext_point)
                    seg_geom = LineString([seg_p0, seg_p1])

                    ext_buffer = ext_geom.buffer(0.001)
                    for k in tree.query(ext_buffer.envelope):
                        oc = line_coords(other_geoms[int(k)])
                        if ext_buffer.intersects(Point(oc[0])) or ext_buffer.intersects(Point(oc[-1])):
                            return  # break_update_step

                    seg_angle = _seg_angle_deg(seg_p0, seg_p1)
                    geom_length = LineString(coords).length
                    candidates = []
                    for k in tree.query(ext_geom.buffer(buffer_snap)):
                        oc = line_coords(other_geoms[int(k)])
                        if len(oc) < 2:
                            continue
                        for which in ("start", "end"):
                            if which == "start":
                                vtx, neighbor, endpoint_type = oc[0], oc[1], -1
                            else:
                                vtx, neighbor, endpoint_type = oc[-1], oc[-2], 1
                            dist = ext_geom.distance(Point(vtx))
                            if dist > buffer_snap or geom_length <= dist:
                                continue
                            distant_angle = _seg_angle_deg(vtx, neighbor)
                            angular_variation = abs(seg_angle - distant_angle)
                            hausdorff = seg_geom.hausdorff_distance(LineString([vtx, neighbor]))
                            if (min_ang <= angular_variation <= max_ang) and hausdorff > hausdorff_limit:
                                candidates.append((vtx, dist, angular_variation, endpoint_type))

                    if not candidates:
                        return
                    candidates.sort(key=_cn_endsnap_sort_key(behavior, same_dir))
                    for cand in candidates:
                        new_coords = list(coords)
                        if is_start:
                            new_coords[0] = cand[0]
                        else:
                            new_coords[-1] = cand[0]
                        new_geom = LineString(new_coords)
                        if (not new_geom.is_empty) and new_geom.length >= cand[1]:
                            coords = new_coords
                            geoms[i] = new_geom
                            break

                snap_extremity(True, behavior_start)
                coords = line_coords(geoms[i])
                snap_extremity(False, behavior_end)
            except Exception:
                continue

        work[col] = gpd.GeoSeries(geoms, index=work.index, crs=work.crs)
        if explode_and_gather:
            work = _gather_lines(work, entity_identification_fields)
        else:
            work = work.drop(columns=["__cn_eid__"], errors="ignore")
        return _restore(work, original_crs)

    def get_schema(self):
        return {
            "type": "object",
            "properties": {
                "buffer_endpoints_snapping": {"type": "number", "default": 5.0, "minimum": 0.0},
                "preferred_behavior_for_starting_extremities": {
                    "type": "integer", "enum": [0, 1, 2, 3], "default": 1,
                    "description": "0=Nearest+MinAng, 1=Farest+MinAng, 2=Nearest+MaxAng, 3=Farest+MaxAng.",
                },
                "preferred_behavior_for_ending_extremities": {
                    "type": "integer", "enum": [0, 1, 2, 3], "default": 0,
                    "description": "0=Nearest+MinAng, 1=Farest+MinAng, 2=Nearest+MaxAng, 3=Farest+MaxAng.",
                },
                "hausdorff_distance_limit": {"type": "number", "default": 5.0, "minimum": 0.0},
                "min_angular_limit_of_parallel_geometries": {
                    "type": "number", "default": 0.0, "minimum": 0.0, "maximum": 180.0,
                },
                "max_angular_limit_of_parallel_geometries": {
                    "type": "number", "default": 180.0, "minimum": 0.0, "maximum": 180.0,
                },
                "prefers_same_geometry_direction": {"type": "boolean", "default": True},
                "explode_and_gather": {"type": "boolean", "default": False},
                "entity_identification_fields": {
                    "type": ["array", "null"], "items": {"type": "string"}, "default": None,
                },
                "fix_geometries_before_processing": {"type": "boolean", "default": True},
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
        }


# ===========================================================================
# 6. HubSnapping
# ===========================================================================


@register
class CnHubSnappingCapability(Capability):
    name = "cn_hub_snapping"
    description = (
        "Cluster nearby line vertices within a buffer and snap them onto a "
        "shared hub point (existing vertex or centroid)."
    )

    def execute(
        self,
        gdf,
        buffer_hub_snapping=1.5,
        hubpoint_must_be_an_existing_vertex=True,
        explode_and_gather=False,
        entity_identification_fields=None,
        fix_geometries_before_processing=True,
        maximize_endpoints=True,
        endpoints_threshold=2,
        crs_meters="EPSG:3857",
        **_,
    ):
        from shapely import STRtree
        from shapely.geometry import LineString, Point, Polygon
        from shapely.geometry import box as _box

        if gdf is None or len(gdf) == 0:
            return gdf

        work, original_crs = _to_metric(gdf, crs_meters)
        if fix_geometries_before_processing:
            work = _fix_geometries(work)
        work = _to_singleparts(work)
        if explode_and_gather:
            work = _explode_lines(work)
        work = work.reset_index(drop=True)
        col = work.geometry.name

        coords = {}
        for idx, geom in enumerate(work.geometry.values):
            if geom is not None and (not geom.is_empty) and isinstance(geom, LineString):
                coords[idx] = [tuple(c[:2]) for c in geom.coords]

        buf = float(buffer_hub_snapping)
        buf_sq = buf * buf

        def _process_endpoint(anchor_xy):
            ax, ay = anchor_xy
            live_idx = [i for i in coords if len(coords[i]) >= 2]
            tree_geoms = [LineString(coords[i]) for i in live_idx]
            if not tree_geoms:
                return
            tree = STRtree(tree_geoms)
            anchor_pt = Point(ax, ay)
            b = anchor_pt.buffer(buf).bounds
            nearest = []
            for pos in tree.query(_box(b[0], b[1], b[2], b[3])):
                fi = live_idx[int(pos)]
                fcoords = coords[fi]
                best_v = None
                best_d2 = None
                for vp, (vx, vy) in enumerate(fcoords):
                    d2 = (vx - ax) ** 2 + (vy - ay) ** 2
                    if best_d2 is None or d2 < best_d2:
                        best_d2 = d2
                        best_v = vp
                if best_d2 is None or best_d2 > buf_sq:
                    continue
                vx, vy = fcoords[best_v]
                dist = best_d2 ** 0.5
                if dist > buf:
                    continue
                is_endpoint = (best_v == 0) or (best_v == len(fcoords) - 1)
                nearest.append((fi, best_v, (vx, vy), dist, is_endpoint))

            nearest.sort(key=lambda x: x[3])
            n_endpoints = sum(1 for v in nearest if v[4])
            if maximize_endpoints:
                if (n_endpoints + 1) < endpoints_threshold and len(nearest) < 3:
                    return
            else:
                if len(nearest) < 3:
                    return

            ring = [v[2] for v in nearest]
            try:
                polygon = Polygon(ring)
            except Exception:
                return
            if polygon.is_empty or polygon.area == 0.0:
                return
            cx, cy = polygon.centroid.x, polygon.centroid.y
            nearest = sorted(nearest, key=lambda v: ((v[2][0] - cx) ** 2 + (v[2][1] - cy) ** 2))
            hub = nearest[0][2] if hubpoint_must_be_an_existing_vertex else (cx, cy)
            for fi, vp, _xy, _d, _ep in nearest:
                if fi in coords and 0 <= vp < len(coords[fi]):
                    coords[fi][vp] = hub

        order = sorted(coords.keys(), key=lambda i: LineString(coords[i]).length)
        for i in order:
            if i not in coords or len(coords[i]) < 2:
                continue
            _process_endpoint(coords[i][0])
            if i in coords and len(coords[i]) >= 2:
                _process_endpoint(coords[i][-1])

        new_geoms = list(work.geometry.values)
        for idx, c in coords.items():
            if len(c) >= 2:
                new_geoms[idx] = LineString(c)
        result = work.copy()
        result[col] = gpd.GeoSeries(new_geoms, index=result.index, crs=work.crs)

        if explode_and_gather:
            result = _gather_lines(result, entity_identification_fields)
        else:
            result = result.drop(columns=["__cn_eid__"], errors="ignore")
        return _restore(result, original_crs)

    def get_schema(self):
        return {
            "type": "object",
            "properties": {
                "buffer_hub_snapping": {"type": "number", "minimum": 0.0, "default": 1.5},
                "hubpoint_must_be_an_existing_vertex": {"type": "boolean", "default": True},
                "explode_and_gather": {"type": "boolean", "default": False},
                "entity_identification_fields": {
                    "type": ["array", "null"], "items": {"type": "string"}, "default": None,
                },
                "fix_geometries_before_processing": {"type": "boolean", "default": True},
                "maximize_endpoints": {"type": "boolean", "default": True},
                "endpoints_threshold": {"type": "integer", "minimum": 1, "default": 2},
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
        }


# ===========================================================================
# 7. SnapHubsPointsToLayer
# ===========================================================================


def _cn_snaphublayer_ref_points(ref_gdf):
    """Flatten the reference layer to candidate snap points (lines OR points)."""
    from shapely import get_coordinates

    pts = []
    for geom in ref_gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        gtype = geom.geom_type
        if gtype == "Point":
            pts.append((geom.x, geom.y))
        elif gtype == "MultiPoint":
            for p in geom.geoms:
                pts.append((p.x, p.y))
        else:
            for x, y in get_coordinates(geom):
                pts.append((float(x), float(y)))
    return pts


def _cn_snaphublayer_target(cluster_pts, hub_is_existing_vertex):
    from shapely.geometry import MultiPoint

    centroid = MultiPoint(cluster_pts).convex_hull.centroid
    if centroid.is_empty:
        return None
    if hub_is_existing_vertex:
        best = None
        best_d = None
        for px, py in cluster_pts:
            d = (px - centroid.x) ** 2 + (py - centroid.y) ** 2
            if best_d is None or d < best_d:
                best_d = d
                best = (px, py)
        return best
    return (centroid.x, centroid.y)


@register
class CnSnapHubsToLayerCapability(Capability):
    name = "cn_snap_hubs_to_layer"
    description = (
        "Snap line endpoint hubs onto vertices/points of a reference layer "
        "within a buffer."
    )

    def execute(
        self,
        gdf,
        ref_gdf=None,
        buffer_hub_snapping=1.5,
        hubpoint_must_be_an_existing_vertex=True,
        explode_and_gather=False,
        entity_identification_fields=None,
        fix_geometries_before_processing=True,
        crs_meters="EPSG:3857",
        **_,
    ):
        from shapely import STRtree
        from shapely.geometry import LineString, Point

        if ref_gdf is None or ref_gdf.empty:
            raise ValueError(
                "cn_snap_hubs_to_layer requires a reference layer (ref_layer)"
            )
        if gdf is None or len(gdf) == 0:
            return gdf

        work, original_crs = _to_metric(gdf, crs_meters)
        ref_work = (
            ref_gdf.to_crs(crs_meters)
            if ref_gdf.crs is not None and str(ref_gdf.crs) != str(crs_meters)
            else ref_gdf.copy()
        )
        if fix_geometries_before_processing:
            work = _fix_geometries(work)
            ref_work = _fix_geometries(ref_work)
        work = _to_singleparts(work)
        if explode_and_gather:
            work = _explode_lines(work)
        work = work.reset_index(drop=True)
        col = work.geometry.name

        ref_pts = _cn_snaphublayer_ref_points(ref_work)
        if ref_pts:
            ref_geoms = [Point(p) for p in ref_pts]
            tree = STRtree(ref_geoms)
        else:
            tree = None

        buf = float(buffer_hub_snapping)
        geoms = list(work.geometry.values)
        order = sorted(
            range(len(geoms)),
            key=lambda i: geoms[i].length if geoms[i] is not None and not geoms[i].is_empty else float("inf"),
        )

        if tree is not None:
            for i in order:
                geom = geoms[i]
                if geom is None or geom.is_empty or geom.geom_type != "LineString":
                    continue
                coords = list(geom.coords)
                if len(coords) < 2:
                    continue
                changed = False
                for idx in (0, -1):
                    hub = Point(coords[idx])
                    cluster = []
                    for ci in tree.query(hub.buffer(buf)):
                        rx, ry = ref_pts[int(ci)]
                        if ((rx - hub.x) ** 2 + (ry - hub.y) ** 2) ** 0.5 <= buf:
                            cluster.append((rx, ry))
                    if len(cluster) < 3:
                        continue
                    target = _cn_snaphublayer_target(cluster, hubpoint_must_be_an_existing_vertex)
                    if target is not None:
                        coords[idx] = target
                        changed = True
                if changed:
                    geoms[i] = LineString(coords)

        work[col] = gpd.GeoSeries(geoms, index=work.index, crs=work.crs)
        if explode_and_gather:
            work = _gather_lines(work, entity_identification_fields)
        else:
            work = work.drop(columns=["__cn_eid__"], errors="ignore")
        return _restore(work, original_crs)

    def get_schema(self):
        return {
            "type": "object",
            "properties": {
                "ref_layer": {"type": "string", "description": "Reference layer (lines or points)."},
                "buffer_hub_snapping": {"type": "number", "minimum": 0.0, "default": 1.5},
                "hubpoint_must_be_an_existing_vertex": {"type": "boolean", "default": True},
                "explode_and_gather": {"type": "boolean", "default": False},
                "entity_identification_fields": {
                    "type": ["array", "null"], "items": {"type": "string"}, "default": None,
                },
                "fix_geometries_before_processing": {"type": "boolean", "default": True},
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
        }


# ===========================================================================
# 8. SnapEndpointsToLayer
# ===========================================================================


_CN_SNAPENDLAYER_BEHAVIORS = _CN_ENDSNAP_BEHAVIORS


def _cn_snapendlayer_ref_endpoints(ref_gdf):
    """Reference snap targets as ``(anchor_xy, neighbor_xy)`` (lines + points)."""
    from shapely.geometry import LineString, MultiLineString, MultiPoint, Point

    targets = []

    def _from_line(line):
        c = list(line.coords)
        if len(c) < 2:
            if c:
                targets.append(((c[0][0], c[0][1]), (c[0][0], c[0][1])))
            return
        targets.append(((c[0][0], c[0][1]), (c[1][0], c[1][1])))
        targets.append(((c[-1][0], c[-1][1]), (c[-2][0], c[-2][1])))

    for geom in ref_gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        if isinstance(geom, LineString):
            _from_line(geom)
        elif isinstance(geom, MultiLineString):
            for part in geom.geoms:
                _from_line(part)
        elif isinstance(geom, Point):
            targets.append(((geom.x, geom.y), (geom.x, geom.y)))
        elif isinstance(geom, MultiPoint):
            for part in geom.geoms:
                targets.append(((part.x, part.y), (part.x, part.y)))
    return targets


@register
class CnSnapEndpointsToLayerCapability(Capability):
    name = "cn_snap_endpoints_to_layer"
    description = (
        "Snap line endpoints onto endpoints/points of a reference layer within "
        "a buffer, filtered by angular variation and Hausdorff distance."
    )

    def execute(
        self,
        gdf,
        ref_gdf=None,
        buffer_endpoints_snapping=5.0,
        preferred_behavior_for_starting_extremities=1,
        preferred_behavior_for_ending_extremities=0,
        hausdorff_distance_limit=5.0,
        min_angular_limit_of_parallel_geometries=0.0,
        max_angular_limit_of_parallel_geometries=180.0,
        prefers_same_geometry_direction=True,
        explode_and_gather=False,
        entity_identification_fields=None,
        fix_geometries_before_processing=True,
        crs_meters="EPSG:3857",
        **_,
    ):
        from shapely import STRtree
        from shapely.geometry import LineString, Point

        if ref_gdf is None or ref_gdf.empty:
            raise ValueError(
                "cn_snap_endpoints_to_layer requires a reference layer (ref_layer)"
            )
        if gdf is None or len(gdf) == 0:
            return gdf

        work, original_crs = _to_metric(gdf, crs_meters)
        ref_work = (
            ref_gdf.to_crs(crs_meters)
            if ref_gdf.crs is not None and str(ref_gdf.crs) != str(crs_meters)
            else ref_gdf.copy()
        )
        if fix_geometries_before_processing:
            work = _fix_geometries(work)
            ref_work = _fix_geometries(ref_work)
        work = _to_singleparts(work)
        if explode_and_gather:
            work = _explode_lines(work)
        work = work.reset_index(drop=True)
        col = work.geometry.name

        buffer_snap = float(buffer_endpoints_snapping)
        min_ang = float(min_angular_limit_of_parallel_geometries)
        max_ang = float(max_angular_limit_of_parallel_geometries)
        hausdorff_limit = float(hausdorff_distance_limit)
        same_dir = bool(prefers_same_geometry_direction)
        start_behavior = _CN_SNAPENDLAYER_BEHAVIORS[int(preferred_behavior_for_starting_extremities) % 4]
        end_behavior = _CN_SNAPENDLAYER_BEHAVIORS[int(preferred_behavior_for_ending_extremities) % 4]

        targets = _cn_snapendlayer_ref_endpoints(ref_work)
        if not targets:
            out = work.drop(columns=["__cn_eid__"], errors="ignore")
            return _restore(out, original_crs)
        anchors = [t[0] for t in targets]
        neighbors = [t[1] for t in targets]
        tree = STRtree([Point(a) for a in anchors])

        def snap_one(coords, is_start, behavior):
            if len(coords) < 2:
                return coords
            if is_start:
                end_pt, near_pt = coords[0], coords[1]
                local_seg = LineString([end_pt, near_pt])
            else:
                end_pt, near_pt = coords[-1], coords[-2]
                local_seg = LineString([near_pt, end_pt])
            end_geom = Point(end_pt)
            geom_len = LineString(coords).length
            local_angle = _seg_angle_deg(local_seg.coords[0], local_seg.coords[-1])

            micro = end_geom.buffer(0.001)
            for k in tree.query(micro):
                ax, ay = anchors[int(k)]
                if micro.intersects(Point(ax, ay)):
                    return coords  # break_update_step

            candidates = []
            for k in tree.query(end_geom.buffer(buffer_snap)):
                k = int(k)
                anchor = anchors[k]
                neighbor = neighbors[k]
                dist = end_geom.distance(Point(anchor))
                if not (dist <= buffer_snap and geom_len > dist):
                    continue
                dist_seg = LineString([anchor, neighbor])
                dist_angle = 0.0 if dist_seg.length == 0.0 else _seg_angle_deg(anchor, neighbor)
                angular_variation = abs(local_angle - dist_angle)
                hausdorff = local_seg.hausdorff_distance(dist_seg)
                if (min_ang <= angular_variation <= max_ang) and hausdorff > hausdorff_limit:
                    candidates.append((anchor, dist, angular_variation, 1))
            if not candidates:
                return coords
            candidates.sort(key=_cn_endsnap_sort_key(behavior, same_dir))
            for cand in candidates:
                trial = list(coords)
                if is_start:
                    trial[0] = (cand[0][0], cand[0][1])
                else:
                    trial[-1] = (cand[0][0], cand[0][1])
                trial_geom = LineString(trial)
                if not trial_geom.is_empty and trial_geom.length >= cand[1]:
                    return trial
            return coords

        geoms = list(work.geometry.values)
        order = sorted(
            range(len(geoms)),
            key=lambda i: geoms[i].length if geoms[i] is not None and not geoms[i].is_empty else float("inf"),
        )
        for i in order:
            geom = geoms[i]
            if geom is None or geom.is_empty or geom.geom_type != "LineString":
                continue
            coords = list(geom.coords)
            coords = snap_one(coords, True, start_behavior)
            coords = snap_one(coords, False, end_behavior)
            geoms[i] = LineString(coords)

        work[col] = gpd.GeoSeries(geoms, index=work.index, crs=work.crs)
        if explode_and_gather:
            work = _gather_lines(work, entity_identification_fields)
        else:
            work = work.drop(columns=["__cn_eid__"], errors="ignore")
        return _restore(work, original_crs)

    def get_schema(self):
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Reference layer (lines or points) supplying endpoint snap targets.",
                },
                "buffer_endpoints_snapping": {"type": "number", "default": 5.0, "minimum": 0.0},
                "preferred_behavior_for_starting_extremities": {
                    "type": "integer", "enum": [0, 1, 2, 3], "default": 1,
                },
                "preferred_behavior_for_ending_extremities": {
                    "type": "integer", "enum": [0, 1, 2, 3], "default": 0,
                },
                "hausdorff_distance_limit": {"type": "number", "default": 5.0, "minimum": 0.0},
                "min_angular_limit_of_parallel_geometries": {
                    "type": "number", "default": 0.0, "minimum": 0.0, "maximum": 180.0,
                },
                "max_angular_limit_of_parallel_geometries": {
                    "type": "number", "default": 180.0, "minimum": 0.0, "maximum": 180.0,
                },
                "prefers_same_geometry_direction": {"type": "boolean", "default": True},
                "explode_and_gather": {"type": "boolean", "default": False},
                "entity_identification_fields": {
                    "type": ["array", "null"], "items": {"type": "string"}, "default": None,
                },
                "fix_geometries_before_processing": {"type": "boolean", "default": True},
                "crs_meters": {"type": "string", "default": "EPSG:3857"},
            },
        }


__all__ = [
    "CnCalculateDbscanCapability",
    "CnConsolidateWithDbscanCapability",
    "CnMakeIntersectionsVertexesCapability",
    "CnEndpointsTrimExtendCapability",
    "CnEndpointsSnappingCapability",
    "CnHubSnappingCapability",
    "CnSnapHubsToLayerCapability",
    "CnSnapEndpointsToLayerCapability",
]
