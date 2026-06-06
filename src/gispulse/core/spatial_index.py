"""Reusable spatial index — build once, query many.

Capability K of the network/linear-referencing track. A thin, ergonomic
wrapper over :class:`shapely.STRtree` exposing the three query shapes the
rest of the engine needs:

* :meth:`SpatialIndex.query_bbox`  — features whose envelope intersects a bbox;
* :meth:`SpatialIndex.query_radius` — features within a metric radius of a point;
* :meth:`SpatialIndex.nearest`     — the *k* nearest features to a point.

The point is to stop the O(n) nearest-node scans scattered through the
network capabilities (``network._nearest_node`` ranked every node linearly
on every call). Build a :class:`SpatialIndex` over the node geometries once
and each lookup is O(log n).

The index is geometry-only and CRS-agnostic: callers reproject to a metric
CRS *before* building so that ``radius`` / distances are in meters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from shapely.geometry.base import BaseGeometry


class SpatialIndex:
    """An STRtree over a fixed list of geometries, addressable by position.

    Query methods return **integer positions** into the geometry list
    supplied at construction, so callers can map results back to their own
    rows/records.

    Example::

        idx = SpatialIndex(nodes_gdf.geometry)
        near = idx.nearest(Point(x, y))[0]      # position of the closest node
        within = idx.query_radius(pt, 50.0)     # positions within 50 m
    """

    __slots__ = ("_geoms", "_tree")

    def __init__(self, geometries: "Iterable[BaseGeometry]") -> None:
        from shapely import STRtree

        self._geoms: list[BaseGeometry] = list(geometries)
        self._tree = STRtree(self._geoms)

    @classmethod
    def build(cls, geometries: "Iterable[BaseGeometry]") -> "SpatialIndex":
        """Build an index over *geometries* (alias for the constructor)."""
        return cls(geometries)

    def __len__(self) -> int:
        return len(self._geoms)

    @property
    def geometries(self) -> "list[BaseGeometry]":
        """The indexed geometries, in position order."""
        return self._geoms

    def query_bbox(
        self, bbox: tuple[float, float, float, float]
    ) -> list[int]:
        """Return positions whose envelope intersects *bbox*.

        Args:
            bbox: ``(minx, miny, maxx, maxy)`` in the index's coordinate
                system.
        """
        from shapely.geometry import box

        minx, miny, maxx, maxy = bbox
        return [int(i) for i in self._tree.query(box(minx, miny, maxx, maxy))]

    def query_radius(self, point: "BaseGeometry", radius: float) -> list[int]:
        """Return positions of geometries within *radius* of *point*.

        Uses the tree to prune by a bounding buffer, then filters on true
        distance so the result is an exact radius query (not just bbox).

        Args:
            point:  Query geometry (typically a ``Point``).
            radius: Max distance, in the index's units. ``0`` matches only
                geometries touching *point*.
        """
        if radius < 0:
            raise ValueError("radius must be >= 0.")
        candidates = self._tree.query(point.buffer(radius) if radius > 0 else point)
        out = []
        for i in candidates:
            pos = int(i)
            if self._geoms[pos].distance(point) <= radius:
                out.append(pos)
        return out

    def nearest(self, point: "BaseGeometry", k: int = 1) -> list[int]:
        """Return the positions of the *k* nearest geometries to *point*.

        Ordered nearest-first. ``k == 1`` is the fast path (a single
        ``STRtree.nearest`` call). For ``k > 1`` the index expands a search
        window until it holds at least *k* candidates, then ranks them — so
        it stays sub-linear for the common small-*k* case.

        Args:
            point: Query geometry.
            k:     Number of neighbours to return (capped at the index size).
        """
        n = len(self._geoms)
        if n == 0:
            return []
        if k <= 1:
            return [int(self._tree.nearest(point))]

        k = min(k, n)
        near1 = int(self._tree.nearest(point))
        seed = self._geoms[near1].distance(point) or 1.0
        radius = max(seed, 1e-9)
        candidates: list[int] = []
        for _ in range(32):
            candidates = [int(i) for i in self._tree.query(point.buffer(radius))]
            if len(candidates) >= k:
                break
            radius *= 2.0
        else:
            candidates = list(range(n))
        candidates.sort(key=lambda i: self._geoms[i].distance(point))
        return candidates[:k]


__all__ = ["SpatialIndex"]
