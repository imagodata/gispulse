"""Build the road-crossings level-evidence artifact from PICC road surfaces and axes.

Same output contract as ``grb_road_crossings`` (one row per road unit,
EPSG:31370, footprint plus a second ``axis`` geometry column, ``road_id``,
``structure``, ``complex_crossing``, provenance; a separate coverage polygon).

Evidence rules (PICC conceptual data model, table ``VOIRIE_SURFACE``, as
recorded in the PICC plugin README: ``NIVEAU`` there is absolute — "0 si
surface sol, +x si surfaces au dessus = pont, -x si surfaces en dessous =
tunnel"; on ``VOIRIE_AXE`` it is only relative and, measured, always empty):

- ``grade_separated``: touching ``Ouvrage d'art`` surfaces with ``NIVEAU`` ≥ 1,
  a bridge deck, whose level is stated by the source. A deck takes priority
  over the ground surface it covers: a trench following the road on the deck
  never reaches the road underneath (the same reading as the GRB, which draws
  no wegbaan under a deck).
- ``ground``: ``Ouvrage d'art`` surfaces with ``NIVEAU`` 0 (stated ground: the
  PICC draws them exactly under bridge decks and above tunnels), and
  ``Tronçon``/``Carrefour``/``Aire de repos`` surfaces whose ``NIVEAU`` is
  empty. For those, ground rests on the PICC being a continuous inventory in
  which every bridge or tunnel surface is an ``Ouvrage d'art`` — a closed-world
  reading, accepted explicitly (decision D5), not a stated level. A ``NIVEAU``
  present but unreadable is never read as empty.
- ``unknown``: touching ``Ouvrage d'art`` surfaces with ``NIVEAU`` ≤ -1, a
  tunnel, with every carriageway axis inside, taking priority over the ground
  surfaces they underlie. The tunnel's level is stated, but axes carry none
  (``NIVEAU`` empty on ``VOIRIE_AXE``) and no official link to their surface:
  inside a tunnel footprint, the tunnel's own axis cannot be told from a street
  passing above it. Marked ``grade_separated``, a street above would be bored
  as zero; left to the ground surface above, the tunnel's axis would be bored
  as a ground crossing. ``unknown`` asks the consumer for proof instead, and the
  footprint leaves coverage.

Axes are clipped to footprints geometrically and sorted by ``NATUR_DESC``:
``Communale``, ``Nationale``, ``Autoroute`` and ``Ring`` are carriageways;
``Piste cyclable``, ``Chemin ou sentier`` and ``Sentier`` are not (``Chemin ou
sentier`` follows the decision taken for the GRB aardeweg); anything else is
unresolved. ``complex_crossing`` is true for an ``Autoroute`` axis by default.

Coverage exclusions follow ``road_crossings_common``: unresolved axes and
surfaces, footprints leaving the acquisition area, drivable axis length no
unit accounts for, node-less crossings outside structures, and every tunnel
footprint.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import geopandas as gpd
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from gispulse.capabilities.vector.reconcile_knw_structures import _code, _nonempty_id_column
from gispulse.capabilities.vector.road_crossings_common import (
    CARRIAGEWAY,
    NON_CARRIAGEWAY,
    UNRESOLVED,
    CrossingsBuilder,
    GroundElement,
    group_touching,
    polygonal,
    validate_numbers,
)

_STRUCTURE_NATURE = "Ouvrage d'art"
_GROUND_NATURES = frozenset({"Tronçon", "Carrefour", "Aire de repos"})
_SEGMENT_NATURE = "Tronçon"
_CARRIAGEWAY_NATURES = frozenset({"Communale", "Nationale", "Autoroute", "Ring"})
_NON_CARRIAGEWAY_NATURES = frozenset({"Piste cyclable", "Chemin ou sentier", "Sentier"})
_COMPLEX_NATURES = ("Autoroute",)
_EMPTY, _INVALID = "empty", "invalid"


def _label(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _level(value):
    """``NIVEAU`` as an int, ``_EMPTY`` when absent, ``_INVALID`` when unreadable."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return _EMPTY
    if isinstance(value, str) and not value.strip():
        return _EMPTY
    code = _code(value)
    return _INVALID if code is None else code


def build_picc_road_crossings(
    surfaces: gpd.GeoDataFrame,
    axes: gpd.GeoDataFrame,
    *,
    coverage_area,
    length_tolerance_m: float,
    area_tolerance_m2: float,
    boundary_tolerance_m: float,
    exclusion_buffer_m: float,
    surface_id: str = "GEOREF_ID",
    axis_id: str = "GEOREF_ID",
    nature_field: str = "NATUR_DESC",
    level_field: str = "NIVEAU",
    complex_natures: Iterable[str] = _COMPLEX_NATURES,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, dict]:
    """Return ``(crossings, coverage, exclusions, report)`` — see the module docstring.

    ``coverage_area`` is the acquisition area in EPSG:31370 (a polygon: the PICC
    is queried by a WGS84 envelope, whose projection is not a rectangle).
    Tolerances and ``exclusion_buffer_m`` mean what they mean for
    ``build_grb_road_crossings``. An empty layer may lack its property columns.
    """
    numbers = {
        "length_tolerance_m": length_tolerance_m,
        "area_tolerance_m2": area_tolerance_m2,
        "boundary_tolerance_m": boundary_tolerance_m,
        "exclusion_buffer_m": exclusion_buffer_m,
    }
    validate_numbers("PICC_CROSSINGS", coverage_area, numbers)
    if any(f.crs is None or f.crs.to_epsg() != 31370 for f in (surfaces, axes)):
        raise ValueError("PICC_CROSSINGS_CRS_INVALID: EPSG:31370 required on every layer")
    for frame, fields, allowed, id_field in [
        (surfaces, (surface_id, nature_field, level_field), (Polygon, MultiPolygon), surface_id),
        (axes, (axis_id, nature_field), (LineString, MultiLineString), axis_id),
    ]:
        if len(frame) == 0:
            continue
        missing = [name for name in fields if name not in frame]
        if missing:
            raise ValueError(f"PICC_CROSSINGS_FIELD_MISSING: {missing}")
        if any(not isinstance(g, allowed) or g.is_empty or not g.is_valid for g in frame.geometry):
            raise ValueError(
                "PICC_CROSSINGS_GEOMETRY_INVALID: repair source geometries explicitly first"
            )
        if _nonempty_id_column(frame, id_field, "PICC_CROSSINGS_ID_INVALID").duplicated().any():
            raise ValueError("PICC_CROSSINGS_ID_INVALID: unique nonempty IDs required")
    complex_set = frozenset(complex_natures)

    def column(frame, name):
        return list(frame[name]) if name in frame else []

    natures = [_label(v) for v in column(axes, nature_field)]
    builder = CrossingsBuilder(
        axis_ids=[str(v) for v in column(axes, axis_id)],
        axis_geoms=list(axes.geometry),
        axis_kind=[
            CARRIAGEWAY
            if n in _CARRIAGEWAY_NATURES
            else NON_CARRIAGEWAY
            if n in _NON_CARRIAGEWAY_NATURES
            else UNRESOLVED
            for n in natures
        ],
        axis_codes=natures,
        axis_complex=[n in complex_set for n in natures],
        crs=surfaces.crs,
        coverage_area=coverage_area,
        numbers=numbers,
        source="PICC",
        region="WA",
    )

    # --- Surfaces by stated or closed-world level ---------------------------
    ids = [str(v) for v in column(surfaces, surface_id)]
    geoms = list(surfaces.geometry)
    surface_natures = [_label(v) for v in column(surfaces, nature_field)]
    levels = [_level(v) for v in column(surfaces, level_field)]
    bridges, tunnels, grounds, unresolved_surfaces = [], [], [], []
    for i, (nature, level) in enumerate(zip(surface_natures, levels)):
        numeric = isinstance(level, int)
        if nature == _STRUCTURE_NATURE and numeric and level >= 1:
            bridges.append(i)
        elif nature == _STRUCTURE_NATURE and numeric and level <= -1:
            tunnels.append(i)
        elif (nature == _STRUCTURE_NATURE and level == 0) or (
            nature in _GROUND_NATURES and level in (_EMPTY, 0)
        ):
            grounds.append(i)
        else:
            unresolved_surfaces.append(i)
    surface_report = {
        "surfaces": len(ids),
        "bridge_surfaces": len(bridges),
        "tunnel_surfaces": len(tunnels),
        "ground_surfaces": len(grounds),
        "unresolved_surfaces": len(unresolved_surfaces),
        "ground_elements": 0,
        "ground_units": 0,
        "non_carriageway": 0,
        "unresolved": 0,
        "with_unresolved_axes": 0,
        "without_axis": 0,
        "overlap_trimmed": 0,
    }
    for i in unresolved_surfaces:
        builder.exclude("surface_level_unresolved", geoms[i])

    structure_report = {
        "bridges": 0,
        "tunnels_unknown": 0,
        "without_carriageway": 0,
        "unresolved": 0,
    }
    structure_geoms: list = []
    # Both decks and tunnels take priority over the ground surfaces they cover.
    priority_geoms: list = []
    for kind, positions in (("bridge", bridges), ("tunnel", tunnels)):
        for group in group_touching([geoms[i] for i in positions], boundary_tolerance_m):
            members = sorted(ids[positions[g]] for g in group)
            footprint = polygonal(unary_union([geoms[positions[g]] for g in group]))
            structure_geoms.append(footprint)
            priority_geoms.append(footprint)
            builder.check_bounds(footprint)
            if kind == "tunnel":
                builder.exclude("tunnel_axis_level_unknown", footprint)
            found = builder.by_kind(builder.pieces(footprint))
            if found[UNRESOLVED]:
                builder.exclude("structure_axes_unresolved", footprint)
                builder.account(found[UNRESOLVED])
            if not found[CARRIAGEWAY]:
                structure_report["unresolved" if found[UNRESOLVED] else "without_carriageway"] += 1
                continue
            road_id = "PICC:OVA:" + "+".join(members)
            if kind == "bridge":
                structure_report["bridges"] += 1
                evidence = f"OVA {'+'.join(members)} NIVEAU ≥ 1 (bridge deck)"
                builder.add_row(
                    road_id,
                    "grade_separated",
                    "VOIRIE_SURFACE",
                    members,
                    footprint,
                    found[CARRIAGEWAY],
                    evidence,
                )
            else:
                structure_report["tunnels_unknown"] += 1
                evidence = f"OVA {'+'.join(members)} NIVEAU ≤ -1 (tunnel): axis level unknown"
                builder.add_row(
                    road_id,
                    "unknown",
                    "VOIRIE_SURFACE",
                    members,
                    footprint,
                    found[CARRIAGEWAY],
                    evidence,
                )

    # --- Ground surfaces: structures take priority, then the earlier surface -
    order = sorted(grounds, key=lambda i: (len(ids[i]), ids[i]))
    ground_tree, priority_tree = STRtree(geoms), STRtree(priority_geoms)
    claimed: set[int] = set()
    elements: list[GroundElement] = []
    for i in order:
        footprint = geoms[i]
        overlapping = [priority_geoms[int(k)] for k in priority_tree.query(footprint)] + [
            geoms[j] for j in (int(k) for k in ground_tree.query(footprint)) if j in claimed
        ]
        trimmed = footprint
        for other in overlapping:
            if trimmed.intersection(other).area > area_tolerance_m2:
                trimmed = trimmed.difference(other)
        claimed.add(i)
        if trimmed is not footprint:
            surface_report["overlap_trimmed"] += 1
            trimmed = polygonal(trimmed)
            if trimmed is None or trimmed.area <= area_tolerance_m2:
                continue
        builder.check_bounds(trimmed)
        found = builder.by_kind(builder.pieces(trimmed))
        if found[UNRESOLVED]:
            builder.account(found[UNRESOLVED])
            if found[CARRIAGEWAY]:
                builder.exclude_buffers("surface_axes_unresolved", found[UNRESOLVED])
            else:
                builder.exclude("surface_axes_unresolved", trimmed)
        if found[CARRIAGEWAY]:
            surface_report["ground_elements"] += 1
            surface_report["with_unresolved_axes"] += bool(found[UNRESOLVED])
            mergeable = surface_natures[i] == _SEGMENT_NATURE and not builder.has_stop_node(trimmed)
            elements.append(
                GroundElement(ids[i], trimmed, found[CARRIAGEWAY], mergeable, (len(ids[i]), ids[i]))
            )
        elif found[UNRESOLVED]:
            surface_report["unresolved"] += 1
        elif found[NON_CARRIAGEWAY]:
            surface_report["non_carriageway"] += 1
        else:
            surface_report["without_axis"] += 1
            builder.exclude("surface_without_axis", trimmed)
    surface_report["ground_units"] = builder.add_ground_units(
        elements, "PICC:", "VOIRIE_SURFACE", "sol"
    )

    crossings, coverage, excluded, report = builder.finish(structure_geoms)
    report.update(
        surfaces=surface_report,
        structures=structure_report,
        thresholds={**numbers, "complex_natures": sorted(complex_set)},
        axis_classes={
            "carriageway_natures": sorted(_CARRIAGEWAY_NATURES),
            "non_carriageway_natures": sorted(_NON_CARRIAGEWAY_NATURES),
        },
    )
    return crossings, coverage, excluded, report
