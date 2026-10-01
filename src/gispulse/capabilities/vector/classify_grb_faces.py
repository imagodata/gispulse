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
  applicable. The objectenhandboek domain reads 1 *verharde weg*, 2
  *onverharde weg*, 12 *weg met zowel vaste en losse verharding*; the
  ``LBLVERH`` label delivered with the data reads 1 *weg met vaste
  verharding*, 2 *weg met losse verharding*. On its own, an axis with an
  unknown/not-applicable code is neither paved nor unpaved evidence — it is
  simply insufficient, and never contradicts a genuinely paved or unpaved
  axis covering the same face. (A ``MORF=125`` axis is the one exception:
  its morphology alone is unpaved evidence, see below.)
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
  parking/service entrance) count toward ``carriageway_paved``. Excluded
  from it: 113/114 (pedestrian/cycling, not for other vehicles), 116
  (tram-only), 120 (dienstweg, a service road), 125 (aardeweg, an earthen
  track), 130 (veer — a ferry crossing, not a road), any other or unknown
  code. The domain gives 120/125 as bare labels, with no surface rule.
- ``carriageway_unpaved`` rests on the same quality of evidence as
  ``carriageway_paved``: a documented source code on an in-service axis that
  clears the same coverage rules, never geometry. An axis is unpaved evidence
  when it is motorized (``MORF`` 101–112) with ``VERH=2``, or when its
  ``MORF`` is 125 (aardeweg) with ``VERH`` 2, -8, -9 or missing — on the live
  WFS (October 2026) in-service aardewegen are ``VERH=2`` (10000+) or -9
  (1282) far more often than 1/12 (435). ``MORF=120`` (dienstweg) only counts
  with ``VERH=2`` and only if the caller opts in with
  ``dienstweg_unpaved_evidence=True``: most in-service dienstwegen are coded
  ``VERH=1`` (797 against 115), so a dienstweg is never assumed unpaved. ``VERH=2`` is an
  unbound surface (gravel, crushed stone), **not** necessarily bare earth.
  This module states what the source says; which client tariff an unpaved
  carriageway maps to is decided downstream, never here. WGO boundaries play
  no part: a Woz line marks the edge of a soft shoulder, but reading the
  shoulder from adjacency is the same invertible inference that removed
  ``sidewalk``.
- A positive verdict is blocked, fail-closed, by contradicting evidence.
  Contradictions are looked for among every candidate axis (past the extent
  floor in the corridor, with more than ``length_tolerance_m`` inside the
  face), not only those above the coverage threshold, so an axis that merely
  crosses the face can still veto. ``carriageway_unpaved`` is only granted
  when every such axis is itself unpaved evidence: an axis coded ``VERH`` 1
  or 12, whatever its ``MORF``, gives ``axis_surface_conflict``; a
  motor-traffic axis of unknown surface, which may be the face's own road
  with the unpaved axis only crossing it, gives ``axis_surface_unknown``; any
  other axis gives ``axis_not_motorized_carriageway``. ``carriageway_paved``
  is vetoed (``axis_surface_conflict``) only by unpaved evidence that is
  itself matched or runs, inside the face, at least ``axis_min_extent_m`` and
  half the length of the longest deciding paved axis: an unbound footpath
  says nothing about the carriageway beside it, and an unmatched gravel side
  road joining the axis, at any angle, must not demote a long carriageway. The
  asymmetry is deliberate:
  the new class has not yet been validated on real unpaved data, the paved
  class has. A *matched* aardeweg coded ``VERH`` 1 or 12 contradicts itself
  and gives ``axis_surface_conflict``; as a mere candidate it only vetoes the
  unpaved class, like any paved axis.

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
_UNKNOWN_SURFACE = frozenset({-8, -9, None})  # documented unknown/not applicable, or missing
# Wegsegment MORF codes officially describing a motor-traffic way or junction.
# Excludes 113/114 (pedestrian/cycling, explicitly closed to other vehicles),
# 116 (tram-only), 120 (service road), 125 (earthen track), 130 (ferry
# crossing) and any code outside this documented range.
_MOTORIZED_MORF = frozenset(range(101, 113))
_EARTHEN_TRACK_MORF = 125  # aardeweg
_SERVICE_ROAD_MORF = 120  # dienstweg: opt-in, and only when coded VERH=2
# partition_polygons reports "unsplit" when area is conserved, no cut/dangle/
# invalid residual exceeds tolerance, and there was simply nothing to split —
# a WBN corridor with no internal WGO line is entirely and unambiguously one
# face. That is a resolved topology, not an unresolved one.
_RESOLVED_STATUSES = ("partitioned", "unsplit")
_CLASSES = ("carriageway_paved", "carriageway_unpaved", "sidewalk", "unmapped")
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


def _format_evidence(matches, surface_field: str, morphology_field: str | None = None) -> str:
    """Pair each retained axis with its own surface code, deduplicated and sorted.

    ``morphology_field`` also cites each axis's ``MORF``: unpaved and conflict
    verdicts can rest on ``MORF`` alone, so their evidence must show it. The
    paved format is left unchanged.
    """

    def text(code):
        return "unknown" if code is None else str(code)

    triples = sorted(
        {
            (axis_id, text(code), text(morf) if morphology_field else "")
            for _, axis_id, code, morf, _length in matches
        }
    )
    return ",".join(
        f"{axis_id}:{surface_field}={code}"
        + (f":{morphology_field}={morf}" if morphology_field else "")
        for axis_id, code, morf in triples
    )


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
    dienstweg_unpaved_evidence: bool = False,
) -> tuple[gpd.GeoDataFrame, dict]:
    """Label candidate faces from upstream GRB axis evidence, defaulting to ``unmapped``.

    ``faces`` must be a complete ``partition_polygons`` output: the parent
    corridor is rebuilt as the union of its resolved (``partitioned`` or
    ``unsplit``) faces. For each resolved face, every Wegsegment axis with
    ``STATUS=4`` (in service) whose length inside the corridor is at least
    ``axis_min_extent_m`` and that has some length inside this face is a
    *candidate*; a candidate whose share of that length falling inside this
    face is at least ``axis_coverage_ratio_min`` is a *match*. Only matches
    decide a class; every candidate can veto one. A match is *paved* evidence
    when motorized (``MORF`` 101–112) with ``VERH`` 1 or 12, and *unpaved*
    evidence when motorized with ``VERH=2``, when its ``MORF`` is 125 with
    ``VERH`` 2/-8/-9/missing, or — only with
    ``dienstweg_unpaved_evidence=True`` — when its ``MORF`` is 120 with
    ``VERH=2``. In priority order:

    - paved evidence vetoed by unpaved evidence that is matched or runs,
      inside the face, at least ``axis_min_extent_m`` and half the longest
      paved match; unpaved evidence vetoed by any candidate coded ``VERH`` 1
      or 12, whatever its ``MORF``; or a matched ``MORF=125`` axis coded
      ``VERH`` 1 or 12: ``unmapped``, reason ``axis_surface_conflict`` — the
      sources disagree, and picking a side would be an undocumented
      inference;
    - unpaved evidence with a motor-traffic candidate of unknown surface on
      the face: ``unmapped``, reason ``axis_surface_unknown``;
    - unpaved evidence with any other candidate on the face that is not
      itself unpaved evidence: ``unmapped``, reason
      ``axis_not_motorized_carriageway``;
    - paved evidence: ``carriageway_paved``, reason
      ``in_service_paved_axis`` — unless the face's ``topology_status`` is
      ``unsplit`` (no internal WGO line at all, so the whole corridor was
      taken as one face) and its area divided by the longest paved axis's
      covered length exceeds ``unsplit_max_width_m``, in which case the axis
      alone cannot vouch for the full width of the corridor and the face
      stays ``unmapped``, reason
      ``unsplit_corridor_too_wide_for_single_carriageway``;
    - unpaved evidence: ``carriageway_unpaved``, reason
      ``in_service_unpaved_axis``, under the same unsplit width guard
      (measured on the longest unpaved axis);
    - motorized candidates all of unknown/not-applicable ``VERH``:
      ``unmapped``, reason ``axis_surface_unknown``;
    - matches present but none motorized nor unpaved evidence (pedestrian/
      cycling/tram/ferry/unknown ``MORF``, a dienstweg not opted in or not
      coded ``VERH=2``, an aardeweg with an undocumented ``VERH``):
      ``unmapped``, reason ``axis_not_motorized_carriageway``;
    - no usable candidate at all: ``unmapped``, reason
      ``no_in_service_axis_evidence``.

    This module does not produce ``sidewalk`` (see the module docstring).
    Every resolved face still carries ``wcz_boundary_ratio``/
    ``wrb_boundary_ratio``/``woz_boundary_ratio`` — the share of its own
    perimeter carried by each WGO type — as diagnostic-only measurements, and
    ``axis_coverage_ratio`` reports the strongest candidate actually behind
    the verdict (for a conflict, possibly a vetoing candidate below the
    threshold), or (when no class-deciding evidence exists) the strongest
    candidate measured at all, purely for diagnosis. Evidence cites
    ``WS_OIDN:VERH`` per axis, plus ``:MORF`` whenever unpaved evidence or a
    conflict is involved. Every threshold is an explicit argument.
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
    if not isinstance(dienstweg_unpaved_evidence, bool):
        # TypeError on purpose: a caller mistake, never a degradable data defect.
        raise TypeError("GRB_CLASSIFY_OPTION_INVALID: dienstweg_unpaved_evidence must be a bool")
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

    def is_unpaved_evidence(candidate) -> bool:
        surface, morphology = candidate[2], candidate[3]
        return (
            (morphology in _MOTORIZED_MORF and surface in _UNPAVED)
            or (morphology == _EARTHEN_TRACK_MORF and surface in _UNPAVED | _UNKNOWN_SURFACE)
            or (
                dienstweg_unpaved_evidence
                and morphology == _SERVICE_ROAD_MORF
                and surface in _UNPAVED
            )
        )

    def is_self_contradicting(candidate) -> bool:
        # An aardeweg coded paved: it vetoes either verdict, never produces one.
        return candidate[3] == _EARTHEN_TRACK_MORF and candidate[2] in _PAVED

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
        unpaved = [c for c in matches if is_unpaved_evidence(c)]
        self_contradicting = [c for c in matches if is_self_contradicting(c)]
        # Vetoes come from every candidate, not only matches: an axis merely
        # crossing the face below the coverage threshold still contradicts a
        # verdict, provided its length inside this face exceeds
        # length_tolerance_m. The newer unpaved class is only granted when
        # every such axis is itself unpaved evidence. The validated paved class
        # is vetoed only by unpaved evidence that is matched or runs, inside
        # this very face, at least axis_min_extent_m and half the deciding
        # paved axis's length, so a gravel side road joining the axis at any
        # angle cannot demote a long carriageway (see module docstring).
        in_face = [c for c in candidates if c[4] > length_tolerance_m]
        blockers = [c for c in in_face if not is_unpaved_evidence(c)]
        paved_vetoes = [c for c in blockers if c[2] in _PAVED]
        unknown_vetoes = [
            c
            for c in blockers
            if c[3] in _MOTORIZED_MORF and c[2] not in _PAVED and c[2] not in _UNPAVED
        ]
        veto_length = max(axis_min_extent_m, 0.5 * max((c[4] for c in paved), default=0.0))
        unpaved_vetoes = [
            c
            for c in in_face
            if is_unpaved_evidence(c) and (c[0] >= axis_coverage_ratio_min or c[4] >= veto_length)
        ]
        unknown_surface = [c for c in motorized if c[2] not in _PAVED and c[2] not in _UNPAVED]
        ratios = _boundary_ratios(face, boundary_trees, boundary_tolerance_m)
        cite_morphology = True

        if paved and unpaved_vetoes:
            functional_class, reason = "unmapped", "axis_surface_conflict"
            evidence_set = paved + unpaved_vetoes
        elif unpaved and paved_vetoes:
            functional_class, reason = "unmapped", "axis_surface_conflict"
            evidence_set = paved_vetoes + unpaved
        elif self_contradicting:
            functional_class, reason = "unmapped", "axis_surface_conflict"
            evidence_set = paved + self_contradicting
        elif unpaved and unknown_vetoes:
            # A motor-traffic axis of unknown surface on the face may be the
            # road the face really belongs to, the unpaved axis only crossing it.
            functional_class, reason = "unmapped", "axis_surface_unknown"
            evidence_set = unknown_vetoes + unpaved
        elif unpaved and blockers:
            # Any other in-service axis on the face (a path, a service road, a
            # code outside the documented set) may be what the face really is.
            functional_class, reason = "unmapped", "axis_not_motorized_carriageway"
            evidence_set = blockers + unpaved
        elif paved or unpaved:
            evidence_set = paved or unpaved
            longest_covered = max(c[4] for c in evidence_set)
            estimated_width = face.area / longest_covered
            if status == "unsplit" and estimated_width > unsplit_max_width_m:
                functional_class = "unmapped"
                reason = "unsplit_corridor_too_wide_for_single_carriageway"
            elif paved:
                functional_class = "carriageway_paved"
                reason = "in_service_paved_axis"
            else:
                functional_class = "carriageway_unpaved"
                reason = "in_service_unpaved_axis"
            cite_morphology = not paved
        elif unknown_surface:
            functional_class, reason, evidence_set = (
                "unmapped",
                "axis_surface_unknown",
                unknown_surface,
            )
            cite_morphology = False
        elif non_motorized:
            functional_class = "unmapped"
            reason = "axis_not_motorized_carriageway"
            evidence_set = non_motorized
            cite_morphology = False
        else:
            functional_class, reason, evidence_set = "unmapped", "no_in_service_axis_evidence", []

        evidence = (
            _format_evidence(
                evidence_set,
                axis_surface_field,
                axis_morphology_field if cite_morphology else None,
            )
            if evidence_set
            else ""
        )
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
        "options": {"dienstweg_unpaved_evidence": dienstweg_unpaved_evidence},
        "inference": False,
    }
    return result, report
