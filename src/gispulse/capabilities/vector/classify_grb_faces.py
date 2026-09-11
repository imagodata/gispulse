"""Classify reconstructed GRB road faces from explicit upstream attributes.

Only documented source codes decide a class: Wegsegment ``STATUS``/``VERH``/
``MORF`` for the carriageway. Geometry alone never identifies a side, so a
face without explicit axis evidence stays ``unmapped`` with a named reason.
Tolerances compare numbers; no geometry is snapped, buffered or repaired, and
inputs are never mutated.

Official semantics (Digitaal Vlaanderen, objectenhandboek GRB):

- WGO is a **line** layer. ``TYPE`` qualifies the boundary itself, not the
  area it delimits: 1 = Wcz, edge of the slow-user circulation zone, always
  physically separated; 2 = Woz, edge of the unpaved outer part of the road,
  a soft shoulder; 3 = Wrb, edge of the flat paved part reserved for motor
  traffic. Capture follows the Wcz > Wrb > Woz priority, so a missing type is
  not evidence that the corresponding zone is absent. Two attempts at
  deriving a ``sidewalk`` verdict from a face's own WGO boundary composition
  — first from its raw Wcz perimeter share, then from that share plus
  adjacency to a face already proven ``carriageway_paved`` in the same
  corridor — were each found, by adversarial review, to be invertible: a Wcz
  line can separate two portions of the *same* carriageway (for instance
  either side of a raised pedestrian crossing) just as it can separate a
  carriageway from the sidewalk beside it, and nothing in WGO/Wegsegment
  alone tells the two apart. This module therefore does **not** produce
  ``sidewalk``: WGO boundary shares (``wcz_boundary_ratio`` etc.) are still
  measured and reported per face for diagnosis, but a face without axis
  evidence stays ``unmapped`` regardless of how much Wcz borders it.
- Wegsegment ``VERH``: 1 paved, 2 unpaved, 12 mixed, -8 unknown, -9 not
  applicable. An axis with an unknown/not-applicable code is neither paved
  nor unpaved evidence — it is simply insufficient, and never contradicts a
  genuinely paved or unpaved axis covering the same face.
  ``STATUS``: 1 permit requested, 2 permit granted, 3 under construction,
  4 in service, 5 out of service, -8 unknown.
- Wegsegment ``MORF`` (morfologische wegklasse) classifies what the axis
  physically *is*, independently of ``VERH``/``STATUS``: a `voetgangerszone`
  (113, pedestrian zone) or a `wandel- en/of fietsweg niet toegankelijk voor
  andere voertuigen` (114, walking/cycling path, explicitly closed to other
  vehicles) can be ``STATUS=4`` and ``VERH=1`` — in service and paved — while
  being just as officially not a carriageway as an unpaved axis is. A first
  cut of this module read only ``VERH``/``STATUS`` and, on real Gand data,
  labelled several `voetgangerszone`/`wandel- of fietsweg` faces
  ``carriageway_paved`` — the same failure mode the whole chantier exists to
  remove, just moved to a different attribute. ``MORF`` is therefore now
  required evidence, not merely descriptive: only axes whose ``MORF`` is in
  the small set of codes officially describing a motor-traffic way or
  junction (101–112: motorway, dual/single carriageway, roundabout, special
  traffic situation, traffic square, on/off-ramps, parallel/service road,
  parking/service entrance) count toward ``carriageway_paved``. Explicitly
  excluded: 113/114 (pedestrian/cycling, not for other vehicles), 116
  (tram-only), 120 (dienstweg — an unpaved-by-default service track, not a
  public carriageway), 125 (aardeweg — an earthen track, unpaved by
  definition), 130 (veer — a ferry crossing, not a road), any other or
  unknown code.

https://www.vlaanderen.be/digitaal-vlaanderen/onze-diensten-en-platformen/basiskaart-vlaanderen-grb/objectenhandboek-basiskaart-vlaanderen-grb/wegopdeling-wgo
https://www.vlaanderen.be/digitaal-vlaanderen/onze-diensten-en-platformen/basiskaart-vlaanderen-grb/objectenhandboek-basiskaart-vlaanderen-grb/wegsegment-wegsegment
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
_UNPAVED = frozenset({2})  # Wegsegment VERH: unpaved. -8/-9/other are unknown, not unpaved.
# Wegsegment MORF codes officially describing a motor-traffic way or junction.
# Excludes 113/114 (pedestrian/cycling, explicitly closed to other vehicles),
# 116 (tram-only), 120 (service track, unpaved by default), 125 (earthen
# track), 130 (ferry crossing) and any code outside this documented range.
_MOTORIZED_MORF = frozenset(range(101, 113))
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


def _interior_length(line, polygon) -> float:
    """Length of ``line`` inside ``polygon``, excluding runs collinear with its boundary.

    A stretch lying on the boundary belongs to both adjacent faces, so it
    proves membership of neither.

    Deliberate asymmetry, kept after adversarial review: the denominator
    (measured against the whole corridor) excludes only the corridor's
    *outer* boundary, while the numerator (measured against one face) also
    excludes that face's *internal* boundaries with its neighbours. An axis
    that runs mostly collinear with an internal cut before a short genuine
    crossing therefore reports a lower ratio for the face it crosses into —
    a false negative (understated evidence), never a promotion. A fully
    symmetric exclusion was tried and rejected: it shrinks the denominator by
    the same collinear stretch, letting a short graze past an
    ``axis_min_extent_m`` floor measured against that same shrunk value and
    reach ratio 1.0 — a false positive, which this module treats as the
    strictly worse failure mode.

    This exact (unbuffered) difference means an axis that runs a millimetre
    inside a boundary, rather than exactly on it, is not excluded at all —
    a known, documented gap (no real GRB axis in the validated Gand bundle
    triggers it; see the plugin README).
    """
    inside = line.intersection(polygon)
    return 0.0 if inside.is_empty else inside.difference(polygon.boundary).length


def _segments(geometry):
    for part in getattr(geometry, "geoms", (geometry,)):
        coordinates = list(part.coords)
        for start, end in zip(coordinates, coordinates[1:]):
            segment = LineString([start, end])
            if segment.length > 0:
                yield segment


def _boundary_ratios(face, trees: dict[int, STRtree], tolerance: float) -> dict[int, float]:
    """Share of the face perimeter carried by each WGO type — diagnosis only.

    Faces come from a noded polygonization, so a boundary segment originates
    whole from one source line: comparing its midpoint distance is enough and
    keeps the tolerance a numerical comparison rather than a snap. These
    ratios are reported for every resolved face but never decide a class: see
    the module docstring for why a bare WGO-boundary composition cannot tell
    a sidewalk from a carriageway split by an internal Wcz line.
    """
    segments = list(_segments(face.boundary))
    ratios = dict.fromkeys(trees, 0.0)
    perimeter = sum(segment.length for segment in segments)
    if perimeter <= 0:
        return ratios
    midpoints = [segment.interpolate(0.5, normalized=True) for segment in segments]
    for type_code, tree in trees.items():
        hits = set(tree.query(midpoints, predicate="dwithin", distance=tolerance)[0].tolist())
        ratios[type_code] = sum(segments[index].length for index in hits) / perimeter
    return ratios


def _format_evidence(matches, surface_field: str) -> str:
    """Pair each retained axis with its own surface code, deduplicated and sorted."""
    pairs = sorted(
        {
            (axis_id, "unknown" if code is None else str(code))
            for _, axis_id, code, _morf, _length in matches
        }
    )
    return ",".join(f"{axis_id}:{surface_field}={code}" for axis_id, code in pairs)


def classify_grb_faces(
    faces: gpd.GeoDataFrame,
    boundaries: gpd.GeoDataFrame,
    axes: gpd.GeoDataFrame,
    *,
    axis_coverage_ratio_min: float,
    axis_min_extent_m: float,
    unsplit_max_width_m: float,
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
    axis_morphology_field: str = "MORF",
) -> tuple[gpd.GeoDataFrame, dict]:
    """Label candidate faces from upstream GRB axis evidence, defaulting to ``unmapped``.

    ``faces`` must be a complete ``partition_polygons`` output: the parent
    corridor is rebuilt as the union of its resolved (``partitioned`` or
    ``unsplit``) faces. For each resolved face, every Wegsegment axis with
    ``STATUS=4`` (in service) whose length inside the corridor is at least
    ``axis_min_extent_m`` and whose share of that length falls inside this
    face is at least ``axis_coverage_ratio_min`` becomes a candidate; among
    candidates, only those whose ``MORF`` is a motor-traffic code count as
    motorized (see the module docstring). In priority order:

    - motorized candidates split between paved and unpaved: ``unmapped``,
      reason ``contradictory_axis_surface`` — an in-service axis disagreeing
      with itself on ``VERH`` is a data conflict, not resolved by picking a
      side;
    - motorized candidates all paved (``VERH`` 1 or 12): ``carriageway_paved``
      — unless the face's ``topology_status`` is ``unsplit`` (no internal WGO
      line at all, so the whole corridor was taken as one face) and its area
      divided by the longest paved axis's covered length exceeds
      ``unsplit_max_width_m``, in which case the axis alone cannot vouch for
      the full width of the corridor and the face stays ``unmapped``, reason
      ``unsplit_corridor_too_wide_for_single_carriageway``;
    - motorized candidates all unpaved (``VERH`` 2): ``unmapped``, reason
      ``axis_surface_not_paved``;
    - motorized candidates all of unknown/not-applicable ``VERH``:
      ``unmapped``, reason ``axis_surface_unknown``;
    - candidates present but none motorized (pedestrian/cycling/tram/service/
      unpaved-track/ferry ``MORF``): ``unmapped``, reason
      ``axis_not_motorized_carriageway``;
    - no usable candidate at all: ``unmapped``, reason
      ``no_in_service_axis_evidence``.

    This module does not produce ``sidewalk`` (see the module docstring).
    Every resolved face still carries ``wcz_boundary_ratio``/
    ``wrb_boundary_ratio``/``woz_boundary_ratio`` — the share of its own
    perimeter carried by each WGO type — as diagnostic-only measurements, and
    ``axis_coverage_ratio`` reports the strongest candidate actually behind
    the verdict, or (when no class-deciding evidence exists) the strongest
    candidate measured at all, purely for diagnosis. Every threshold is an
    explicit argument.
    """
    if (
        faces.crs is None
        or boundaries.crs != faces.crs
        or axes.crs != faces.crs
        or not faces.crs.is_projected
        or any(a.unit_conversion_factor != 1 for a in faces.crs.axis_info[:2])
    ):
        raise ValueError("GRB_CLASSIFY_CRS_INVALID: identical projected metre CRS required")
    if not math.isfinite(axis_coverage_ratio_min) or not 0.0 <= axis_coverage_ratio_min <= 1.0:
        raise ValueError("GRB_CLASSIFY_RATIO_INVALID: coverage ratio required within [0, 1]")
    if (
        any(
            not math.isfinite(v) or v < 0
            for v in (boundary_tolerance_m, length_tolerance_m, axis_min_extent_m)
        )
        or not math.isfinite(unsplit_max_width_m)
        or unsplit_max_width_m <= 0
    ):
        raise ValueError("GRB_CLASSIFY_TOLERANCE_INVALID: nonnegative finite tolerances required")
    for frame, fields, allowed in [
        (faces, (face_id, polygon_id, status_field), (Polygon, MultiPolygon)),
        (boundaries, (boundary_id, boundary_type_field), (LineString, MultiLineString)),
        (
            axes,
            (axis_id, axis_surface_field, axis_status_field, axis_morphology_field),
            (LineString, MultiLineString),
        ),
    ]:
        missing = [field for field in fields if field not in frame]
        if missing:
            raise ValueError(f"GRB_CLASSIFY_FIELD_MISSING: {missing}")
        if any(not isinstance(g, allowed) or g.is_empty or not g.is_valid for g in frame.geometry):
            raise ValueError(
                "GRB_CLASSIFY_GEOMETRY_INVALID: repair source geometries explicitly before classifying"
            )
    # face_id identifies the output row and must be unique; polygon_id groups
    # faces into corridors and repeats by design.
    column = faces[face_id]
    if (
        column.isna().any()
        or (column.astype(str).str.strip() == "").any()
        or column.astype(str).duplicated().any()
    ):
        raise ValueError("GRB_CLASSIFY_ID_INVALID: unique nonempty face IDs required")
    if faces[polygon_id].isna().any() or (faces[polygon_id].astype(str).str.strip() == "").any():
        raise ValueError("GRB_CLASSIFY_ID_INVALID: nonempty polygon IDs required")

    # Positional numpy masks throughout: an empty list would select columns, and a
    # caller may legitimately hand over a frame with duplicated index labels.
    in_service = axes[(axes[axis_status_field].map(_code) == _IN_SERVICE).to_numpy()]
    # axis_id (WS_OIDN, a Wegenregister reference, not a GRB OIDN) is validated
    # only on in-service rows: it is never a lookup key, only evidence text and
    # deterministic ordering, and an out-of-service row's blank/duplicate ID
    # never reaches the algorithm — it must not fail the whole call.
    axis_id_column = in_service[axis_id]
    if axis_id_column.isna().any() or (axis_id_column.astype(str).str.strip() == "").any():
        raise ValueError("GRB_CLASSIFY_ID_INVALID: nonempty in-service axis IDs required")
    axis_geometries = in_service.geometry.to_list()
    axis_tree = STRtree(axis_geometries)
    axis_ids = [str(v) for v in axis_id_column]
    axis_surfaces = [_code(v) for v in in_service[axis_surface_field]]
    axis_morphologies = [_code(v) for v in in_service[axis_morphology_field]]

    boundary_codes = boundaries[boundary_type_field].map(_code)
    boundary_trees = {
        type_code: STRtree(boundaries.geometry[(boundary_codes == type_code).to_numpy()].to_list())
        for type_code in (_WCZ, _WRB, _WOZ)
    }

    geometries = faces.geometry.to_list()
    statuses = [str(v) for v in faces[status_field]]
    parents = [str(v) for v in faces[polygon_id]]
    grouped: dict[str, list] = {}
    for position, status in enumerate(statuses):
        if status in _RESOLVED_STATUSES:
            grouped.setdefault(parents[position], []).append(geometries[position])
    corridors = {key: unary_union(parts) for key, parts in grouped.items()}

    records = []
    for position, status in enumerate(statuses):
        face = geometries[position]
        if status not in _RESOLVED_STATUSES:
            # Area conservation alone never certifies a subdivision, so unresolved
            # topology stops here: axes and boundaries are not consulted.
            records.append(
                {
                    "functional_class": "unmapped",
                    "classification_reason": "unresolved_topology:" + status,
                    "classification_evidence": "",
                    **dict.fromkeys(_RATIO_COLUMNS, math.nan),
                }
            )
            continue
        corridor = corridors[parents[position]]
        # candidates: (share, axis_id, VERH code, MORF code, length inside the
        # face). share <= 0 means the axis proves nothing at all (see
        # _interior_length) and is excluded here regardless of the threshold
        # below; matches is the subset that actually clears
        # axis_coverage_ratio_min and can decide a class.
        candidates = []
        for index in axis_tree.query(face, predicate="intersects"):
            line = axis_geometries[int(index)]
            extent = _interior_length(line, corridor)
            if extent <= length_tolerance_m or extent < axis_min_extent_m:
                continue
            face_length = _interior_length(line, face)
            share = face_length / extent
            if share <= 0:
                continue
            candidates.append(
                (
                    share,
                    axis_ids[int(index)],
                    axis_surfaces[int(index)],
                    axis_morphologies[int(index)],
                    face_length,
                )
            )
        matches = [c for c in candidates if c[0] >= axis_coverage_ratio_min]
        motorized = [c for c in matches if c[3] in _MOTORIZED_MORF]
        non_motorized = [c for c in matches if c[3] not in _MOTORIZED_MORF]
        paved = [c for c in motorized if c[2] in _PAVED]
        unpaved = [c for c in motorized if c[2] in _UNPAVED]
        unknown_surface = [c for c in motorized if c[2] not in _PAVED and c[2] not in _UNPAVED]
        ratios = _boundary_ratios(face, boundary_trees, boundary_tolerance_m)

        if paved and unpaved:
            functional_class, reason, evidence_set = (
                "unmapped",
                "contradictory_axis_surface",
                paved + unpaved,
            )
        elif paved:
            longest_covered = max(c[4] for c in paved)
            estimated_width = face.area / longest_covered
            if status == "unsplit" and estimated_width > unsplit_max_width_m:
                functional_class = "unmapped"
                reason = "unsplit_corridor_too_wide_for_single_carriageway"
            else:
                functional_class = "carriageway_paved"
                reason = "in_service_paved_axis"
            evidence_set = paved
        elif unpaved:
            functional_class, reason, evidence_set = "unmapped", "axis_surface_not_paved", unpaved
        elif unknown_surface:
            functional_class, reason, evidence_set = (
                "unmapped",
                "axis_surface_unknown",
                unknown_surface,
            )
        elif non_motorized:
            functional_class = "unmapped"
            reason = "axis_not_motorized_carriageway"
            evidence_set = non_motorized
        else:
            functional_class, reason, evidence_set = "unmapped", "no_in_service_axis_evidence", []

        evidence = _format_evidence(evidence_set, axis_surface_field) if evidence_set else ""
        axis_coverage_ratio = (
            max(c[0] for c in evidence_set)
            if evidence_set
            else max((c[0] for c in candidates), default=0.0)
        )
        records.append(
            {
                "functional_class": functional_class,
                "classification_reason": reason,
                "classification_evidence": evidence,
                "axis_coverage_ratio": axis_coverage_ratio,
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
            "unsplit_max_width_m": float(unsplit_max_width_m),
            "boundary_tolerance_m": float(boundary_tolerance_m),
            "length_tolerance_m": float(length_tolerance_m),
        },
        "inference": False,
    }
    return result, report
