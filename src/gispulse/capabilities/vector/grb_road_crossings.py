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
  holding a node where three or more axis ends meet (a junction the GRB did not
  map as a kruispuntzone), and an element with an undocumented ``TYPE`` always
  stay units of their own. ``road_id`` is the unit's first WBN ``OIDN``: stable
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

Axes are sorted by ``MORF``/``STATUS`` only: in-service 101-112 are
carriageways; 113 (voetgangerszone), 114 (wandel- of fietsweg), 116 (tramweg)
and 130 (veer), documented as closed to general motor traffic, are not;
everything else — 120 dienstweg and 125 aardeweg (bare labels in the domain),
-8, a carriageway not in service — is unresolved.

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
from collections import Counter
from collections.abc import Iterable

import geopandas as gpd
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box
from shapely.ops import linemerge, unary_union

from gispulse.capabilities.vector.reconcile_knw_structures import _code, _nonempty_id_column

_STRUCTURE_CODES = frozenset({1, 12})  # KNW overbrugging, tunnelmond
_KRUISPUNTZONE, _WEGSEGMENT = 1, 2  # WBN TYPE
_IN_SERVICE = 4  # Wegsegment STATUS
# Wegsegment MORF 101-112: documented motor-traffic ways and junctions (same
# gate as classify_grb_faces).
_CARRIAGEWAY_MORF = frozenset(range(101, 113))
# Documented as closed to general motor traffic: voetgangerszone, wandel- of
# fietsweg, tramweg, veer. 120 dienstweg and 125 aardeweg are bare labels: unresolved.
_NON_CARRIAGEWAY_MORF = frozenset({113, 114, 116, 130})
_COMPLEX_MORF = (101,)  # autosnelweg
_CARRIAGEWAY, _NON_CARRIAGEWAY, _UNRESOLVED = "carriageway", "non_carriageway", "unresolved"
_COLUMNS = (
    "road_id",
    "structure",
    "complex_crossing",
    "structure_evidence",
    "source",
    "source_layer",
    "source_ids",
    "axis_ids",
    "morf_codes",
)


def _lines(geometry) -> list[LineString]:
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    return [line for part in getattr(geometry, "geoms", ()) for line in _lines(part)]


def _points(geometry) -> list[Point]:
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Point):
        return [geometry]
    return [point for part in getattr(geometry, "geoms", ()) for point in _points(part)]


def _polygonal(geometry) -> Polygon | MultiPolygon | None:
    parts = [
        polygon
        for part in getattr(geometry, "geoms", [geometry])
        for polygon in getattr(part, "geoms", [part])
        if isinstance(polygon, Polygon) and not polygon.is_empty
    ]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def _endpoints(geometry) -> list[Point]:
    return [Point(line.coords[i]) for line in _lines(geometry) for i in (0, -1)]


def _pieces_inside(line, polygon, tolerance: float) -> list[LineString]:
    """Axis pieces genuinely inside ``polygon``.

    A tangent contact yields a point and a run along the boundary has its
    midpoint on the boundary: neither is a piece of this footprint's axis.
    """
    return [
        piece
        for piece in _lines(line.intersection(polygon))
        if piece.length > tolerance and polygon.contains(piece.interpolate(0.5, normalized=True))
    ]


def _simple_axes(pieces: Iterable[LineString]) -> list[LineString]:
    """Join contiguous pieces (degree-2 nodes only, junctions stay split);
    never emit a self-intersecting line."""
    merged = _lines(linemerge(list(pieces)))
    out: list[LineString] = []
    for line in merged:
        out.extend([line] if line.is_simple else _lines(shapely.node(line)))
    return out


def _self_crossings(line) -> list[Point]:
    """Points where a non-simple axis crosses itself (not its own ends)."""
    if line.is_simple:
        return []
    ends = {(p.x, p.y) for p in _endpoints(line)}
    return [
        p
        for p in {(q.x, q.y): q for q in _endpoints(shapely.node(line))}.values()
        if (p.x, p.y) not in ends
    ]


class _DisjointSets:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        self.parent[self.find(left)] = self.find(right)


def _validate(
    wbn,
    wegsegment,
    knw,
    *,
    wbn_fields,
    knw_type_field,
    axis_fields,
    coverage_bounds,
    numbers,
):
    if any(frame.crs is None or frame.crs.to_epsg() != 31370 for frame in (wbn, wegsegment, knw)):
        raise ValueError("GRB_CROSSINGS_CRS_INVALID: EPSG:31370 required on every layer")
    if (
        len(coverage_bounds) != 4
        or not all(math.isfinite(v) for v in coverage_bounds)
        or coverage_bounds[0] >= coverage_bounds[2]
        or coverage_bounds[1] >= coverage_bounds[3]
    ):
        raise ValueError("GRB_CROSSINGS_EXTENT_INVALID: ordered finite EPSG:31370 bounds required")
    if any(not math.isfinite(v) or v < 0 for v in numbers.values()) or not (
        numbers["exclusion_buffer_m"] > 0
    ):
        raise ValueError(
            "GRB_CROSSINGS_TOLERANCE_INVALID: nonnegative finite tolerances and a positive "
            "exclusion buffer required"
        )
    for frame, fields in [(wbn, wbn_fields), (wegsegment, axis_fields), (knw, (knw_type_field,))]:
        missing = [field for field in fields if field not in frame]
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
    for frame, field in [(wbn, wbn_fields[0]), (wegsegment, axis_fields[0])]:
        if _nonempty_id_column(frame, field, "GRB_CROSSINGS_ID_INVALID").duplicated().any():
            raise ValueError("GRB_CROSSINGS_ID_INVALID: unique nonempty IDs required")


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
    axis_fields = (axis_id, axis_status_field, axis_morphology_field)
    numbers = {
        "length_tolerance_m": length_tolerance_m,
        "area_tolerance_m2": area_tolerance_m2,
        "boundary_tolerance_m": boundary_tolerance_m,
        "exclusion_buffer_m": exclusion_buffer_m,
    }
    _validate(
        wbn,
        wegsegment,
        knw,
        wbn_fields=(wbn_id, wbn_type_field),
        knw_type_field=knw_type_field,
        axis_fields=axis_fields,
        coverage_bounds=coverage_bounds,
        numbers=numbers,
    )
    crs = wbn.crs
    bounds = box(*coverage_bounds)
    inside_bounds = bounds.buffer(boundary_tolerance_m)
    shapely.prepare(inside_bounds)
    complex_codes = frozenset(int(c) for c in complex_morf_codes)
    exclusions: list[tuple[str, object]] = []

    def exclude(reason, geometry):
        exclusions.append((reason, geometry))

    # --- Axes ---------------------------------------------------------------
    axis_ids = _nonempty_id_column(wegsegment, axis_id, "GRB_CROSSINGS_ID_INVALID").tolist()
    axis_morf = [_code(v) for v in wegsegment[axis_morphology_field]]
    axis_kind = []
    for morf, status in zip(axis_morf, (_code(v) for v in wegsegment[axis_status_field])):
        if morf in _NON_CARRIAGEWAY_MORF:
            axis_kind.append(_NON_CARRIAGEWAY)
        elif morf in _CARRIAGEWAY_MORF and status == _IN_SERVICE:
            axis_kind.append(_CARRIAGEWAY)
        else:
            axis_kind.append(_UNRESOLVED)
    axis_geoms = list(wegsegment.geometry)
    axis_tree = STRtree(axis_geoms)
    # Axis pieces some unit accounts for (a row, or an excluded footprint);
    # whatever drivable length is left over gets a coverage hole.
    accounted: dict[int, list[LineString]] = {}

    def pieces_by_kind(footprint):
        found: dict[str, dict[int, list[LineString]]] = {
            _CARRIAGEWAY: {},
            _NON_CARRIAGEWAY: {},
            _UNRESOLVED: {},
        }
        for j in sorted(int(i) for i in axis_tree.query(footprint)):
            pieces = _pieces_inside(axis_geoms[j], footprint, length_tolerance_m)
            if pieces:
                found[axis_kind[j]][j] = pieces
        return found

    def account(found_kind):
        for j, pieces in found_kind.items():
            accounted.setdefault(j, []).extend(pieces)

    rows: list[dict] = []

    def add_row(road_id, structure, layer, source_ids, footprint, pieces, evidence):
        axes = _simple_axes(line for j in sorted(pieces) for line in pieces[j])
        morfs = sorted({axis_morf[j] for j in pieces if axis_morf[j] is not None})
        rows.append(
            {
                "road_id": road_id,
                "structure": structure,
                "complex_crossing": any(m in complex_codes for m in morfs),
                "structure_evidence": evidence,
                "source": "GRB",
                "source_layer": layer,
                "source_ids": "+".join(source_ids),
                "axis_ids": ",".join(sorted({axis_ids[j] for j in pieces})),
                "morf_codes": ",".join(str(m) for m in morfs),
                "geometry": footprint,
                "axis": axes[0] if len(axes) == 1 else MultiLineString(axes),
            }
        )
        account(pieces)

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
    structure_tree = STRtree(structure_geoms)
    groups = _DisjointSets(len(structure_geoms))
    if structure_geoms:
        left, right = structure_tree.query(
            structure_geoms, predicate="dwithin", distance=boundary_tolerance_m
        )
        for a, b in zip(left, right):
            groups.union(int(a), int(b))
    members: dict[int, list[int]] = {}
    for position in range(len(structure_geoms)):
        members.setdefault(groups.find(position), []).append(position)
    structure_report = {
        "groups": len(members),
        "grade_separated": 0,
        "unknown": {},
        "without_carriageway": 0,
        "unresolved": 0,
        "with_unresolved_axes": 0,
    }

    for positions in sorted(members.values(), key=lambda ps: sorted(structure_ids[p] for p in ps)):
        ids = sorted(structure_ids[p] for p in positions)
        footprint = _polygonal(unary_union([structure_geoms[p] for p in positions]))
        if not inside_bounds.contains(footprint):
            exclude("footprint_crosses_bounds", footprint)
        found = pieces_by_kind(footprint)
        carriageway, unresolved = found[_CARRIAGEWAY], found[_UNRESOLVED]
        if unresolved:
            exclude("structure_axes_unresolved", footprint)
            account(unresolved)
        if not carriageway:
            structure_report["unresolved" if unresolved else "without_carriageway"] += 1
            continue
        if unresolved:
            structure_report["with_unresolved_axes"] += 1
        ends_inside = any(
            footprint.contains(point) for j in carriageway for point in _endpoints(axis_geoms[j])
        )
        crossed: set[int] = set()
        pairs = []
        ordered = sorted(carriageway)
        for n, a in enumerate(ordered):
            for b in ordered[n + 1 :]:
                meeting = unary_union(carriageway[a]).intersection(unary_union(carriageway[b]))
                if any(footprint.contains(p) for p in _points(meeting)):
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
            add_row(road_id, "grade_separated", "KNW", ids, footprint, carriageway, evidence)
        else:
            counts = structure_report["unknown"]
            counts[reason] = counts.get(reason, 0) + 1
            evidence = f"KNW {'+'.join(ids)} {reason}"
            add_row(road_id, "unknown", "KNW", ids, footprint, carriageway, evidence)
            exclude(f"structure_{reason}", footprint)

    # --- WBN elements: trim, sort, then group the fragments of one carriageway -
    wbn_ids = _nonempty_id_column(wbn, wbn_id, "GRB_CROSSINGS_ID_INVALID").tolist()
    wbn_geoms = list(wbn.geometry)
    wbn_types = [_code(v) for v in wbn[wbn_type_field]]
    # Where three or more axis ends meet, carriageways join: an element holding
    # such a node is a junction even when not mapped as a kruispuntzone, and
    # an element whose TYPE is not a documented code is never assumed a fragment.
    degree = Counter((round(p.x, 3), round(p.y, 3)) for g in axis_geoms for p in _endpoints(g))
    junction_nodes = [Point(xy) for xy, count in degree.items() if count >= 3]
    junction_tree = STRtree(junction_nodes)
    order = sorted(range(len(wbn_geoms)), key=lambda i: (len(wbn_ids[i]), wbn_ids[i]))
    wbn_tree = STRtree(wbn_geoms)
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
    # element -> (trimmed footprint, carriageway axis positions, complex flag,
    # whether it may be merged as a fragment)
    ground: dict[int, tuple] = {}
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
            trimmed = _polygonal(trimmed)
            if trimmed is None or trimmed.area <= area_tolerance_m2:
                continue
        if not inside_bounds.contains(trimmed):
            exclude("footprint_crosses_bounds", trimmed)
        found = pieces_by_kind(trimmed)
        if found[_UNRESOLVED]:
            account(found[_UNRESOLVED])
            if found[_CARRIAGEWAY]:
                # Ground either way: only a trench crossing the unresolved branch
                # alone would go unseen, so only that branch's surroundings go.
                for pieces in found[_UNRESOLVED].values():
                    for piece in pieces:
                        exclude("wbn_axes_unresolved", piece.buffer(exclusion_buffer_m))
            else:
                exclude("wbn_axes_unresolved", trimmed)
        if found[_CARRIAGEWAY]:
            wbn_report["ground_elements"] += 1
            wbn_report["with_unresolved_axes"] += bool(found[_UNRESOLVED])
            complex_flag = any(axis_morf[j] in complex_codes for j in found[_CARRIAGEWAY])
            mergeable = wbn_types[i] == _WEGSEGMENT and not any(
                trimmed.contains(junction_nodes[int(k)]) for k in junction_tree.query(trimmed)
            )
            ground[i] = (trimmed, frozenset(found[_CARRIAGEWAY]), complex_flag, mergeable)
        elif found[_UNRESOLVED]:
            wbn_report["unresolved"] += 1
        elif found[_NON_CARRIAGEWAY]:
            wbn_report["non_carriageway"] += 1
        else:
            wbn_report["without_axis"] += 1
            exclude("wbn_without_axis", trimmed)

    elements = sorted(ground, key=lambda i: (len(wbn_ids[i]), wbn_ids[i]))
    position = {i: n for n, i in enumerate(elements)}
    fragments = _DisjointSets(len(elements))
    if elements:
        ground_geoms = [ground[i][0] for i in elements]
        left, right = STRtree(ground_geoms).query(
            ground_geoms, predicate="dwithin", distance=boundary_tolerance_m
        )
        for a, b in zip(left, right):
            i, k = elements[int(a)], elements[int(b)]
            if (
                i < k
                and ground[i][3]
                and ground[k][3]
                and ground[i][2] == ground[k][2]
                and ground[i][1] & ground[k][1]
            ):
                fragments.union(position[i], position[k])
    units: dict[int, list[int]] = {}
    for i in elements:
        units.setdefault(fragments.find(position[i]), []).append(i)
    for unit in units.values():
        ids = [wbn_ids[i] for i in unit]  # already in (len, id) order
        footprint = _polygonal(unary_union([ground[i][0] for i in unit]))
        # Clip the original axes against the united footprint, so a carriageway
        # crossing a fragment boundary keeps one continuous axis.
        found = pieces_by_kind(footprint)
        wbn_report["ground_units"] += 1
        add_row(
            f"GRB:WBN:{ids[0]}",
            "ground",
            "WBN",
            ids,
            footprint,
            found[_CARRIAGEWAY],
            f"WBN {'+'.join(ids)} maaiveld",
        )

    # --- Drivable axes and level conflicts no unit vouches for ----------------
    drivable = [j for j, kind in enumerate(axis_kind) if kind != _NON_CARRIAGEWAY]
    unaccounted_length = 0.0
    for j in drivable:
        done = accounted.get(j)
        rest = axis_geoms[j]
        if done:
            rest = rest.difference(unary_union(done).buffer(boundary_tolerance_m))
        for piece in _lines(rest):
            if piece.length > max(length_tolerance_m, boundary_tolerance_m):
                unaccounted_length += piece.intersection(bounds).length
                exclude("axis_without_unit", piece.buffer(exclusion_buffer_m))
    structure_guard = (
        unary_union(structure_geoms).buffer(boundary_tolerance_m) if structure_geoms else None
    )
    if structure_guard is not None:
        shapely.prepare(structure_guard)
    conflict_points: list[Point] = []
    drivable_geoms = [axis_geoms[j] for j in drivable]
    if drivable_geoms:
        left, right = STRtree(drivable_geoms).query(drivable_geoms, predicate="crosses")
        for a, b in zip(left, right):
            if a >= b:
                continue
            nodes = _endpoints(drivable_geoms[a]) + _endpoints(drivable_geoms[b])
            for point in _points(drivable_geoms[a].intersection(drivable_geoms[b])):
                if not any(point.distance(node) <= length_tolerance_m for node in nodes):
                    conflict_points.append(point)
        for geometry in drivable_geoms:
            conflict_points.extend(_self_crossings(geometry))
    conflicts = 0
    for point in conflict_points:
        if structure_guard is not None and structure_guard.contains(point):
            continue
        conflicts += bounds.contains(point)
        exclude("nodeless_crossing_outside_structure", point.buffer(exclusion_buffer_m))

    # --- Assemble -----------------------------------------------------------
    if rows:
        table = pd.DataFrame(rows).sort_values("road_id", kind="stable").reset_index(drop=True)
        crossings = gpd.GeoDataFrame(
            table[list(_COLUMNS)], geometry=gpd.GeoSeries(table.geometry, crs=crs), crs=crs
        )
        crossings["axis"] = gpd.GeoSeries(table.axis, crs=crs, index=crossings.index)
    else:
        crossings = gpd.GeoDataFrame(
            {
                column: pd.Series(dtype=bool if column == "complex_crossing" else object)
                for column in _COLUMNS
            },
            geometry=gpd.GeoSeries([], crs=crs),
            crs=crs,
        )
        crossings["axis"] = gpd.GeoSeries([], crs=crs, index=crossings.index)
    crossings["complex_crossing"] = crossings["complex_crossing"].astype(bool)

    kept = [
        (reason, area)
        for reason, geometry in exclusions
        if (area := _polygonal(geometry.intersection(bounds))) is not None
    ]
    excluded = gpd.GeoDataFrame(
        {"reason": [reason for reason, _ in kept]},
        geometry=gpd.GeoSeries([area for _, area in kept], crs=crs),
        crs=crs,
    )
    covered = _polygonal(bounds.difference(unary_union([area for _, area in kept])))
    coverage = gpd.GeoDataFrame(
        {"source": ["GRB"], "region": ["VL"]},
        geometry=gpd.GeoSeries([covered if covered is not None else Polygon()], crs=crs),
        crs=crs,
    )
    by_reason = excluded.dissolve("reason").area.to_dict() if len(excluded) else {}
    report = {
        "rows": len(crossings),
        "structure_counts": {
            str(k): int(v) for k, v in crossings.structure.value_counts().to_dict().items()
        },
        "complex_rows": int(crossings.complex_crossing.sum()),
        "wbn": wbn_report,
        "structures": structure_report,
        "coverage": {
            "bounds_area_m2": float(bounds.area),
            "covered_area_m2": float(covered.area) if covered is not None else 0.0,
            "excluded_area_m2_by_reason": {str(k): float(v) for k, v in by_reason.items()},
        },
        "axis_without_unit_m": float(unaccounted_length),
        "nodeless_crossings_outside_structures": int(conflicts),
        "thresholds": {**numbers, "complex_morf_codes": sorted(complex_codes)},
    }
    return crossings, coverage, excluded, report
