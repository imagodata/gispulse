"""Build the road-crossings level-evidence artifact from GRB corridors, axes and structures.

Output contract (one row per physical road unit, EPSG:31370): the footprint
polygon is the active geometry, ``axis`` is a second geometry column, plus
``road_id``, ``structure`` (``ground`` / ``grade_separated`` / ``unknown``) and
``complex_crossing``. A separate coverage polygon declares where the artifact
is complete. Level and category only, each from a cited source rule: no price,
no cost class, no paving material.

Evidence rules:

- ``ground``: WBN (wegbaan) elements carrying an in-service motor-traffic
  Wegsegment axis. The GRB objectenhandboek captures only the corridor visible
  at ground level as wegbaan — "enkel de aan het maaiveld zichtbare
  wegcorridor wordt opgenomen als wegbaan. Waar de wegcorridor ingetunneld is,
  op een overbrugging gelegen is of door een overbrugging wordt afgedekt, wordt
  geen wegbaan (Wbn) opgenomen." — so a WBN element is positive evidence of a
  ground-level corridor. A road with no WBN element is never ``ground``. The
  footprint is the whole WBN corridor (kerb to kerb and beyond: sidewalks and
  verges inside it), not a carriageway-only surface. Touching
  ``wegsegment``-type elements that share a carriageway axis (and the same
  category) are fragments of one carriageway: one ``road_id``, footprints
  united, axes joined. A ``kruispuntzone`` (WBN ``TYPE`` 1), an element
  holding an axis end that is not a plain pass-through node (where three or
  more ends meet: a junction the GRB did not map as a kruispuntzone; a single
  end: a dead end reaching into it), and an element with an undocumented
  ``TYPE`` always stay units of their own. ``road_id`` is the unit's first WBN ``OIDN``: stable
  for a given acquisition, but a unit cut by the bounds of another acquisition
  can get another id — never concatenate bundles without deduplicating on
  ``source_ids``.
- ``grade_separated``: a group of touching KNW ``overbrugging`` (1) /
  ``tunnelmond`` (12) polygons inside which at least two in-service
  motor-traffic axes cross without a node, every such axis takes part in such
  a crossing, and none ends inside. The OSLO Wegenregister profile defines a
  Wegknoop as the object describing connectivity between two segments; two
  axes crossing with no node are not connected there, and the KNW footprint
  documents the structure that separates them. This is a level relation
  between two roads, not a bare bridge tag — an inference from those two
  official facts, not a rule the source states (the Wegenregister
  ``OngelijkgrondseKruising`` relation would state it, and is not acquired).
  Which road is on top is not resolved: the label holds for an excavation
  that follows one of the crossing roads through the structure, not for one
  dug at ground level under a deck off any mapped road. Measured on a Gand
  sample: 91 of 101 node-less crossings of motor-traffic axes lie inside a
  KNW 1/12 footprint.
- ``unknown``: any other structure group carrying a motor-traffic axis — a
  single axis (a road over water, or under a railway bridge: KNW alone cannot
  tell which), an axis ending inside (a junction or dead end on or under the
  structure), or an axis that crosses no other one.

Axes are sorted by ``MORF``/``STATUS`` only: in-service 101-112 and 120
(dienstweg) are carriageways; 113 (voetgangerszone), 114 (wandel- of
fietsweg), 116 (tramweg), 125 (aardeweg) and 130 (veer) are not; everything
else — -8, a carriageway not in service — is unresolved. 120 and 125 are bare
labels in the domain: classing them is a costing-side decision, taken here
explicitly (a service road is bored under like a road, an earthen track is
trenched through) and listed in the report.

``complex_crossing`` is true when an axis of the unit has a motorway
morphology (default ``MORF`` 101 autosnelweg, defined by the Wegenregister
catalogue as two physically separated carriageways with no at-grade
crossing), never derived from material. It is per unit: a corridor carrying
an autosnelweg and its parallelweg is complex as a whole.

Coverage starts from the acquisition bounds and excludes:

- every ``unknown`` footprint;
- every structure group carrying an unresolved axis (it could change the
  verdict), every WBN element carrying only unresolved axes or no axis at
  all, and a buffer around an unresolved axis inside a ``ground`` element (the
  element is ground either way; a trench crossing that axis alone would go
  unseen). A structure with no drivable axis is no road and stays covered;
- every footprint that leaves the bounds: axes entirely outside were not
  acquired, so its verdict cannot be trusted;
- a buffer around any drivable (carriageway or unresolved) axis length that
  no unit accounts for: a road with no footprint (a private road the GRB did
  not capture, a tunnel body) or one running exactly on a boundary;
- a buffer around any node-less crossing of drivable axes, or self-crossing,
  outside every structure (level evidence that contradicts the ground rule).

A WBN element carrying only documented non-motor axes stays covered without a
row: it is documented as not being a carriageway.

Sources:
https://www.vlaanderen.be/digitaal-vlaanderen/onze-diensten-en-platformen/basiskaart-vlaanderen-grb/objectenhandboek-basiskaart-vlaanderen-grb/wegbaan-wbn
https://www.vlaanderen.be/digitaal-vlaanderen/onze-diensten-en-platformen/basiskaart-vlaanderen-grb/objectenhandboek-basiskaart-vlaanderen-grb/kunstwerk-knw
https://data.vlaanderen.be/doc/applicatieprofiel/wegenregister/
https://metadata.dev-vlaanderen.be/srv/api/records/aff2c639-28cf-4bee-8fa8-9213663a5ef4
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import geopandas as gpd
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

from gispulse.capabilities.vector.reconcile_knw_structures import _code, _nonempty_id_column
from gispulse.capabilities.vector.road_crossings_common import (
    CARRIAGEWAY,
    NON_CARRIAGEWAY,
    UNRESOLVED,
    CrossingsBuilder,
    GroundElement,
    endpoints,
    group_touching,
    points,
    polygonal,
    validate_numbers,
)

_STRUCTURE_CODES = frozenset({1, 12})  # KNW overbrugging, tunnelmond
_KRUISPUNTZONE, _WEGSEGMENT = 1, 2  # WBN TYPE
_IN_SERVICE = 4  # Wegsegment STATUS
# Wegsegment MORF 101-112: documented motor-traffic ways and junctions (same
# gate as classify_grb_faces), plus 120 dienstweg by explicit decision.
_CARRIAGEWAY_MORF = frozenset(range(101, 113)) | {120}
# Documented as closed to general motor traffic (voetgangerszone, wandel- of
# fietsweg, tramweg, veer), plus 125 aardeweg by explicit decision.
_NON_CARRIAGEWAY_MORF = frozenset({113, 114, 116, 125, 130})
_COMPLEX_MORF = (101,)  # autosnelweg


def _validate(wbn, wegsegment, knw, *, wbn_fields, knw_type_field, axis_fields):
    if any(frame.crs is None or frame.crs.to_epsg() != 31370 for frame in (wbn, wegsegment, knw)):
        raise ValueError("GRB_CROSSINGS_CRS_INVALID: EPSG:31370 required on every layer")
    for frame, fields in [(wbn, wbn_fields), (wegsegment, axis_fields), (knw, (knw_type_field,))]:
        missing = [name for name in fields if name not in frame]
        if missing:
            raise ValueError(f"GRB_CROSSINGS_FIELD_MISSING: {missing}")
    for frame, allowed in [
        (wbn, (Polygon, MultiPolygon)),
        (wegsegment, (LineString, MultiLineString)),
    ]:
        if any(not isinstance(g, allowed) or g.is_empty or not g.is_valid for g in frame.geometry):
            raise ValueError(
                "GRB_CROSSINGS_GEOMETRY_INVALID: repair source geometries explicitly first"
            )
    for frame, name in [(wbn, wbn_fields[0]), (wegsegment, axis_fields[0])]:
        if _nonempty_id_column(frame, name, "GRB_CROSSINGS_ID_INVALID").duplicated().any():
            raise ValueError("GRB_CROSSINGS_ID_INVALID: unique nonempty IDs required")


def _bounds_area(coverage_bounds):
    if (
        len(coverage_bounds) != 4
        or not all(math.isfinite(v) for v in coverage_bounds)
        or coverage_bounds[0] >= coverage_bounds[2]
        or coverage_bounds[1] >= coverage_bounds[3]
    ):
        raise ValueError("GRB_CROSSINGS_EXTENT_INVALID: ordered finite EPSG:31370 bounds required")
    return box(*coverage_bounds)


def build_grb_road_crossings(
    wbn: gpd.GeoDataFrame,
    wegsegment: gpd.GeoDataFrame,
    knw: gpd.GeoDataFrame,
    *,
    coverage_bounds: tuple,
    length_tolerance_m: float,
    area_tolerance_m2: float,
    boundary_tolerance_m: float,
    exclusion_buffer_m: float,
    wbn_id: str = "OIDN",
    wbn_type_field: str = "TYPE",
    knw_id: str = "OIDN",
    knw_type_field: str = "TYPE",
    axis_id: str = "WS_OIDN",
    axis_status_field: str = "STATUS",
    axis_morphology_field: str = "MORF",
    complex_morf_codes: Iterable[int] = _COMPLEX_MORF,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, dict]:
    """Return ``(crossings, coverage, exclusions, report)`` — see the module docstring.

    Tolerances absorb numerical residuals only (no snapping): ``length_tolerance_m``
    drops degenerate axis pieces, ``area_tolerance_m2`` ignores sliver overlaps,
    ``boundary_tolerance_m`` groups touching polygons and absorbs the rounding
    between an axis and its clipped pieces. ``exclusion_buffer_m`` is the
    half-width removed from coverage around an axis length no unit accounts for,
    or around a node-less crossing outside any structure: an explicit, reported
    choice, since the missing corridor's real width is unknown. ``exclusions``
    lists every area removed from coverage with its reason. Reported lengths and
    counts are measured inside ``coverage_bounds`` only.
    """
    numbers = {
        "length_tolerance_m": length_tolerance_m,
        "area_tolerance_m2": area_tolerance_m2,
        "boundary_tolerance_m": boundary_tolerance_m,
        "exclusion_buffer_m": exclusion_buffer_m,
    }
    area = _bounds_area(coverage_bounds)
    validate_numbers("GRB_CROSSINGS", area, numbers)
    _validate(
        wbn,
        wegsegment,
        knw,
        wbn_fields=(wbn_id, wbn_type_field),
        knw_type_field=knw_type_field,
        axis_fields=(axis_id, axis_status_field, axis_morphology_field),
    )
    complex_codes = frozenset(int(c) for c in complex_morf_codes)

    # --- Axes ---------------------------------------------------------------
    axis_morf = [_code(v) for v in wegsegment[axis_morphology_field]]
    axis_kind = []
    for morf, status in zip(axis_morf, (_code(v) for v in wegsegment[axis_status_field])):
        if morf in _NON_CARRIAGEWAY_MORF:
            axis_kind.append(NON_CARRIAGEWAY)
        elif morf in _CARRIAGEWAY_MORF and status == _IN_SERVICE:
            axis_kind.append(CARRIAGEWAY)
        else:
            axis_kind.append(UNRESOLVED)
    builder = CrossingsBuilder(
        axis_ids=_nonempty_id_column(wegsegment, axis_id, "GRB_CROSSINGS_ID_INVALID").tolist(),
        axis_geoms=list(wegsegment.geometry),
        axis_kind=axis_kind,
        axis_codes=["" if m is None else str(m) for m in axis_morf],
        axis_complex=[m in complex_codes for m in axis_morf],
        crs=wbn.crs,
        coverage_area=area,
        numbers=numbers,
        source="GRB",
        region="VL",
    )
    axis_ids, axis_geoms = builder.axis_ids, builder.axis_geoms

    # --- Structures (KNW 1/12), grouped when they touch ----------------------
    structure_codes = knw[knw_type_field].map(_code)
    structures = knw[structure_codes.isin(_STRUCTURE_CODES).to_numpy()]
    if len(structures) and knw_id not in structures:
        raise ValueError(f"GRB_CROSSINGS_FIELD_MISSING: ['{knw_id}' (on structure-typed KNW)]")
    if any(
        not isinstance(g, (Polygon, MultiPolygon)) or g.is_empty or not g.is_valid
        for g in structures.geometry
    ):
        raise ValueError(
            "GRB_CROSSINGS_GEOMETRY_INVALID: repair source geometries explicitly first"
        )
    structure_ids: list[str] = []
    if len(structures):
        structure_column = _nonempty_id_column(structures, knw_id, "GRB_CROSSINGS_ID_INVALID")
        if structure_column.duplicated().any():
            raise ValueError("GRB_CROSSINGS_ID_INVALID: unique nonempty IDs required")
        structure_ids = structure_column.tolist()
    structure_geoms = list(structures.geometry)
    members = group_touching(structure_geoms, boundary_tolerance_m)
    structure_report = {
        "groups": len(members),
        "grade_separated": 0,
        "unknown": {},
        "without_carriageway": 0,
        "unresolved": 0,
        "with_unresolved_axes": 0,
    }

    for positions in sorted(members, key=lambda ps: sorted(structure_ids[p] for p in ps)):
        ids = sorted(structure_ids[p] for p in positions)
        footprint = polygonal(unary_union([structure_geoms[p] for p in positions]))
        builder.check_bounds(footprint)
        found = builder.by_kind(builder.pieces(footprint))
        carriageway, unresolved = found[CARRIAGEWAY], found[UNRESOLVED]
        if unresolved:
            builder.exclude("structure_axes_unresolved", footprint)
            builder.account(unresolved)
        if not carriageway:
            structure_report["unresolved" if unresolved else "without_carriageway"] += 1
            continue
        if unresolved:
            structure_report["with_unresolved_axes"] += 1
        ends_inside = any(
            footprint.contains(point) for j in carriageway for point in endpoints(axis_geoms[j])
        )
        crossed: set[int] = set()
        pairs = []
        ordered = sorted(carriageway)
        for n, a in enumerate(ordered):
            for b in ordered[n + 1 :]:
                meeting = unary_union(carriageway[a]).intersection(unary_union(carriageway[b]))
                if any(footprint.contains(p) for p in points(meeting)):
                    crossed.update((a, b))
                    pairs.append(f"{axis_ids[a]}x{axis_ids[b]}")
        if ends_inside:
            reason = "axis_ends_inside"
        elif len(carriageway) < 2:
            reason = "single_axis"
        elif crossed != set(carriageway):
            reason = "axis_not_crossed"
        else:
            reason = None
        road_id = "GRB:KNW:" + "+".join(ids)
        if reason is None:
            structure_report["grade_separated"] += 1
            evidence = "KNW " + "+".join(ids) + " node-less axis crossings " + ",".join(pairs)
            builder.add_row(
                road_id, "grade_separated", "KNW", ids, footprint, carriageway, evidence
            )
        else:
            counts = structure_report["unknown"]
            counts[reason] = counts.get(reason, 0) + 1
            evidence = f"KNW {'+'.join(ids)} {reason}"
            builder.add_row(road_id, "unknown", "KNW", ids, footprint, carriageway, evidence)
            builder.exclude(f"structure_{reason}", footprint)

    # --- WBN elements: trim, sort, then group the fragments of one carriageway -
    wbn_ids = _nonempty_id_column(wbn, wbn_id, "GRB_CROSSINGS_ID_INVALID").tolist()
    wbn_geoms = list(wbn.geometry)
    wbn_types = [_code(v) for v in wbn[wbn_type_field]]
    order = sorted(range(len(wbn_geoms)), key=lambda i: (len(wbn_ids[i]), wbn_ids[i]))
    wbn_tree, structure_tree = STRtree(wbn_geoms), STRtree(structure_geoms)
    claimed: set[int] = set()
    wbn_report = {
        "elements": len(wbn_geoms),
        "ground_elements": 0,
        "ground_units": 0,
        "non_carriageway": 0,
        "unresolved": 0,
        "with_unresolved_axes": 0,
        "without_axis": 0,
        "overlap_trimmed": 0,
        "unrecognised_type": sum(t not in (_KRUISPUNTZONE, _WEGSEGMENT) for t in wbn_types),
    }
    ground: list[GroundElement] = []
    for i in order:
        footprint = wbn_geoms[i]
        # Structures take priority ("een wegbaanelement houdt op ter hoogte van een
        # kunstwerk"), then the earlier element: no area is ever claimed twice.
        overlapping = [structure_geoms[int(k)] for k in structure_tree.query(footprint)] + [
            wbn_geoms[j] for j in (int(k) for k in wbn_tree.query(footprint)) if j in claimed
        ]
        trimmed = footprint
        for other in overlapping:
            if trimmed.intersection(other).area > area_tolerance_m2:
                trimmed = trimmed.difference(other)
        claimed.add(i)
        if trimmed is not footprint:
            wbn_report["overlap_trimmed"] += 1
            trimmed = polygonal(trimmed)
            if trimmed is None or trimmed.area <= area_tolerance_m2:
                continue
        builder.check_bounds(trimmed)
        found = builder.by_kind(builder.pieces(trimmed))
        if found[UNRESOLVED]:
            builder.account(found[UNRESOLVED])
            if found[CARRIAGEWAY]:
                # Ground either way: only a trench crossing the unresolved branch
                # alone would go unseen, so only that branch's surroundings go.
                builder.exclude_buffers("wbn_axes_unresolved", found[UNRESOLVED])
            else:
                builder.exclude("wbn_axes_unresolved", trimmed)
        if found[CARRIAGEWAY]:
            wbn_report["ground_elements"] += 1
            wbn_report["with_unresolved_axes"] += bool(found[UNRESOLVED])
            # A kruispuntzone, an element holding a junction or a dead end, or
            # one whose TYPE is not a documented code is never a fragment.
            mergeable = wbn_types[i] == _WEGSEGMENT and not builder.has_stop_node(trimmed)
            ground.append(
                GroundElement(
                    wbn_ids[i],
                    trimmed,
                    found[CARRIAGEWAY],
                    mergeable,
                    (len(wbn_ids[i]), wbn_ids[i]),
                )
            )
        elif found[UNRESOLVED]:
            wbn_report["unresolved"] += 1
        elif found[NON_CARRIAGEWAY]:
            wbn_report["non_carriageway"] += 1
        else:
            wbn_report["without_axis"] += 1
            builder.exclude("wbn_without_axis", trimmed)
    wbn_report["ground_units"] = builder.add_ground_units(ground, "GRB:WBN:", "WBN", "maaiveld")

    crossings, coverage, excluded, report = builder.finish(structure_geoms)
    report.update(
        wbn=wbn_report,
        structures=structure_report,
        thresholds={**numbers, "complex_morf_codes": sorted(complex_codes)},
        axis_classes={
            "carriageway_morf": sorted(_CARRIAGEWAY_MORF),
            "non_carriageway_morf": sorted(_NON_CARRIAGEWAY_MORF),
            "carriageway_status": _IN_SERVICE,
        },
    )
    return crossings, coverage, excluded, report
