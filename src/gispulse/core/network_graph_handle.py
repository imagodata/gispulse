"""Reusable routing handle — build a network graph once, query it many times.

Capability F of the network/linear-referencing track. Today every network
capability (``shortest_path``, ``isochrone``, ``od_matrix``,
``network_allocation``, ``mst``, ``connectivity_check``) rebuilds a NetworkX
graph from scratch on each call *and* snaps query points to nodes with an
O(n) linear scan (the old ``network._nearest_node``). For a workflow that
fires many routing queries against the same network that is wasteful twice
over.

:class:`NetworkGraph` is the persistent handle that fixes both:

* **build once** — :meth:`NetworkGraph.from_lines` constructs the
  ``networkx.Graph`` a single time (endpoints fused on 6-decimal rounding,
  byte-for-byte the behaviour of the legacy ``network._build_graph``, so
  existing results are unchanged);
* **query many** — :meth:`nearest_node` snaps a point to the closest node in
  **O(log n)** through a lazily-built
  :class:`~gispulse.core.spatial_index.SpatialIndex` (K) instead of the
  linear scan.

The handle is geometry-only and CRS-agnostic: callers reproject to a metric
CRS *before* building so edge weights and snap distances are in meters.

Example::

    graph = NetworkGraph.from_lines(network_m, weight_col="travel_time")
    src = graph.nearest_node(Point(x0, y0))   # O(log n)
    dst = graph.nearest_node(Point(x1, y1))
    path = networkx.shortest_path(graph.graph, src, dst, weight="weight")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from shapely.geometry import LineString, MultiLineString, Point

from gispulse.core.spatial_index import SpatialIndex

if TYPE_CHECKING:  # pragma: no cover - typing only
    import geopandas as gpd
    import networkx as nx


class NetworkGraph:
    """A NetworkX graph over a line layer, plus O(log n) point snapping.

    Wraps the ``networkx.Graph`` together with the ``{(x, y) -> node_id}``
    index the legacy code exposed, and adds a spatial index over the node
    geometries so :meth:`nearest_node` is sub-linear. Build once with
    :meth:`from_lines`, then reuse the same instance across every routing
    query on that network.
    """

    __slots__ = ("_graph", "_node_index", "_node_points", "_index")

    def __init__(
        self,
        graph: "nx.Graph",
        node_index: dict[tuple[float, float], int],
    ) -> None:
        self._graph = graph
        self._node_index = node_index
        # node_points[node_id] = Point(rounded coords) — dense 0..K-1 because
        # from_lines assigns ids as ``len(node_index)`` in encounter order.
        points: list[Point] = [Point(0, 0)] * len(node_index)
        for (x, y), nid in node_index.items():
            points[nid] = Point(x, y)
        self._node_points = points
        self._index: SpatialIndex | None = None  # built lazily on first snap

    @classmethod
    def from_lines(
        cls,
        gdf: "gpd.GeoDataFrame",
        weight_col: str | None = None,
        *,
        snap_decimals: int = 6,
    ) -> "NetworkGraph":
        """Build a handle from a GeoDataFrame of lines.

        Each line endpoint, rounded to *snap_decimals* decimals, becomes a
        node; each (Multi)LineString part becomes an edge. The edge
        ``weight`` is ``weight_col`` when present, else the geometric length.

        Args:
            gdf:           Line network (LineString / MultiLineString).
            weight_col:    Column used as edge weight; geometric length if
                           absent or ``None``.
            snap_decimals: Coordinate rounding used to fuse coincident
                           endpoints (default 6, matching the legacy snap).
        """
        try:
            import networkx as nx
        except ImportError as exc:  # pragma: no cover - exercised via callers
            raise ImportError(
                "Network capabilities require 'networkx'. "
                "Install with: pip install networkx"
            ) from exc

        graph = nx.Graph()
        node_index: dict[tuple[float, float], int] = {}
        has_weight = weight_col is not None and weight_col in gdf.columns

        def _node(pt: Point) -> int:
            key = (round(pt.x, snap_decimals), round(pt.y, snap_decimals))
            nid = node_index.get(key)
            if nid is None:
                nid = len(node_index)
                node_index[key] = nid
                graph.add_node(nid, x=key[0], y=key[1])
            return nid

        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            lines = geom.geoms if isinstance(geom, MultiLineString) else [geom]
            for line in lines:
                if not isinstance(line, LineString) or len(line.coords) < 2:
                    continue
                coords = list(line.coords)
                start = _node(Point(coords[0]))
                end = _node(Point(coords[-1]))
                weight = float(row[weight_col]) if has_weight else line.length
                graph.add_edge(start, end, weight=weight, geometry=line)

        return cls(graph, node_index)

    @property
    def graph(self) -> "nx.Graph":
        """The underlying ``networkx.Graph`` (nodes carry ``x``/``y``)."""
        return self._graph

    @property
    def node_index(self) -> dict[tuple[float, float], int]:
        """The legacy ``{(x, y) -> node_id}`` mapping (rounded coords)."""
        return self._node_index

    def __len__(self) -> int:
        return self._graph.number_of_nodes()

    def node_point(self, node_id: int) -> Point:
        """Return the ``Point`` of node *node_id* (rounded coords)."""
        return self._node_points[node_id]

    def nearest_node(self, point: Point) -> int:
        """Return the id of the node closest to *point* in O(log n).

        Uses a :class:`SpatialIndex` over the node geometries (built lazily
        on first call). Returns ``-1`` for an empty graph, mirroring the
        legacy ``network._nearest_node`` contract.
        """
        if not self._node_points:
            return -1
        if self._index is None:
            self._index = SpatialIndex(self._node_points)
        hit = self._index.nearest(point)
        return hit[0] if hit else -1

    def nearest_nodes(self, points: "Iterable[Point]") -> list[int]:
        """Snap several points at once; returns one node id per point."""
        if not self._node_points:
            return [-1 for _ in points]
        if self._index is None:
            self._index = SpatialIndex(self._node_points)
        out: list[int] = []
        for pt in points:
            hit = self._index.nearest(pt)
            out.append(hit[0] if hit else -1)
        return out


__all__ = ["NetworkGraph"]
