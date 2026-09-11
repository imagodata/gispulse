"""Classify reconstructed GRB road faces from explicit upstream attributes.

Only documented source codes decide a class: Wegsegment ``STATUS``/``VERH`` for
the carriageway, WGO ``TYPE`` for the internal functional boundaries. Geometry
alone never identifies a side, so a face without explicit evidence stays
``unmapped`` with a named reason. Tolerances compare numbers; no geometry is
snapped, buffered or repaired, and inputs are never mutated.

Official semantics (Digitaal Vlaanderen, objectenhandboek GRB):

- WGO is a **line** layer. ``TYPE`` qualifies the boundary itself, not the area
  it delimits: 1 = Wcz, edge of the slow-user circulation zone, always
  physically separated; 2 = Woz, edge of the unpaved outer part of the road, a
  soft shoulder and never a sidewalk; 3 = Wrb, edge of the flat paved part
  reserved for motor traffic. Capture follows the Wcz > Wrb > Woz priority, so
  a missing type is not evidence that the corresponding zone is absent.
  Crucially, a Wcz line borders *two* faces (the slow-user zone and the
  carriageway beside it), so a face's own Wcz share never identifies which of
  the two it is: only adjacency to a face already proven ``carriageway_paved``
  by axis evidence turns a dominant Wcz share into a ``sidewalk`` verdict.
- Wegsegment ``VERH``: 1 paved, 2 unpaved, 12 mixed, -8 unknown, -9 not
  applicable. ``STATUS``: 1 permit requested, 2 permit granted, 3 under
  construction, 4 in service, 5 out of service, -8 unknown.

https://www.vlaanderen.be/digitaal-vlaanderen/onze-diensten-en-platformen/basiskaart-vlaanderen-grb/objectenhandboek-basiskaart-vlaanderen-grb/wegopdeling-wgo
"""

from __future__ import annotations

import math

import geopandas as gpd
import pandas as pd
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import unary_union

_WCZ, _WOZ, _WRB = 1, 2, 3  # WGO TYPE
_IN_SERVICE = 4  # Wegsegment STATUS
_PAVED = frozenset({1, 12})  # Wegsegment VERH: paved, and mixed paved/unbound
# partition_polygons reports "unsplit" when area is conserved, no cut/dangle/
# invalid residual exceeds tolerance, and there was simply nothing to split —
# a WBN corridor with no internal WGO line is entirely and unambiguously one
# face. That is a resolved topology, not an unresolved one.
_RESOLVED_STATUSES = ("partitioned", "unsplit")
_CLASSES = ("carriageway_paved", "sidewalk", "unmapped")
_TEXT_COLUMNS = ("functional_class", "classification_reason", "classification_evidence")
_RATIO_COLUMNS = (
    "axis_coverage_ratio",
    "wcz_boundary_ratio",
    "wrb_boundary_ratio",
    "woz_boundary_ratio",
)


def _code(value) -> int | None:
    """Normalise a raw GRB integer code; anything unusable stays unknown."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if math.isfinite(number) and number == int(number) else None


def _length_inside(line, region, excluded_boundary) -> float:
    """Length of ``line`` inside ``region``, excluding runs collinear with ``excluded_boundary``.

    ``excluded_boundary`` is the same geometry for both the numerator (one
    face) and the denominator (the whole corridor) of the axis coverage
    ratio, so a run an axis spends collinear with *any* corridor boundary —
    outer or shared between two faces — is dropped from both consistently.
    Dropping it only from the numerator would deflate the ratio of the face
    the axis briefly hugs on its way to a genuine, unambiguous crossing.
    """
    inside = line.intersection(region)
    return 0.0 if inside.is_empty else inside.difference(excluded_boundary).length


def _segments(geometry):
    for part in getattr(geometry, "geoms", (geometry,)):
        coordinates = list(part.coords)
        for start, end in zip(coordinates, coordinates[1:]):
            segment = LineString([start, end])
            if segment.length > 0:
                yield segment


def _boundary_shares(face, tagged: dict[int, tuple[STRtree, list]], tolerance: float):
    """Share of the face perimeter carried by each WGO type, and the touching IDs.

    Faces come from a noded polygonization, so a boundary segment originates
    whole from one source line: comparing its midpoint distance is enough and
    keeps the tolerance a numerical comparison rather than a snap.
    """
    segments = list(_segments(face.boundary))
    ratios = dict.fromkeys(tagged, 0.0)
    touching_ids: dict[int, list[str]] = {code: [] for code in tagged}
    perimeter = sum(segment.length for segment in segments)
    if perimeter <= 0:
        return ratios, touching_ids
    midpoints = [segment.interpolate(0.5, normalized=True) for segment in segments]
    for type_code, (tree, ids) in tagged.items():
        if not ids:
            continue
        segment_hits, line_hits = tree.query(midpoints, predicate="dwithin", distance=tolerance)
        ratios[type_code] = (
            sum(segments[i].length for i in sorted(set(segment_hits.tolist()))) / perimeter
        )
        touching_ids[type_code] = sorted({ids[i] for i in sorted(set(line_hits.tolist()))})
    return ratios, touching_ids


def _format_evidence(matches) -> str:
    """Pair each retained axis with its own VERH, e.g. ``101:VERH=1,102:VERH=12``."""
    return ",".join(
        f"{axis_id}:VERH={'unknown' if verh is None else verh}"
        for _, axis_id, verh in sorted(matches, key=lambda match: match[1])
    )


def classify_grb_faces(
    faces: gpd.GeoDataFrame,
    boundaries: gpd.GeoDataFrame,
    axes: gpd.GeoDataFrame,
    *,
    axis_coverage_ratio_min: float,
    axis_min_extent_m: float,
    wcz_ratio_min: float,
    wrb_ratio_max: float,
    boundary_tolerance_m: float,
    length_tolerance_m: float,
    face_id: str = "face_id",
    polygon_id: str = "polygon_id",
    status_field: str = "topology_status",
    boundary_id: str = "OIDN",
    boundary_type_field: str = "TYPE",
    axis_id: str = "WS_OIDN",
    axis_surface_field: str = "VERH",
    axis_status_field: str = "STATUS",
) -> tuple[gpd.GeoDataFrame, dict]:
    """Label candidate faces from upstream GRB evidence, defaulting to ``unmapped``.

    ``faces`` must be a complete ``partition_polygons`` output: the parent
    corridor is rebuilt as the union of its resolved (``partitioned`` or
    ``unsplit``) faces, and classification runs in two passes over it.

    1. A face with an in-service (``STATUS=4``) axis whose length inside the
       corridor is at least ``axis_min_extent_m`` and whose share of that
       length falls inside this face is at least ``axis_coverage_ratio_min``
       becomes ``carriageway_paved`` when every such axis is paved (``VERH``
       1 or 12), stays ``unmapped`` when every one is not, and stays
       ``unmapped`` with a distinct reason when they disagree.
    2. A face pass 1 left undecided is a ``sidewalk`` candidate only if it
       shares a boundary with a face pass 1 anchored as ``carriageway_paved``
       in the *same* corridor: a face's own Wcz share never proves which side
       of a Wcz line it is on (a Wcz line borders both the slow-user zone and
       the carriageway beside it), only proximity to a proven carriageway
       does. Faces in a corridor with no such anchor stay ``unmapped``.

    Every threshold is an explicit argument, and each face carries the reason
    and the ratios behind its class.
    """
    if (
        faces.crs is None
        or boundaries.crs != faces.crs
        or axes.crs != faces.crs
        or not faces.crs.is_projected
        or any(a.unit_conversion_factor != 1 for a in faces.crs.axis_info[:2])
    ):
        raise ValueError("GRB_CLASSIFY_CRS_INVALID: identical projected metre CRS required")
    if any(
        not math.isfinite(v) or not 0.0 <= v <= 1.0
        for v in (axis_coverage_ratio_min, wcz_ratio_min, wrb_ratio_max)
    ):
        raise ValueError("GRB_CLASSIFY_RATIO_INVALID: coverage ratios required within [0, 1]")
    if any(
        not math.isfinite(v) or v < 0
        for v in (boundary_tolerance_m, length_tolerance_m, axis_min_extent_m)
    ):
        raise ValueError("GRB_CLASSIFY_TOLERANCE_INVALID: nonnegative finite tolerances required")
    for frame, fields, allowed in [
        (faces, (face_id, polygon_id, status_field), (Polygon, MultiPolygon)),
        (boundaries, (boundary_id, boundary_type_field), (LineString, MultiLineString)),
        (axes, (axis_id, axis_surface_field, axis_status_field), (LineString, MultiLineString)),
    ]:
        missing = [field for field in fields if field not in frame]
        if missing:
            raise ValueError(f"GRB_CLASSIFY_FIELD_MISSING: {missing}")
        if any(not isinstance(g, allowed) or g.is_empty or not g.is_valid for g in frame.geometry):
            raise ValueError(
                "GRB_CLASSIFY_GEOMETRY_INVALID: repair source geometries explicitly before classifying"
            )
    # face_id must be unique (it identifies the output row); polygon_id groups
    # faces into corridors and repeats by design. axis_id (WS_OIDN) is a
    # Wegenregister reference, not a GRB OIDN, and is not required to be
    # unique per geometric segment: it is used only for evidence and
    # deterministic ordering, never as a lookup key.
    for frame, identity in ((faces, face_id),):
        column = frame[identity]
        if (
            column.isna().any()
            or (column.astype(str).str.strip() == "").any()
            or column.astype(str).duplicated().any()
        ):
            raise ValueError("GRB_CLASSIFY_ID_INVALID: unique nonempty face IDs required")
    for frame, identity in ((faces, polygon_id), (axes, axis_id)):
        column = frame[identity]
        if column.isna().any() or (column.astype(str).str.strip() == "").any():
            raise ValueError("GRB_CLASSIFY_ID_INVALID: nonempty IDs required")

    # Positional numpy masks throughout: an empty list would select columns, and a
    # caller may legitimately hand over a frame with duplicated index labels.
    in_service = axes[(axes[axis_status_field].map(_code) == _IN_SERVICE).to_numpy()]
    axis_geometries = in_service.geometry.to_list()
    axis_tree = STRtree(axis_geometries)
    axis_ids = [str(v) for v in in_service[axis_id]]
    axis_surfaces = [_code(v) for v in in_service[axis_surface_field]]

    boundary_ids_all = [str(v) for v in boundaries[boundary_id]]
    boundary_codes = boundaries[boundary_type_field].map(_code)
    tagged_boundaries: dict[int, tuple[STRtree, list]] = {}
    for type_code in (_WCZ, _WRB, _WOZ):
        mask = (boundary_codes == type_code).to_numpy()
        tagged_boundaries[type_code] = (
            STRtree(boundaries.geometry[mask].to_list()),
            [boundary_ids_all[i] for i, hit in enumerate(mask) if hit],
        )

    geometries = faces.geometry.to_list()
    statuses = [str(v) for v in faces[status_field]]
    parents = [str(v) for v in faces[polygon_id]]

    grouped: dict[str, list] = {}
    for position, status in enumerate(statuses):
        if status in _RESOLVED_STATUSES:
            grouped.setdefault(parents[position], []).append(geometries[position])
    corridors = {
        key: (unary_union(parts), unary_union([part.boundary for part in parts]))
        for key, parts in grouped.items()
    }

    # Pass 1: axis evidence only. A resolved face this pass cannot settle (no
    # in-service axis covers enough of it) is left as None, to be judged in
    # pass 2 against faces this pass anchored as carriageway_paved in the same
    # corridor.
    pass1: list[dict | None] = [None] * len(geometries)
    for position, status in enumerate(statuses):
        if status not in _RESOLVED_STATUSES:
            continue
        face = geometries[position]
        corridor, corridor_boundary = corridors[parents[position]]
        candidates = []
        for index in axis_tree.query(face, predicate="intersects"):
            line = axis_geometries[int(index)]
            extent = _length_inside(line, corridor, corridor_boundary)
            if extent <= length_tolerance_m or extent < axis_min_extent_m:
                continue
            share = _length_inside(line, face, corridor_boundary) / extent
            candidates.append((share, axis_ids[int(index)], axis_surfaces[int(index)]))
        matches = [c for c in candidates if c[0] >= axis_coverage_ratio_min]
        if not matches:
            continue
        paved = [m for m in matches if m[2] in _PAVED]
        unpaved = [m for m in matches if m[2] not in _PAVED]
        if paved and unpaved:
            pass1[position] = {
                "functional_class": "unmapped",
                "classification_reason": "contradictory_axis_surface",
                "classification_evidence": _format_evidence(paved + unpaved),
                "axis_coverage_ratio": max(m[0] for m in matches),
            }
        elif paved:
            pass1[position] = {
                "functional_class": "carriageway_paved",
                "classification_reason": "in_service_paved_axis",
                "classification_evidence": _format_evidence(paved),
                "axis_coverage_ratio": max(m[0] for m in paved),
            }
        else:
            pass1[position] = {
                "functional_class": "unmapped",
                "classification_reason": "axis_surface_not_paved",
                "classification_evidence": _format_evidence(unpaved),
                "axis_coverage_ratio": max(m[0] for m in unpaved),
            }

    anchors: dict[str, list] = {}
    for position, verdict in enumerate(pass1):
        if verdict and verdict["functional_class"] == "carriageway_paved":
            anchors.setdefault(parents[position], []).append(geometries[position])
    anchor_boundary = {
        key: unary_union([g.boundary for g in geoms]) for key, geoms in anchors.items()
    }

    records = []
    for position, status in enumerate(statuses):
        if status not in _RESOLVED_STATUSES:
            # Area conservation alone never certifies a subdivision, so unresolved
            # topology stops here: neither axes nor boundaries are consulted.
            records.append(
                {
                    "functional_class": "unmapped",
                    "classification_reason": "unresolved_topology:" + status,
                    "classification_evidence": "",
                    **dict.fromkeys(_RATIO_COLUMNS, math.nan),
                }
            )
            continue
        face = geometries[position]
        ratios, touching = _boundary_shares(face, tagged_boundaries, boundary_tolerance_m)
        if pass1[position] is not None:
            record = dict(pass1[position])
            record.update(
                wcz_boundary_ratio=ratios[_WCZ],
                wrb_boundary_ratio=ratios[_WRB],
                woz_boundary_ratio=ratios[_WOZ],
            )
            records.append(record)
            continue
        anchor = anchor_boundary.get(parents[position])
        anchored = (
            anchor is not None
            and face.boundary.intersects(anchor)
            and face.boundary.intersection(anchor).length > boundary_tolerance_m
        )
        evidence = ""
        if not anchored:
            functional_class, reason = "unmapped", "no_carriageway_anchor_in_corridor"
        elif ratios[_WCZ] < wcz_ratio_min:
            # Woz is an unpaved shoulder, never a sidewalk; it is reported, never mapped.
            functional_class = "unmapped"
            reason = (
                "woz_only_not_sidewalk"
                if ratios[_WOZ] >= wcz_ratio_min
                else "no_dominant_wcz_boundary"
            )
        elif ratios[_WRB] > wrb_ratio_max or ratios[_WRB] >= ratios[_WCZ]:
            functional_class, reason = "unmapped", "wrb_boundary_dominant"
        else:
            functional_class, reason = "sidewalk", "dominant_wcz_boundary"
            evidence = ",".join(f"{boundary_id}={oidn}" for oidn in touching[_WCZ])
        records.append(
            {
                "functional_class": functional_class,
                "classification_reason": reason,
                "classification_evidence": evidence,
                "axis_coverage_ratio": 0.0,
                "wcz_boundary_ratio": ratios[_WCZ],
                "wrb_boundary_ratio": ratios[_WRB],
                "woz_boundary_ratio": ratios[_WOZ],
            }
        )

    assigned = pd.DataFrame(records).reindex(columns=list(_TEXT_COLUMNS + _RATIO_COLUMNS))
    result = faces.copy()
    for column in _TEXT_COLUMNS:
        result[column] = assigned[column].astype(str).to_numpy()
    for column in _RATIO_COLUMNS:
        result[column] = assigned[column].astype(float).to_numpy()
    counts = result.functional_class.value_counts().to_dict()
    areas = result.geometry.area.groupby(result.functional_class).sum().to_dict()
    total_area = float(result.geometry.area.sum())
    report = {
        "input_faces": int(len(result)),
        "in_service_axes": int(len(in_service)),
        "classes": {name: int(counts.get(name, 0)) for name in _CLASSES},
        "class_ratios": {
            name: (int(counts.get(name, 0)) / len(result) if len(result) else 0.0)
            for name in _CLASSES
        },
        "class_area_m2": {name: float(areas.get(name, 0.0)) for name in _CLASSES},
        "area_ratios": {
            name: (float(areas.get(name, 0.0)) / total_area if total_area > 0 else 0.0)
            for name in _CLASSES
        },
        "reasons": {
            str(label): int(count)
            for label, count in sorted(
                result.classification_reason.value_counts().to_dict().items()
            )
        },
        "thresholds": {
            "axis_coverage_ratio_min": float(axis_coverage_ratio_min),
            "axis_min_extent_m": float(axis_min_extent_m),
            "wcz_ratio_min": float(wcz_ratio_min),
            "wrb_ratio_max": float(wrb_ratio_max),
            "boundary_tolerance_m": float(boundary_tolerance_m),
            "length_tolerance_m": float(length_tolerance_m),
        },
        "inference": False,
    }
    return result, report
