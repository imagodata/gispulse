"""Connected-component labelling of a line network (capability E).

``connectivity_check`` (in :mod:`gispulse.capabilities.network`) answers a
global *is this network connected?* and returns per-component hulls. What it
does **not** do is tag every input feature with the component it belongs to.

``connected_components`` fills that gap: it annotates each line of a network
with a ``component_id`` and the ``component_size`` (number of features in
that component), so disconnected islands — isolated road fragments, separate
drainage basins, orphan utility runs — fall out as a simple attribute you
can filter, dissolve or style on.

Components are numbered by size, **largest first** (``component_id == 0`` is
the biggest), with ties broken deterministically. Connectivity is computed
with union-find over shared endpoints (no networkx dependency); endpoints
within ``snap_tolerance_m`` are treated as the same node, so a slightly
mis-digitised junction does not split a component.
"""

from __future__ import annotations

from collections import Counter

import geopandas as gpd
import numpy as np

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register


class _UnionFind:
    """Minimal union-find for clustering nodes into components."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


@register
class ConnectedComponentsCapability(Capability):
    """Labels each network feature with its connected-component id and size."""

    name = "connected_components"
    description = (
        "Annotates each line of a network with component_id (0 = largest "
        "component) and component_size (features in that component). "
        "Endpoints within snap_tolerance_m count as the same node."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        snap_tolerance_m: float = 0.0,
        component_col: str = "component_id",
        size_col: str = "component_size",
        crs_meters: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:              Line network (LineString / MultiLineString).
            snap_tolerance_m: Endpoints within this metric distance are the
                              same node. ``0`` requires exact coincidence.
            component_col:    Output column for the component id.
            size_col:         Output column for the component feature count.
            crs_meters:       Metric CRS used when ``snap_tolerance_m`` > 0
                              on angular data. Default ``EPSG:3857``.

        Returns:
            Copy of ``gdf`` with ``component_col`` / ``size_col`` added
            (``component_id`` = -1 for rows with no usable geometry).
        """
        from shapely.geometry import LineString, MultiLineString

        out = gdf.copy()
        n_rows = len(out)
        if n_rows == 0:
            out[component_col] = np.array([], dtype=np.int64)
            out[size_col] = np.array([], dtype=np.int64)
            return out

        tol = max(snap_tolerance_m, 0.0)
        if tol > 0 and gdf.crs is not None and crs_meters and str(gdf.crs) != crs_meters:
            work = gdf.to_crs(crs_meters)
        else:
            work = gdf

        # Map each row to the endpoint coordinates of all its line parts.
        # row_endpoints[row] = list of (x, y) endpoints (start/end of each part)
        row_endpoints: list[list[tuple[float, float]]] = []
        all_coords: list[tuple[float, float]] = []
        for geom in work.geometry:
            ends: list[tuple[float, float]] = []
            if geom is not None and not geom.is_empty:
                members = geom.geoms if isinstance(geom, MultiLineString) else [geom]
                for line in members:
                    if isinstance(line, LineString) and len(line.coords) >= 2:
                        cs = list(line.coords)
                        ends.append((cs[0][0], cs[0][1]))
                        ends.append((cs[-1][0], cs[-1][1]))
            row_endpoints.append(ends)
            all_coords.extend(ends)

        node_of_coord = self._node_keys(all_coords, tol)

        # Remap arbitrary node keys to a dense 0..K-1 space for union-find.
        dense: dict[int, int] = {}
        for key in node_of_coord:
            if key not in dense:
                dense[key] = len(dense)
        uf = _UnionFind(len(dense))

        # Walk endpoints per row, unioning the nodes a feature connects.
        cursor = 0
        row_first_node: list[int | None] = []
        for ends in row_endpoints:
            first: int | None = None
            prev: int | None = None
            for _ in ends:
                node = dense[node_of_coord[cursor]]
                cursor += 1
                if first is None:
                    first = node
                if prev is not None:
                    uf.union(prev, node)
                prev = node
            row_first_node.append(first)

        # Raw component per row = the find() of its first endpoint node.
        raw = [uf.find(node) if node is not None else None for node in row_first_node]

        # Size = number of rows per raw component; order components by
        # (-size, first-appearance) so the largest gets id 0, deterministically.
        counts = Counter(c for c in raw if c is not None)
        first_seen: dict[int, int] = {}
        for i, c in enumerate(raw):
            if c is not None and c not in first_seen:
                first_seen[c] = i
        ordered = sorted(counts, key=lambda c: (-counts[c], first_seen[c]))
        final_id = {c: i for i, c in enumerate(ordered)}

        out[component_col] = np.array(
            [final_id[c] if c is not None else -1 for c in raw], dtype=np.int64
        )
        out[size_col] = np.array(
            [counts[c] if c is not None else 0 for c in raw], dtype=np.int64
        )
        return out

    @staticmethod
    def _node_keys(coords: list[tuple[float, float]], tol: float) -> list[int]:
        """Return a node key per coordinate (fused within *tol*)."""
        if not coords:
            return []
        if tol <= 0:
            key_of: dict[tuple[float, float], int] = {}
            out = []
            for x, y in coords:
                k = (round(x, 6), round(y, 6))
                if k not in key_of:
                    key_of[k] = len(key_of)
                out.append(key_of[k])
            return out

        from shapely import STRtree
        from shapely.geometry import Point

        pts = [Point(x, y) for x, y in coords]
        tree = STRtree(pts)
        uf = _UnionFind(len(pts))
        for i, p in enumerate(pts):
            for j in tree.query(p.buffer(tol)):
                jj = int(j)
                if jj != i and pts[jj].distance(p) <= tol:
                    uf.union(i, jj)
        return [uf.find(i) for i in range(len(pts))]

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "snap_tolerance_m": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.0,
                    "description": "Endpoints within this metric distance are one node.",
                },
                "component_col": {"type": "string", "default": "component_id"},
                "size_col": {"type": "string", "default": "component_size"},
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Metric CRS for tolerance on angular data.",
                },
            },
        }


__all__ = ["ConnectedComponentsCapability"]
