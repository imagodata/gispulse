"""Build the road-crossings level-evidence artifact from UrbIS surfaces, axes and structures.

Same output contract as ``grb_road_crossings`` (one row per road unit,
EPSG:31370, footprint plus a second ``axis`` geometry column, ``road_id``,
``structure``, ``complex_crossing``, provenance; a separate coverage polygon).

UrbIS ``LVL`` is relative everywhere — "par rapport à un axe qui lui est
sécant" on ``StreetAxes``, "par rapport à un autre objet surfacique" on
``StreetSurfaces``/``Bridges``/``Tunnels`` (Paradigm product specifications) —
so a level alone never proves anything. The rules pair it with the official
structure polygons, as decided (D5):

- ``grade_separated``: a group of touching road bridges (``Bridges`` ``TYPE``
  ``ROB``) with every carriageway axis inside, or of touching road tunnels
  (``Tunnels`` ``TYPE`` ``ROT``) with the carriageway axes whose ``LVL`` is
  below 0 inside. A railway, metro or footbridge (``RAB``/``MB``/``PB``) and a
  rail or metro tunnel leave the road underneath or above at ground.
- ``ground``: carriageway surfaces (``StreetSurfaces`` ``TYPE`` ``S``, ``I``,
  ``C``, ``SC``, ``IC``) at ``LVL`` 0, carrying the carriageway axes at ``LVL``
  0 only. A road bridge takes priority over the surface it covers (a trench
  following the road on the deck never reaches the road underneath, the same
  reading as the GRB and the PICC); a tunnel does not (the road above it is
  crossed at ground level, and the tunnel's own axes, below 0, are kept apart).
- A carriageway surface whose ``LVL`` is not 0 and that lies outside a matching
  structure (above 0 outside a road bridge, below 0 outside any tunnel)
  contradicts the structure layers: it leaves coverage.

Footprints are carriageway surfaces, not the whole street corridor: UrbIS
draws sidewalks (``SW``) and medians (``M``) as separate surfaces carrying no
axis. Axes are sorted by ``TYPE``: ``S``, ``I``, ``B``, ``T``, ``C``, ``SC`` and
``IC`` are carriageways; ``PT``, ``PB``, ``MT`` and ``RT`` (pedestrian, metro,
rail) are not; anything else — ramps ``A``/``AC``, galleries ``G``, places
``P``, parking ``K`` — is unresolved. ``complex_crossing`` is true for an axis
whose ``HIERARCHY`` is ``H`` (autoroute) by default.

Coverage exclusions follow ``road_crossings_common``.
"""

from __future__ import annotations

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

_CARRIAGEWAY_SURFACES = frozenset({"S", "I", "C", "SC", "IC"})
_SEGMENT_SURFACE = "S"
_CARRIAGEWAY_AXES = frozenset({"S", "I", "B", "T", "C", "SC", "IC"})
_NON_CARRIAGEWAY_AXES = frozenset({"PT", "PB", "MT", "RT"})
_ROAD_BRIDGE, _ROAD_TUNNEL = "ROB", "ROT"
_COMPLEX_HIERARCHIES = ("H",)


def _label(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def build_urbis_road_crossings(
    surfaces: gpd.GeoDataFrame,
    axes: gpd.GeoDataFrame,
    bridges: gpd.GeoDataFrame,
    tunnels: gpd.GeoDataFrame,
    *,
    coverage_area,
    length_tolerance_m: float,
    area_tolerance_m2: float,
    boundary_tolerance_m: float,
    exclusion_buffer_m: float,
    id_field: str = "INSPIRE_ID",
    type_field: str = "TYPE",
    level_field: str = "LVL",
    hierarchy_field: str = "HIERARCHY",
    complex_hierarchies: Iterable[str] = _COMPLEX_HIERARCHIES,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, dict]:
    """Return ``(crossings, coverage, exclusions, report)`` — see the module docstring.

    ``coverage_area`` is the acquisition area in EPSG:31370. Tolerances and
    ``exclusion_buffer_m`` mean what they mean for ``build_grb_road_crossings``.
    """
    numbers = {
        "length_tolerance_m": length_tolerance_m,
        "area_tolerance_m2": area_tolerance_m2,
        "boundary_tolerance_m": boundary_tolerance_m,
        "exclusion_buffer_m": exclusion_buffer_m,
    }
    validate_numbers("URBIS_CROSSINGS", coverage_area, numbers)
    frames = (surfaces, axes, bridges, tunnels)
    if any(f.crs is None or f.crs.to_epsg() != 31370 for f in frames):
        raise ValueError("URBIS_CROSSINGS_CRS_INVALID: EPSG:31370 required on every layer")
    for frame, fields, allowed in [
        (surfaces, (id_field, type_field, level_field), (Polygon, MultiPolygon)),
        (axes, (id_field, type_field, level_field, hierarchy_field), (LineString, MultiLineString)),
        (bridges, (id_field, type_field), (Polygon, MultiPolygon)),
        (tunnels, (id_field, type_field), (Polygon, MultiPolygon)),
    ]:
        if len(frame) == 0:
            continue
        missing = [name for name in fields if name not in frame]
        if missing:
            raise ValueError(f"URBIS_CROSSINGS_FIELD_MISSING: {missing}")
        if any(not isinstance(g, allowed) or g.is_empty or not g.is_valid for g in frame.geometry):
            raise ValueError(
                "URBIS_CROSSINGS_GEOMETRY_INVALID: repair source geometries explicitly first"
            )
        if _nonempty_id_column(frame, id_field, "URBIS_CROSSINGS_ID_INVALID").duplicated().any():
            raise ValueError("URBIS_CROSSINGS_ID_INVALID: unique nonempty IDs required")
    complex_set = frozenset(complex_hierarchies)

    def column(frame, name):
        return list(frame[name]) if name in frame else []

    axis_types = [_label(v) for v in column(axes, type_field)]
    axis_levels = [_code(v) for v in column(axes, level_field)]
    hierarchies = [_label(v) for v in column(axes, hierarchy_field)]
    builder = CrossingsBuilder(
        axis_ids=[str(v) for v in column(axes, id_field)],
        axis_geoms=list(axes.geometry),
        axis_kind=[
            CARRIAGEWAY
            if t in _CARRIAGEWAY_AXES
            else NON_CARRIAGEWAY
            if t in _NON_CARRIAGEWAY_AXES
            else UNRESOLVED
            for t in axis_types
        ],
        axis_codes=[f"{t}/{h}" if h else t for t, h in zip(axis_types, hierarchies)],
        axis_complex=[h in complex_set for h in hierarchies],
        crs=surfaces.crs,
        coverage_area=coverage_area,
        numbers=numbers,
        source="UrbIS",
        region="BXL",
    )

    def axes_at(level_test):
        return lambda j: axis_levels[j] is not None and level_test(axis_levels[j])

    def structure_rows(frame, structure_type, keep, label):
        rows_report = {"groups": 0, "rows": 0, "without_carriageway": 0, "unresolved": 0}
        types = [_label(v) for v in column(frame, type_field)]
        ids = [str(v) for v in column(frame, id_field)]
        positions = [i for i, t in enumerate(types) if t == structure_type]
        geoms = list(frame.geometry)
        footprints = []
        for group in group_touching([geoms[i] for i in positions], boundary_tolerance_m):
            rows_report["groups"] += 1
            members = sorted(ids[positions[g]] for g in group)
            footprint = polygonal(unary_union([geoms[positions[g]] for g in group]))
            footprints.append(footprint)
            builder.check_bounds(footprint)
            found = builder.by_kind(builder.pieces(footprint, keep=keep))
            if found[UNRESOLVED]:
                builder.exclude("structure_axes_unresolved", footprint)
                builder.account(found[UNRESOLVED])
            if not found[CARRIAGEWAY]:
                key = "unresolved" if found[UNRESOLVED] else "without_carriageway"
                rows_report[key] += 1
                continue
            rows_report["rows"] += 1
            builder.add_row(
                f"UrbIS:{structure_type}:" + "+".join(members),
                "grade_separated",
                label,
                members,
                footprint,
                found[CARRIAGEWAY],
                f"{label} {structure_type} {'+'.join(members)}",
            )
        return footprints, rows_report

    deck_geoms, bridge_report = structure_rows(bridges, _ROAD_BRIDGE, None, "Bridges")
    tunnel_geoms, tunnel_report = structure_rows(
        tunnels, _ROAD_TUNNEL, axes_at(lambda level: level < 0), "Tunnels"
    )
    all_tunnels = list(tunnels.geometry)

    # --- Carriageway surfaces ------------------------------------------------
    ids = [str(v) for v in column(surfaces, id_field)]
    types = [_label(v) for v in column(surfaces, type_field)]
    levels = [_code(v) for v in column(surfaces, level_field)]
    geoms = list(surfaces.geometry)
    deck_tree, tunnel_tree = STRtree(deck_geoms), STRtree(all_tunnels)
    surface_report = {
        "surfaces": len(ids),
        "carriageway_surfaces": 0,
        "level_contradictions": 0,
        "ground_elements": 0,
        "ground_units": 0,
        "non_carriageway": 0,
        "unresolved": 0,
        "with_unresolved_axes": 0,
        "without_axis": 0,
        "overlap_trimmed": 0,
    }
    grounds = []
    for i, (kind, level) in enumerate(zip(types, levels)):
        if kind not in _CARRIAGEWAY_SURFACES:
            continue
        surface_report["carriageway_surfaces"] += 1
        if level == 0:
            grounds.append(i)
            continue
        # Above 0 must sit on a road bridge, below 0 inside a tunnel.
        tree, pool = (deck_tree, deck_geoms) if (level or 0) > 0 else (tunnel_tree, all_tunnels)
        inside = level is not None and any(
            geoms[i].intersection(pool[int(k)]).area >= geoms[i].area - area_tolerance_m2
            for k in tree.query(geoms[i])
        )
        if not inside:
            surface_report["level_contradictions"] += 1
            builder.exclude("surface_level_without_structure", geoms[i])

    order = sorted(grounds, key=lambda i: (len(ids[i]), ids[i]))
    surface_tree = STRtree(geoms)
    claimed: set[int] = set()
    elements: list[GroundElement] = []
    at_ground = axes_at(lambda level: level == 0)
    for i in order:
        footprint = geoms[i]
        overlapping = [deck_geoms[int(k)] for k in deck_tree.query(footprint)] + [
            geoms[j] for j in (int(k) for k in surface_tree.query(footprint)) if j in claimed
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
        found = builder.by_kind(builder.pieces(trimmed, keep=at_ground))
        if found[UNRESOLVED]:
            builder.account(found[UNRESOLVED])
            if found[CARRIAGEWAY]:
                builder.exclude_buffers("surface_axes_unresolved", found[UNRESOLVED])
            else:
                builder.exclude("surface_axes_unresolved", trimmed)
        if found[CARRIAGEWAY]:
            surface_report["ground_elements"] += 1
            surface_report["with_unresolved_axes"] += bool(found[UNRESOLVED])
            mergeable = types[i] == _SEGMENT_SURFACE and not builder.has_stop_node(trimmed)
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
        elements, "UrbIS:", "StreetSurfaces", "LVL 0", keep=at_ground
    )

    crossings, coverage, excluded, report = builder.finish(deck_geoms + tunnel_geoms)
    report.update(
        surfaces=surface_report,
        structures={"road_bridges": bridge_report, "road_tunnels": tunnel_report},
        thresholds={**numbers, "complex_hierarchies": sorted(complex_set)},
        axis_classes={
            "carriageway_axis_types": sorted(_CARRIAGEWAY_AXES),
            "non_carriageway_axis_types": sorted(_NON_CARRIAGEWAY_AXES),
            "carriageway_surface_types": sorted(_CARRIAGEWAY_SURFACES),
        },
    )
    return crossings, coverage, excluded, report
