"""Public network-graph construction — lines to a topological graph.

Capability A of the network/linear-referencing track. Turns a layer of
lines into an explicit **node + edge** graph, the reusable substrate that
the routing capabilities (shortest_path, od_matrix, isochrone, …) rebuild
privately on every call today.

Output is a single GeoDataFrame discriminated by a ``feature_type`` column
(``"node"`` / ``"edge"``) — the project's standing multi-output contract,
so it flows through ``apply`` unchanged:

* node rows: ``node_id`` (int), ``degree`` (int), ``geometry`` (Point);
* edge rows: ``edge_id`` (stable, from ``id_col``), ``from_node``,
  ``to_node`` (int), ``length_m`` (float), the source attributes, and the
  line ``geometry``.

Endpoints closer than ``snap_tolerance_m`` are fused into one node using a
:class:`~gispulse.core.spatial_index.SpatialIndex` (no O(n²) bucketing).
Processing happens in a metric CRS so ``length_m`` and the tolerance are in
meters; the result is reprojected back to the input CRS.

Split a result back into the two layers with
:func:`split_network_graph`.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, Point

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.core.crs import is_angular
from gispulse.core.spatial_index import SpatialIndex


def _iter_line_parts(geom: Any):
    """Yield the LineString parts of a (Multi)LineString, skipping degenerate."""
    if geom is None or geom.is_empty:
        return
    parts = geom.geoms if isinstance(geom, MultiLineString) else [geom]
    for part in parts:
        if isinstance(part, LineString) and len(part.coords) >= 2:
            yield part


class _UnionFind:
    """Minimal union-find for clustering coincident endpoints."""

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
class BuildNetworkGraphCapability(Capability):
    """Builds a node/edge graph from a line network (public, reusable)."""

    name = "build_network_graph"
    description = (
        "Turns a line layer into a topological graph: a single GeoDataFrame "
        "tagged feature_type=node/edge with stable ids, degrees, from/to "
        "nodes and metric lengths. Endpoints within snap_tolerance_m fuse "
        "into one node (via a spatial index)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        id_col: str | None = None,
        weight_col: str | None = None,
        snap_tolerance_m: float = 0.0,
        crs_meters: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:              Line network (LineString / MultiLineString).
            id_col:           Column whose value seeds each ``edge_id``.
                              Defaults to the row position. Multi-part rows
                              get a ``<id>#<part>`` suffix.
            weight_col:       Column copied onto edges as ``weight`` (kept
                              alongside the always-computed ``length_m``).
            snap_tolerance_m: Endpoints within this metric distance fuse
                              into a single node. ``0`` fuses only exactly
                              coincident endpoints.
            crs_meters:       Metric CRS for length / tolerance when the
                              input is angular. Default ``EPSG:3857``.

        Returns:
            One GeoDataFrame tagged ``feature_type`` (node|edge), in the
            input CRS.
        """
        original_crs = gdf.crs
        reproject = is_angular(gdf)
        effective_crs = crs_meters or "EPSG:3857"
        work = gdf.to_crs(effective_crs) if reproject else gdf

        if work.empty:
            return self._empty(original_crs)

        # --- collect endpoints -------------------------------------------
        # endpoints[i] = (x, y); endpoint_ref[i] = (row_pos, part_idx, which)
        endpoints: list[tuple[float, float]] = []
        # per (row_pos, part_idx): (start_endpoint_idx, end_endpoint_idx, line)
        edge_defs: list[tuple[int, int, int, int, LineString]] = []

        for row_pos, geom in enumerate(work.geometry):
            for part_idx, line in enumerate(_iter_line_parts(geom)):
                coords = list(line.coords)
                s_idx = len(endpoints)
                endpoints.append((coords[0][0], coords[0][1]))
                e_idx = len(endpoints)
                endpoints.append((coords[-1][0], coords[-1][1]))
                edge_defs.append((row_pos, part_idx, s_idx, e_idx, line))

        if not edge_defs:
            return self._empty(original_crs)

        # --- fuse endpoints into nodes -----------------------------------
        node_of_endpoint = self._fuse_endpoints(endpoints, snap_tolerance_m)
        # node id remap to a dense 0..K-1, node geometry = mean of members
        members: dict[int, list[int]] = {}
        for ep_idx, node_key in enumerate(node_of_endpoint):
            members.setdefault(node_key, []).append(ep_idx)
        node_id_of_key: dict[int, int] = {}
        node_rows: list[dict[str, Any]] = []
        for new_id, (key, eps) in enumerate(sorted(members.items())):
            node_id_of_key[key] = new_id
            xs = [endpoints[e][0] for e in eps]
            ys = [endpoints[e][1] for e in eps]
            node_rows.append(
                {
                    "feature_type": "node",
                    "node_id": new_id,
                    "degree": 0,  # filled below
                    "geometry": Point(sum(xs) / len(xs), sum(ys) / len(ys)),
                }
            )

        # --- build edges --------------------------------------------------
        has_id = id_col is not None and id_col in work.columns
        has_weight = weight_col is not None and weight_col in work.columns
        attr_cols = [
            c
            for c in work.columns
            if c != work.geometry.name
            and c not in {"feature_type", "node_id", "degree", "edge_id",
                          "from_node", "to_node", "length_m"}
        ]
        # part counts per row to decide on the #suffix
        part_counts: dict[int, int] = {}
        for row_pos, part_idx, _s, _e, _ln in edge_defs:
            part_counts[row_pos] = max(part_counts.get(row_pos, 0), part_idx + 1)

        degree: dict[int, int] = {}
        edge_rows: list[dict[str, Any]] = []
        for row_pos, part_idx, s_idx, e_idx, line in edge_defs:
            from_node = node_id_of_key[node_of_endpoint[s_idx]]
            to_node = node_id_of_key[node_of_endpoint[e_idx]]
            degree[from_node] = degree.get(from_node, 0) + 1
            degree[to_node] = degree.get(to_node, 0) + 1

            base_id = work.iloc[row_pos][id_col] if has_id else row_pos
            edge_id: Any = base_id
            if part_counts.get(row_pos, 1) > 1:
                edge_id = f"{base_id}#{part_idx}"

            row: dict[str, Any] = {
                "feature_type": "edge",
                "edge_id": edge_id,
                "from_node": from_node,
                "to_node": to_node,
                "length_m": float(line.length),
                "geometry": line,
            }
            if has_weight:
                row["weight"] = work.iloc[row_pos][weight_col]
            for c in attr_cols:
                row[c] = work.iloc[row_pos][c]
            edge_rows.append(row)

        for nr in node_rows:
            nr["degree"] = degree.get(nr["node_id"], 0)

        out = gpd.GeoDataFrame(node_rows + edge_rows, geometry="geometry", crs=work.crs)
        if reproject and original_crs is not None:
            out = out.to_crs(original_crs)
        return out.reset_index(drop=True)

    def _fuse_endpoints(
        self, endpoints: list[tuple[float, float]], tol: float
    ) -> list[int]:
        """Return a node-key per endpoint position (fused within *tol*)."""
        if tol <= 0:
            # Exact fusion on rounded coordinates (mirrors the legacy
            # 6-decimal snap used by network._build_graph).
            key_of: dict[tuple[float, float], int] = {}
            result = []
            for x, y in endpoints:
                key = (round(x, 6), round(y, 6))
                if key not in key_of:
                    key_of[key] = len(key_of)
                result.append(key_of[key])
            return result

        pts = [Point(x, y) for x, y in endpoints]
        idx = SpatialIndex(pts)
        uf = _UnionFind(len(pts))
        for i, p in enumerate(pts):
            for j in idx.query_radius(p, tol):
                if j != i:
                    uf.union(i, j)
        return [uf.find(i) for i in range(len(pts))]

    def _empty(self, crs: Any) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {
                "feature_type": [],
                "node_id": [],
                "degree": [],
                "edge_id": [],
                "from_node": [],
                "to_node": [],
                "length_m": [],
                "geometry": [],
            },
            geometry="geometry",
            crs=crs,
        )

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "id_col": {
                    "type": ["string", "null"],
                    "description": "Column seeding edge_id; defaults to row position.",
                },
                "weight_col": {
                    "type": ["string", "null"],
                    "description": "Column copied onto edges as 'weight'.",
                },
                "snap_tolerance_m": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0.0,
                    "description": "Fuse endpoints within this metric distance.",
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Metric CRS for length/tolerance on angular input.",
                },
            },
        }


def split_network_graph(
    graph_gdf: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Split a ``build_network_graph`` result into ``(nodes_gdf, edges_gdf)``.

    Drops the all-null columns that belonged to the other feature type so
    each layer carries only its own attributes.
    """
    nodes = graph_gdf[graph_gdf["feature_type"] == "node"].dropna(axis=1, how="all")
    edges = graph_gdf[graph_gdf["feature_type"] == "edge"].dropna(axis=1, how="all")
    return (
        nodes.reset_index(drop=True),
        edges.reset_index(drop=True),
    )


__all__ = ["BuildNetworkGraphCapability", "split_network_graph"]
