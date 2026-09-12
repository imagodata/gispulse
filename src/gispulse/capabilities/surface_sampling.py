"""Surface sampling along lines for GISPulse.

Requires optional dependencies:
    - shapely   (already a geopandas dependency)

Ported from the surface-sampling seam of an internal fibre-network project
(the geometric half of its civil-works costing), stripped of any costing:
given lines (e.g. routed traces from ``route_pairs``) and a polygon layer
carrying a class attribute (land cover, road surface, zoning…), produce for
each line the **ordered sequence of homogeneous segments**
``(length_m, class)`` it traverses.

Purely geometric linear referencing: every polygon intersection contributes a
``(start_m, end_m, class)`` interval along the line; uncovered stretches get
the fallback class; overlaps are resolved either by layer order (first wins)
or by an explicit priority list; consecutive same-class segments are merged.
No costs, no domain assumptions — converting segments to €/m (or anything
else) is the consumer's job.

Requires ``tier="pro"`` like the rest of the routing/network family it
composes with.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from typing import Any

import geopandas as gpd
from shapely import line_locate_point, points
from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.core.crs import is_angular
from gispulse.persistence.tier import check_tier

# Length tolerance (m) below which a sub-segment is ignored: swallows the
# numeric slivers produced by intersections at polygon borders.
_MIN_SEGMENT_M = 1e-6

# Column contract of the emitted segments.
_SAMPLE_COLUMNS = [
    "line_id",
    "segment_order",
    "surface_class",
    "length_m",
    "share",
    "geometry",
]


def _sub_lines(intersection: BaseGeometry) -> list[LineString]:
    """Flatten a line × polygon intersection into LineStrings (points/empties dropped)."""
    if intersection.is_empty:
        return []
    if isinstance(intersection, LineString):
        return [intersection]
    if isinstance(intersection, MultiLineString):
        return [line for line in intersection.geoms if not line.is_empty]
    geoms = getattr(intersection, "geoms", None)  # GeometryCollection
    if geoms is None:
        return []
    lines: list[LineString] = []
    for geom in geoms:
        if isinstance(geom, LineString) and not geom.is_empty:
            lines.append(geom)
        elif isinstance(geom, MultiLineString):
            lines.extend(line for line in geom.geoms if not line.is_empty)
    return lines


def _covered_intervals(
    line: LineString, surfaces: Iterable[tuple[BaseGeometry, str]]
) -> list[tuple[float, float, str]]:
    """``(start_m, end_m, class)`` intervals covered by the surfaces along the line.

    Every intersection sub-line is linearly referenced by its endpoints.
    Intervals may overlap: resolution happens downstream (:func:`_class_at`),
    driven by the insertion order of *surfaces* or an explicit priority.
    """
    intervals: list[tuple[float, float, str]] = []
    endpoint_coords: list[tuple[float, float]] = []
    endpoint_classes: list[str] = []
    for geometry, surface_class in surfaces:
        intersection = line.intersection(geometry)
        for sub in _sub_lines(intersection):
            coords = list(sub.coords)
            endpoint_coords.append((float(coords[0][0]), float(coords[0][1])))
            endpoint_coords.append((float(coords[-1][0]), float(coords[-1][1])))
            endpoint_classes.append(surface_class)
    if not endpoint_coords:
        return intervals

    projected = line_locate_point(line, points(endpoint_coords))
    for idx, surface_class in enumerate(endpoint_classes):
        start = float(projected[2 * idx])
        end = float(projected[2 * idx + 1])
        lo, hi = (start, end) if start <= end else (end, start)
        if hi - lo > _MIN_SEGMENT_M:
            intervals.append((lo, hi, surface_class))
    return intervals


def _resolve_breakpoints(
    intervals: list[tuple[float, float, str]], total_m: float
) -> list[float]:
    """Sorted boundaries (0 and total included) where the class may change."""
    marks = {0.0, total_m}
    for lo, hi, _ in intervals:
        marks.add(min(lo, total_m))
        marks.add(min(hi, total_m))
    return sorted(m for m in marks if 0.0 <= m <= total_m)


def _class_at(
    mid_m: float,
    intervals: list[tuple[float, float, str]],
    fallback_class: str,
    priority_rank: Mapping[str, int] | None,
) -> str:
    """Class covering position ``mid_m`` (overlap resolution).

    Without *priority_rank*: first covering interval wins (insertion order of
    the surfaces — the historical contract). With it: among the covering
    classes the **highest rank wins** (stable tie-break by class name), and a
    class absent from the mapping outranks every ranked one — deliberately
    conservative in the source project (an unranked cover is treated as the
    most important one rather than silently losing).
    """
    covering = [cls for lo, hi, cls in intervals if lo <= mid_m <= hi]
    if not covering:
        return fallback_class
    if priority_rank is None:
        return covering[0]
    default_rank = len(priority_rank)
    return max(covering, key=lambda cls: (priority_rank.get(cls, default_rank), cls))


def _merged_segments(
    line: LineString,
    intervals: list[tuple[float, float, str]],
    fallback_class: str,
    priority_rank: Mapping[str, int] | None,
) -> list[tuple[float, float, str]]:
    """Ordered homogeneous ``(start_m, end_m, class)`` runs along the line."""
    total_m = line.length
    breakpoints = _resolve_breakpoints(intervals, total_m)
    merged: list[tuple[float, float, str]] = []
    for lo, hi in pairwise(breakpoints):
        if hi - lo <= _MIN_SEGMENT_M:
            continue
        cls = _class_at((lo + hi) / 2.0, intervals, fallback_class, priority_rank)
        if merged and merged[-1][2] == cls and abs(merged[-1][1] - lo) <= _MIN_SEGMENT_M:
            merged[-1] = (merged[-1][0], hi, cls)
        else:
            merged.append((lo, hi, cls))
    return merged


def _iter_line_parts(geom: Any) -> Iterable[LineString]:
    if geom is None or geom.is_empty:
        return
    parts = geom.geoms if isinstance(geom, MultiLineString) else [geom]
    for part in parts:
        if isinstance(part, LineString) and len(part.coords) >= 2:
            yield part


@register
class SampleSurfaceAlongLinesCapability(Capability):
    """Segmente chaque ligne par classe de surface traversée (référencement linéaire)."""

    name = "sample_surface_along_lines"
    description = (
        "Splits every line into its ordered sequence of homogeneous segments "
        "by the surface class it crosses (land cover, road surface, "
        "zoning...): the polygon layer comes in as ref_layer with a class "
        "column. Uncovered stretches get fallback_class; overlaps are "
        "resolved by layer order (first wins) or by an explicit priority "
        "list (last entry wins; unlisted classes outrank listed ones). "
        "Purely geometric — one row per segment with length_m, share and the "
        "sub-line geometry. Composes with route_pairs to classify routed "
        "traces."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        class_col: str = "surface_class",
        fallback_class: str = "unclassified",
        priority: Sequence[str] | None = None,
        crs_meters: str | None = None,
        **_: Any,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:            Lignes à segmenter (LineString / MultiLineString ;
                            p.ex. traces de ``route_pairs``).
            ref_gdf:        Couche surfacique (via ``ref_layer``) : polygones +
                            colonne de classe.
            class_col:      Colonne de classe de la couche surfacique.
            fallback_class: Classe des portions non couvertes (trou de
                            couverture). Jamais devinée : défaut explicite
                            ``"unclassified"``.
            priority:       Résolution des recouvrements. ``null`` (défaut) :
                            première couche gagnante (ordre de la couche).
                            Sinon liste de classes, la **dernière** l'emporte ;
                            une classe absente de la liste l'emporte sur
                            toutes les classes listées (garde-fou hérité du
                            projet source : un recouvrement non classé ne perd
                            jamais en silence).
            crs_meters:     CRS métrique de travail pour couches angulaires
                            (défaut EPSG:3857) — les longueurs sont en mètres.

        Returns:
            GeoDataFrame, une ligne par tronçon homogène : ``line_id``
            (position de la ligne d'entrée), ``segment_order`` (0..n par
            ligne), ``surface_class``, ``length_m``, ``share`` (part de la
            longueur totale de la ligne), ``geometry`` (sous-ligne du
            tronçon). La somme des ``length_m`` d'une ligne égale sa longueur
            (aux slivers numériques près). Reprojeté vers le CRS d'origine.

        Raises:
            ValueError: couche surfacique absente ou ``class_col`` inconnue.
        """
        check_tier("pro")

        if ref_gdf is None:
            raise ValueError(
                "sample_surface_along_lines requires a polygon layer "
                "(inject via ref_layer)."
            )
        if class_col not in ref_gdf.columns:
            raise ValueError(
                f"sample_surface_along_lines: class_col {class_col!r} not in "
                "the surface layer."
            )

        original_crs = gdf.crs
        if gdf.empty:
            return gpd.GeoDataFrame(columns=_SAMPLE_COLUMNS, crs=original_crs)

        reproject = is_angular(gdf)
        effective_crs = crs_meters or "EPSG:3857"
        lines_m = gdf.to_crs(effective_crs) if reproject else gdf
        working_crs = lines_m.crs
        surfaces_m = (
            ref_gdf.to_crs(working_crs)
            if ref_gdf.crs is not None and ref_gdf.crs != working_crs
            else ref_gdf
        )

        priority_rank: dict[str, int] | None = None
        if priority is not None:
            priority_rank = {str(cls): rank for rank, cls in enumerate(priority)}

        # (geometry, class) candidates in layer order — the order carries the
        # overlap priority when no explicit priority list is given.
        surface_rows: list[tuple[BaseGeometry, str]] = [
            (geom, str(cls))
            for geom, cls in zip(surfaces_m.geometry, surfaces_m[class_col])
            if geom is not None and not geom.is_empty
        ]
        sindex = surfaces_m.sindex if surface_rows else None

        rows: list[dict[str, Any]] = []
        for line_id, geom in enumerate(lines_m.geometry):
            parts = list(_iter_line_parts(geom))
            if not parts:
                continue
            total_m = sum(part.length for part in parts)
            if total_m <= _MIN_SEGMENT_M:
                continue
            order = 0
            for part in parts:
                if sindex is not None:
                    hits = sorted(sindex.query(part, predicate="intersects"))
                    candidates = [
                        (surfaces_m.geometry.iloc[i], str(surfaces_m[class_col].iloc[i]))
                        for i in hits
                        if surfaces_m.geometry.iloc[i] is not None
                        and not surfaces_m.geometry.iloc[i].is_empty
                    ]
                else:
                    candidates = []
                intervals = _covered_intervals(part, candidates)
                for lo, hi, cls in _merged_segments(
                    part, intervals, fallback_class, priority_rank
                ):
                    rows.append(
                        {
                            "line_id": line_id,
                            "segment_order": order,
                            "surface_class": cls,
                            "length_m": hi - lo,
                            "share": (hi - lo) / total_m,
                            "geometry": substring(part, lo, hi),
                        }
                    )
                    order += 1

        if not rows:
            return gpd.GeoDataFrame(columns=_SAMPLE_COLUMNS, crs=original_crs)

        result = gpd.GeoDataFrame(rows, geometry="geometry", crs=working_crs)
        if reproject and original_crs is not None:
            result = result.to_crs(original_crs)
        return result.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": ["string", "null"],
                    "description": "Polygon surface layer carrying the class column.",
                },
                "class_col": {
                    "type": "string",
                    "default": "surface_class",
                    "description": "Class column on the surface layer.",
                },
                "fallback_class": {
                    "type": "string",
                    "default": "unclassified",
                    "description": "Class assigned to uncovered stretches.",
                },
                "priority": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "Overlap resolution: null = first layer wins (layer "
                        "order); else the last listed class wins, and an "
                        "unlisted class outranks the listed ones."
                    ),
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Metric working CRS for angular layers. Use EPSG:2154 in France.",
                },
            },
        }
