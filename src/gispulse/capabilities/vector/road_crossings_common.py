"""Shared assembly for road-crossings level-evidence artifacts (GRB, PICC, UrbIS).

A regional module decides, from its own cited source rules, which axes are
carriageways and which footprints are ``ground``, ``grade_separated`` or
``unknown``. This module owns everything that must behave identically across
regions: clipping axes to footprints, merging the fragments of one
carriageway, accounting for every drivable axis, coverage exclusions and the
output contract (one row per road unit, EPSG:31370, footprint as the active
geometry, a second ``axis`` geometry column).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import geopandas as gpd
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import linemerge, unary_union

CARRIAGEWAY, NON_CARRIAGEWAY, UNRESOLVED = "carriageway", "non_carriageway", "unresolved"
COLUMNS = (
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


def lines(geometry) -> list[LineString]:
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    return [line for part in getattr(geometry, "geoms", ()) for line in lines(part)]


def points(geometry) -> list[Point]:
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Point):
        return [geometry]
    return [point for part in getattr(geometry, "geoms", ()) for point in points(part)]


def polygonal(geometry) -> Polygon | MultiPolygon | None:
    parts = [
        polygon
        for part in getattr(geometry, "geoms", [geometry])
        for polygon in getattr(part, "geoms", [part])
        if isinstance(polygon, Polygon) and not polygon.is_empty
    ]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def endpoints(geometry) -> list[Point]:
    return [Point(line.coords[i]) for line in lines(geometry) for i in (0, -1)]


def pieces_inside(line, polygon, tolerance: float) -> list[LineString]:
    """Axis pieces genuinely inside ``polygon``.

    A tangent contact yields a point and a run along the boundary has its
    midpoint on the boundary: neither is a piece of this footprint's axis.
    """
    return [
        piece
        for piece in lines(line.intersection(polygon))
        if piece.length > tolerance and polygon.contains(piece.interpolate(0.5, normalized=True))
    ]


def simple_axes(pieces: Iterable[LineString]) -> list[LineString]:
    """Join contiguous pieces (degree-2 nodes only, junctions stay split);
    never emit a self-intersecting line."""
    out: list[LineString] = []
    for line in lines(linemerge(list(pieces))):
        out.extend([line] if line.is_simple else lines(shapely.node(line)))
    return out


def self_crossings(line) -> list[Point]:
    """Points where a non-simple axis crosses itself (not its own ends)."""
    if line.is_simple:
        return []
    ends = {(p.x, p.y) for p in endpoints(line)}
    return [
        p
        for p in {(q.x, q.y): q for q in endpoints(shapely.node(line))}.values()
        if (p.x, p.y) not in ends
    ]


class DisjointSets:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        self.parent[self.find(left)] = self.find(right)


def group_touching(geometries: Sequence, distance: float) -> list[list[int]]:
    """Positions of ``geometries`` grouped by transitive contact within ``distance``."""
    groups = DisjointSets(len(geometries))
    if geometries:
        left, right = STRtree(list(geometries)).query(
            list(geometries), predicate="dwithin", distance=distance
        )
        for a, b in zip(left, right):
            groups.union(int(a), int(b))
    members: dict[int, list[int]] = {}
    for position in range(len(geometries)):
        members.setdefault(groups.find(position), []).append(position)
    return list(members.values())


def validate_numbers(prefix: str, coverage_area, numbers: dict[str, float]) -> None:
    if (
        not isinstance(coverage_area, (Polygon, MultiPolygon))
        or coverage_area.is_empty
        or not coverage_area.is_valid
    ):
        raise ValueError(f"{prefix}_EXTENT_INVALID: a valid nonempty EPSG:31370 area is required")
    if any(not math.isfinite(v) or v < 0 for v in numbers.values()) or not (
        numbers["exclusion_buffer_m"] > 0
    ):
        raise ValueError(
            f"{prefix}_TOLERANCE_INVALID: nonnegative finite tolerances and a positive "
            "exclusion buffer required"
        )


@dataclass
class GroundElement:
    """A ground footprint and the carriageway axis pieces it carries."""

    element_id: str
    footprint: Polygon | MultiPolygon
    carriageway: dict[int, list[LineString]]
    mergeable: bool
    order: tuple = field(default=())


class CrossingsBuilder:
    """Collect rows and coverage exclusions, then assemble the published frames."""

    def __init__(
        self,
        *,
        axis_ids: Sequence[str],
        axis_geoms: Sequence,
        axis_kind: Sequence[str],
        axis_codes: Sequence[str],
        axis_complex: Sequence[bool],
        crs,
        coverage_area,
        numbers: dict[str, float],
        source: str,
        region: str,
    ) -> None:
        self.axis_ids = list(axis_ids)
        self.axis_geoms = list(axis_geoms)
        self.axis_kind = list(axis_kind)
        self.axis_codes = list(axis_codes)
        self.axis_complex = list(axis_complex)
        self.tree = STRtree(self.axis_geoms)
        self.crs = crs
        self.coverage_area = coverage_area
        self.numbers = dict(numbers)
        self.length_tol = numbers["length_tolerance_m"]
        self.area_tol = numbers["area_tolerance_m2"]
        self.boundary_tol = numbers["boundary_tolerance_m"]
        self.buffer = numbers["exclusion_buffer_m"]
        self.source, self.region = source, region
        self.inside_area = coverage_area.buffer(self.boundary_tol)
        shapely.prepare(self.inside_area)
        # Only a node where exactly two axis ends meet merely carries a road on;
        # three or more ends make a junction, a single end a dead end.
        degree = Counter(
            (round(p.x, 3), round(p.y, 3)) for g in self.axis_geoms for p in endpoints(g)
        )
        self.stop_nodes = [Point(xy) for xy, count in degree.items() if count != 2]
        self.stop_tree = STRtree(self.stop_nodes)
        self.rows: list[dict] = []
        self.exclusions: list[tuple[str, object]] = []
        # Axis pieces some unit accounts for (a row, or an excluded footprint);
        # whatever drivable length is left over gets a coverage hole.
        self.accounted: dict[int, list[LineString]] = {}

    # --- building blocks -----------------------------------------------------
    def pieces(self, footprint, keep: Callable[[int], bool] | None = None):
        found: dict[int, list[LineString]] = {}
        for j in sorted(int(i) for i in self.tree.query(footprint)):
            if keep is not None and not keep(j):
                continue
            inside = pieces_inside(self.axis_geoms[j], footprint, self.length_tol)
            if inside:
                found[j] = inside
        return found

    def by_kind(self, found: dict[int, list[LineString]]):
        split: dict[str, dict[int, list[LineString]]] = {
            CARRIAGEWAY: {},
            NON_CARRIAGEWAY: {},
            UNRESOLVED: {},
        }
        for j, pieces in found.items():
            split[self.axis_kind[j]][j] = pieces
        return split

    def account(self, found: dict[int, list[LineString]]) -> None:
        for j, pieces in found.items():
            self.accounted.setdefault(j, []).extend(pieces)

    def exclude(self, reason: str, geometry) -> None:
        self.exclusions.append((reason, geometry))

    def exclude_buffers(self, reason: str, found: dict[int, list[LineString]]) -> None:
        for pieces in found.values():
            for piece in pieces:
                self.exclude(reason, piece.buffer(self.buffer))

    def check_bounds(self, footprint) -> None:
        """Axes entirely outside the acquisition area were never fetched: the
        verdict of a footprint reaching out there cannot be trusted inside."""
        if not self.inside_area.contains(footprint):
            self.exclude("footprint_crosses_bounds", footprint)

    def has_stop_node(self, footprint) -> bool:
        return any(
            footprint.contains(self.stop_nodes[int(k)]) for k in self.stop_tree.query(footprint)
        )

    def is_complex(self, found: dict[int, list[LineString]]) -> bool:
        return any(self.axis_complex[j] for j in found)

    def add_row(self, road_id, structure, layer, source_ids, footprint, found, evidence) -> None:
        axes = simple_axes(line for j in sorted(found) for line in found[j])
        codes = sorted({self.axis_codes[j] for j in found if self.axis_codes[j]})
        self.rows.append(
            {
                "road_id": road_id,
                "structure": structure,
                "complex_crossing": self.is_complex(found),
                "structure_evidence": evidence,
                "source": self.source,
                "source_layer": layer,
                "source_ids": "+".join(source_ids),
                "axis_ids": ",".join(sorted({self.axis_ids[j] for j in found})),
                "morf_codes": ",".join(codes),
                "geometry": footprint,
                "axis": axes[0] if len(axes) == 1 else MultiLineString(axes),
            }
        )
        self.account(found)

    def add_ground_units(
        self,
        elements: Sequence[GroundElement],
        prefix: str,
        layer: str,
        evidence: str,
        keep: Callable[[int], bool] | None = None,
    ) -> int:
        """Merge touching mergeable elements sharing a carriageway axis and the
        same category into one unit, then emit one ``ground`` row per unit.

        Every carriageway axis (passing ``keep``) is clipped again against the
        united footprint, so that a carriageway crossing a fragment boundary
        keeps one continuous axis, and one lying exactly on that boundary is
        inside the unit.
        """
        elements = sorted(elements, key=lambda e: e.order or (len(e.element_id), e.element_id))
        complex_flags = [self.is_complex(e.carriageway) for e in elements]
        fragments = DisjointSets(len(elements))
        if elements:
            left, right = STRtree([e.footprint for e in elements]).query(
                [e.footprint for e in elements], predicate="dwithin", distance=self.boundary_tol
            )
            for a, b in zip(left, right):
                a, b = int(a), int(b)
                if (
                    a < b
                    and elements[a].mergeable
                    and elements[b].mergeable
                    and complex_flags[a] == complex_flags[b]
                    and set(elements[a].carriageway) & set(elements[b].carriageway)
                ):
                    fragments.union(a, b)
        units: dict[int, list[int]] = {}
        for position in range(len(elements)):
            units.setdefault(fragments.find(position), []).append(position)
        for unit in units.values():
            ids = [elements[p].element_id for p in unit]
            footprint = polygonal(unary_union([elements[p].footprint for p in unit]))
            found = self.by_kind(self.pieces(footprint, keep=keep))[CARRIAGEWAY]
            self.add_row(
                f"{prefix}{ids[0]}",
                "ground",
                layer,
                ids,
                footprint,
                found,
                f"{layer} {'+'.join(ids)} {evidence}",
            )
        return len(units)

    # --- assembly ------------------------------------------------------------
    def finish(self, structure_geoms: Sequence) -> tuple:
        """Return ``(crossings, coverage, exclusions, report)``.

        ``structure_geoms`` are the footprints inside which a node-less crossing
        of drivable axes is an expected level relation, not a conflict.
        """
        drivable = [j for j, kind in enumerate(self.axis_kind) if kind != NON_CARRIAGEWAY]
        unaccounted_length = 0.0
        for j in drivable:
            done = self.accounted.get(j)
            rest = self.axis_geoms[j]
            if done:
                rest = rest.difference(unary_union(done).buffer(self.boundary_tol))
            for piece in lines(rest):
                if piece.length > max(self.length_tol, self.boundary_tol):
                    unaccounted_length += piece.intersection(self.coverage_area).length
                    self.exclude("axis_without_unit", piece.buffer(self.buffer))
        guard = (
            unary_union(list(structure_geoms)).buffer(self.boundary_tol)
            if structure_geoms
            else None
        )
        if guard is not None:
            shapely.prepare(guard)
        conflict_points: list[Point] = []
        drivable_geoms = [self.axis_geoms[j] for j in drivable]
        if drivable_geoms:
            left, right = STRtree(drivable_geoms).query(drivable_geoms, predicate="crosses")
            for a, b in zip(left, right):
                if a >= b:
                    continue
                nodes = endpoints(drivable_geoms[a]) + endpoints(drivable_geoms[b])
                for point in points(drivable_geoms[a].intersection(drivable_geoms[b])):
                    if not any(point.distance(node) <= self.length_tol for node in nodes):
                        conflict_points.append(point)
            for geometry in drivable_geoms:
                conflict_points.extend(self_crossings(geometry))
        conflicts = 0
        for point in conflict_points:
            if guard is not None and guard.contains(point):
                continue
            conflicts += self.coverage_area.contains(point)
            self.exclude("nodeless_crossing_outside_structure", point.buffer(self.buffer))

        crs = self.crs
        if self.rows:
            table = (
                pd.DataFrame(self.rows).sort_values("road_id", kind="stable").reset_index(drop=True)
            )
            crossings = gpd.GeoDataFrame(
                table[list(COLUMNS)], geometry=gpd.GeoSeries(table.geometry, crs=crs), crs=crs
            )
            crossings["axis"] = gpd.GeoSeries(table.axis, crs=crs, index=crossings.index)
        else:
            crossings = gpd.GeoDataFrame(
                {
                    column: pd.Series(dtype=bool if column == "complex_crossing" else object)
                    for column in COLUMNS
                },
                geometry=gpd.GeoSeries([], crs=crs),
                crs=crs,
            )
            crossings["axis"] = gpd.GeoSeries([], crs=crs, index=crossings.index)
        crossings["complex_crossing"] = crossings["complex_crossing"].astype(bool)

        area = self.coverage_area
        kept = [
            (reason, part)
            for reason, geometry in self.exclusions
            if (part := polygonal(geometry.intersection(area))) is not None
        ]
        excluded = gpd.GeoDataFrame(
            {"reason": [reason for reason, _ in kept]},
            geometry=gpd.GeoSeries([part for _, part in kept], crs=crs),
            crs=crs,
        )
        covered = polygonal(area.difference(unary_union([part for _, part in kept])))
        coverage = gpd.GeoDataFrame(
            {"source": [self.source], "region": [self.region]},
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
            "coverage": {
                "bounds_area_m2": float(area.area),
                "covered_area_m2": float(covered.area) if covered is not None else 0.0,
                "excluded_area_m2_by_reason": {str(k): float(v) for k, v in by_reason.items()},
            },
            "axis_without_unit_m": float(unaccounted_length),
            "nodeless_crossings_outside_structures": int(conflicts),
        }
        return crossings, coverage, excluded, report
