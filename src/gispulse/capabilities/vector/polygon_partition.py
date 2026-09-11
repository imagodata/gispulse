"""Partition polygon interiors with source lines, retaining topology diagnostics."""

from __future__ import annotations

import hashlib
import math

import geopandas as gpd
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, box
from shapely.ops import polygonize_full, unary_union


def _lines(geometry):
    if isinstance(geometry, LineString):
        return [geometry] if geometry.length > 0 else []
    return [line for part in getattr(geometry, "geoms", ()) for line in _lines(part)]


def partition_polygons(
    polygons: gpd.GeoDataFrame,
    boundaries: gpd.GeoDataFrame,
    *,
    polygon_id: str,
    boundary_id: str,
    coverage_bounds: tuple[float, float, float, float],
    area_tolerance_m2: float,
    length_tolerance_m: float,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Build candidate faces only inside fully acquired polygon envelopes.

    Neither snapping nor surface/price/ground inference is performed. The metre
    tolerances report numerical residuals; they do not change input geometries.
    Unresolved linework is retained in a secondary geometry column of diagnostics.
    """
    if (
        polygons.crs is None
        or boundaries.crs != polygons.crs
        or not polygons.crs.is_projected
        or any(a.unit_conversion_factor != 1 for a in polygons.crs.axis_info[:2])
    ):
        raise ValueError("PARTITION_CRS_INVALID: identical projected metre CRS required")
    if (
        len(coverage_bounds) != 4
        or not all(math.isfinite(v) for v in coverage_bounds)
        or coverage_bounds[0] >= coverage_bounds[2]
        or coverage_bounds[1] >= coverage_bounds[3]
    ):
        raise ValueError("PARTITION_COVERAGE_INVALID: ordered finite acquisition bbox required")
    if any(not math.isfinite(v) or v < 0 for v in (area_tolerance_m2, length_tolerance_m)):
        raise ValueError("PARTITION_TOLERANCE_INVALID: nonnegative finite tolerances required")
    for frame, identity, allowed in [
        (polygons, polygon_id, (Polygon, MultiPolygon)),
        (boundaries, boundary_id, (LineString, MultiLineString)),
    ]:
        if (
            identity not in frame
            or frame[identity].isna().any()
            or frame[identity].astype(str).duplicated().any()
            or (frame[identity].astype(str).str.strip() == "").any()
        ):
            raise ValueError("PARTITION_ID_INVALID: unique nonempty source IDs required")
        if any(not isinstance(g, allowed) or g.is_empty or not g.is_valid for g in frame.geometry):
            raise ValueError(
                "PARTITION_GEOMETRY_INVALID: repair source geometries explicitly before partitioning"
            )
    coverage = box(*coverage_bounds)
    tree = STRtree(boundaries.geometry.to_list())
    faces, diagnostics = [], []
    for idx in sorted(range(len(polygons)), key=lambda i: str(polygons.iloc[i][polygon_id])):
        polygon = polygons.geometry.iloc[idx]
        identity = str(polygons.iloc[idx][polygon_id])
        if not coverage.covers(polygon):
            diagnostics.append(
                {
                    "polygon_id": identity,
                    "status": "outside_acquisition_coverage",
                    "face_count": 0,
                    "area_error_m2": None,
                    "cuts_m": None,
                    "dangles_m": None,
                    "invalid_m": None,
                    "unresolved_geometry": None,
                    "geometry": polygon,
                }
            )
            continue
        source_lines = []
        for j in sorted(tree.query(polygon, predicate="intersects")):
            source_lines.extend(_lines(boundaries.geometry.iloc[int(j)].intersection(polygon)))
        network = unary_union([polygon.boundary, *source_lines])
        collection, cuts, dangles, invalid = polygonize_full(_lines(network))
        parts = sorted(
            (p for p in collection.geoms if polygon.covers(p.representative_point())),
            key=lambda p: p.normalize().wkb,
        )
        # Area conservation is necessary, never sufficient to certify subdivision.
        area_error = abs(sum(p.area for p in parts) - polygon.area)
        status = (
            "area_mismatch"
            if area_error > area_tolerance_m2
            else "unresolved_lines"
            if max(cuts.length, dangles.length, invalid.length) > length_tolerance_m
            else "unsplit"
            if len(parts) <= (len(polygon.geoms) if isinstance(polygon, MultiPolygon) else 1)
            else "partitioned"
        )
        for part in parts:
            faces.append(
                {
                    "polygon_id": identity,
                    "face_id": identity + ":" + hashlib.sha256(part.normalize().wkb).hexdigest(),
                    "topology_status": status,
                    "geometry": part,
                }
            )
        diagnostics.append(
            {
                "polygon_id": identity,
                "status": status,
                "face_count": len(parts),
                "area_error_m2": area_error,
                "cuts_m": cuts.length,
                "dangles_m": dangles.length,
                "invalid_m": invalid.length,
                "unresolved_geometry": unary_union([cuts, dangles, invalid]),
                "geometry": polygon,
            }
        )
    result = gpd.GeoDataFrame(
        faces,
        columns=["polygon_id", "face_id", "topology_status", "geometry"],
        geometry="geometry",
        crs=polygons.crs,
    )
    report = gpd.GeoDataFrame(
        diagnostics,
        columns=[
            "polygon_id",
            "status",
            "face_count",
            "area_error_m2",
            "cuts_m",
            "dangles_m",
            "invalid_m",
            "unresolved_geometry",
            "geometry",
        ],
        geometry="geometry",
        crs=polygons.crs,
    )
    report["unresolved_geometry"] = gpd.GeoSeries(report.unresolved_geometry, crs=polygons.crs)
    return result, report
