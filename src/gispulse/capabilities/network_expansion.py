"""Greedy multi-source network expansion for GISPulse.

Requires optional dependencies:
    - networkx  (via :class:`~gispulse.core.network_graph_handle.NetworkGraph`)
    - shapely   (already a geopandas dependency)

Grows a tree/forest out of a set of *frontier* (root) nodes by greedy
Prim-style absorption: at each step it absorbs the not-yet-reached node whose
**marginal cost** — edge weight plus a one-off **activation cost** of the
target node — is minimal, then extends the frontier, until the reachable
component is exhausted.

How this differs from its siblings in :mod:`gispulse.capabilities.network`:

* ``mst`` spans **every** node with a single edge weight and no roots.
* ``steiner_tree`` connects a **fixed, mandatory** set of terminals.
* ``network_greedy_expansion`` starts from **multiple roots**, has **no**
  mandatory targets (it absorbs whatever is reachable and cheapest first),
  and charges a **per-node activation cost** paid once, on absorption, on top
  of the edge weight.

Pure ``heapq`` implementation (no numba / JIT). Like every capability in
:mod:`gispulse.capabilities.network` it requires ``tier="pro"``.

Ported faithfully from the ``expand()`` oil-slick kernel of an internal
fibre-network project: multi-source binary-heap Prim on a CSR graph, with a
deliberately broad NaN-cost fast-fail data-quality guard: any NaN edge weight
or activation cost is rejected up front (NaN is not ordered under comparison,
so one reaching the heap would make tie handling ill-defined). The scan is
intentionally simple — it covers the whole graph, not only the edges the
frontier reaches — and ``inf`` stays allowed, being fully ordered in IEEE and
only ever summed.
"""

from __future__ import annotations

import heapq
import math
from typing import Any

import geopandas as gpd
from shapely.geometry import LineString

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.core.crs import is_angular
from gispulse.core.network_graph_handle import NetworkGraph
from gispulse.persistence.tier import check_tier

# Column contract of the emitted absorption-tree edges. Kept as a module
# constant so the empty-result branches and the populated branch stay in sync.
_EXPANSION_COLUMNS = [
    "step_order",
    "u",
    "v",
    "edge_weight",
    "activation_cost",
    "marginal_cost",
    "geometry",
]


@register
class NetworkGreedyExpansionCapability(Capability):
    """Greedy multi-source expansion of a line network with per-node activation cost."""

    name = "network_greedy_expansion"
    description = (
        "Grows a tree/forest from a set of frontier (root) nodes by greedy "
        "Prim-style absorption: at each step the reachable node with the "
        "smallest marginal cost (edge weight + target activation cost) is "
        "absorbed. Unlike 'mst' (spans everything, no roots) and "
        "'steiner_tree' (connects a fixed terminal set), it starts from "
        "multiple roots, has no mandatory targets, and charges a one-off "
        "activation cost per node. Frontier passed as ref_layer; optional "
        "per-node activation costs via a second ref_layers entry."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        ref_gdfs: list[gpd.GeoDataFrame] | None = None,
        cost_gdf: gpd.GeoDataFrame | None = None,
        cost_col: str = "activation_cost",
        weight_col: str | None = None,
        crs_meters: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Line network (LineStrings) to grow through.
            ref_gdf:    Frontier (root) points, injected via ``ref_layer``.
                        Each must coincide with a network node — see the
                        snapping note below.
            ref_gdfs:   Plural injection via ``ref_layers``, which must list
                        ``[frontier_alias, cost_alias]`` in that order. The
                        second (activation-cost) layer is **optional**: with a
                        single alias, ``ref_gdfs`` has length 1 and every
                        activation cost is 0; with two, ``ref_gdfs[1]`` is the
                        cost layer. When both ``ref_gdfs`` and ``ref_gdf`` are
                        given, ``ref_gdfs[0]`` wins as the frontier.
            cost_gdf:   Point layer carrying the per-node activation cost in
                        ``cost_col``. Alternative to ``ref_gdfs[1]`` (an
                        explicit ``cost_gdf`` takes precedence). Each point is
                        snapped to its nearest network node; a node with no
                        cost point has an activation cost of 0.
            cost_col:   Activation-cost column on the cost layer (default
                        ``"activation_cost"``).
            weight_col: Arc weight column; defaults to geometric length after
                        metric reprojection.
            crs_meters: Metric CRS used when the network is angular (lat/lon)
                        so weights and snapping are in meters. Default
                        EPSG:3857.

        Frontier snapping — porting note: the source algorithm takes the
        frontier as **existing network nodes** and fast-fails when one is not
        in the graph. An unbounded nearest-node snap (as ``steiner_tree`` uses
        for terminals) can never fail on a non-empty graph, which would make
        that fast-fail meaningless. So each frontier point is matched by
        **exact node identity**: its coordinates (rounded to the graph's
        6-decimal snap, in the working CRS) must equal a network node.
        Frontier points that are not network nodes are reported as missing
        (``ValueError``). Activation-cost points, by contrast, snap to the
        nearest node as documented above.

        Returns:
            GeoDataFrame of the absorption-tree edges, one row per absorbed
            node, in absorption order:

            - ``step_order``      : 1-based absorption order (deterministic).
            - ``u`` / ``v``       : parent (already reached) and child (newly
                                    absorbed) node ids.
            - ``edge_weight``     : weight of the parent→child edge.
            - ``activation_cost`` : one-off activation cost of ``v`` (0 when no
                                    cost point snaps onto it).
            - ``marginal_cost``   : ``edge_weight + activation_cost``.
            - ``geometry``        : the parent→child edge geometry.

            Reprojected back to the network's original CRS. Nodes in
            components not reachable from the frontier never appear.

        Raises:
            ValueError: empty frontier, a frontier point that is not a network
                node (missing nodes listed), or a NaN edge weight / activation
                cost (rejected up front as a data-quality guard; ``inf`` is
                allowed).
        """
        check_tier("pro")

        # Resolve the frontier and (optional) activation-cost layers from the
        # several injection paths. The plural ref_layers carries
        # [frontier, cost]; ref_gdfs[0] is the frontier and ref_gdfs[1], when
        # present, the cost layer. An explicit cost_gdf param wins over
        # ref_gdfs[1]; ref_gdf (singular) is the frontier fallback.
        frontier_gdf: gpd.GeoDataFrame | None = None
        resolved_cost_gdf = cost_gdf
        if ref_gdfs:
            frontier_gdf = ref_gdfs[0]
            if len(ref_gdfs) >= 2 and resolved_cost_gdf is None:
                resolved_cost_gdf = ref_gdfs[1]
        if frontier_gdf is None:
            frontier_gdf = ref_gdf

        if frontier_gdf is None or frontier_gdf.empty:
            raise ValueError(
                "network_greedy_expansion requires a non-empty frontier layer "
                "(inject via ref_layer, or as ref_layers[0])."
            )

        original_crs = gdf.crs
        if gdf.empty:
            return gpd.GeoDataFrame(columns=_EXPANSION_COLUMNS, crs=original_crs)

        reproject = is_angular(gdf)
        effective_crs = crs_meters or "EPSG:3857"
        network_m = gdf.to_crs(effective_crs) if reproject else gdf

        graph = NetworkGraph.from_lines(network_m, weight_col)
        G = graph.graph
        if len(graph) == 0:
            return gpd.GeoDataFrame(columns=_EXPANSION_COLUMNS, crs=original_crs)

        # --- Frontier nodes: exact node identity (see porting note) ---
        # Align the frontier onto the graph's working CRS (``network_m.crs``)
        # whenever its own CRS differs — independently of whether the network
        # itself needed reprojection. A projected network (``reproject`` False)
        # still requires a frontier given in another CRS to be reprojected, or
        # the exact node-identity match below fails on legitimate input.
        frontier_m = (
            frontier_gdf.to_crs(network_m.crs)
            if frontier_gdf.crs is not None and frontier_gdf.crs != network_m.crs
            else frontier_gdf
        )
        node_index = graph.node_index
        frontier_nodes: set[int] = set()
        missing: list[tuple[float, float]] = []
        for geom in frontier_m.geometry:
            if geom is None or geom.is_empty:
                continue
            pt = geom if geom.geom_type == "Point" else geom.centroid
            key = (round(pt.x, 6), round(pt.y, 6))
            nid = node_index.get(key)
            if nid is None:
                missing.append(key)
            else:
                frontier_nodes.add(nid)
        if missing:
            raise ValueError(
                "network_greedy_expansion: frontier point(s) are not network "
                f"nodes: {sorted(missing)[:5]}"
            )
        if not frontier_nodes:
            raise ValueError(
                "network_greedy_expansion: frontier layer has no usable point "
                "(all geometries empty)."
            )

        # --- Per-node activation cost (nearest-node snap) ---
        activation_cost: dict[int, float] = {}
        if resolved_cost_gdf is not None and not resolved_cost_gdf.empty:
            if cost_col not in resolved_cost_gdf.columns:
                raise ValueError(
                    f"network_greedy_expansion: cost_col {cost_col!r} not in "
                    "the activation-cost layer."
                )
            # Same rule as the frontier: align the cost layer to the graph's
            # working CRS whenever its own CRS differs — independently of the
            # network's own reprojection. Otherwise a cost point in another CRS
            # snaps to the wrong node and its cost is silently lost / mis-assigned.
            cost_m = (
                resolved_cost_gdf.to_crs(network_m.crs)
                if resolved_cost_gdf.crs is not None
                and resolved_cost_gdf.crs != network_m.crs
                else resolved_cost_gdf
            )
            for geom, raw in zip(cost_m.geometry, cost_m[cost_col]):
                if geom is None or geom.is_empty:
                    continue
                pt = geom if geom.geom_type == "Point" else geom.centroid
                nid = graph.nearest_node(pt)
                if nid < 0:
                    continue
                # Last cost point wins on collision — deterministic given the
                # (fixed) input row order.
                activation_cost[nid] = float(raw)

        # --- NaN fast-fail (AX #5 validate at the boundary). A NaN edge weight
        # or activation cost is rejected up front as a deliberately broad
        # data-quality guard: NaN is not ordered under comparison, so one
        # reaching the heap would make tie handling ill-defined. The scan is
        # intentionally simple and covers the whole graph (not only the
        # reachable edges); inf stays allowed (fully ordered in IEEE, only ever
        # summed). ---
        for _u, _v, data in G.edges(data=True):
            w = data.get("weight")
            if w is not None and math.isnan(w):
                raise ValueError(
                    "network_greedy_expansion: NaN edge weight — a NaN cost "
                    "breaks the deterministic absorption order (inf is allowed)."
                )
        if any(math.isnan(c) for c in activation_cost.values()):
            raise ValueError(
                "network_greedy_expansion: NaN activation cost — a NaN cost "
                "breaks the deterministic absorption order (inf is allowed)."
            )

        # --- Greedy multi-source Prim absorption (binary heap) ---
        # Heap entries are (marginal, child, parent): the int child/parent ids
        # give a deterministic intra-call tie-break on equal marginal cost
        # (independent of Python's heap internals). This guarantees the same
        # output for the same input, not an order parity with any other project.
        absorbed: set[int] = set(frontier_nodes)
        heap: list[tuple[float, int, int]] = []

        def _push_from(node: int) -> None:
            for nbr in G.neighbors(node):
                if nbr in absorbed:
                    continue
                weight = float(G.edges[node, nbr]["weight"])
                marginal = weight + activation_cost.get(nbr, 0.0)
                heapq.heappush(heap, (marginal, nbr, node))

        for root in sorted(frontier_nodes):
            _push_from(root)

        rows: list[dict[str, Any]] = []
        step = 0
        while heap:
            marginal, node, parent = heapq.heappop(heap)
            if node in absorbed:
                continue
            absorbed.add(node)
            step += 1
            edge = G.edges[parent, node]
            weight = float(edge["weight"])
            act = float(activation_cost.get(node, 0.0))
            geom = edge.get("geometry")
            if geom is None:
                geom = LineString([graph.node_point(parent), graph.node_point(node)])
            rows.append(
                {
                    "step_order": step,
                    "u": int(parent),
                    "v": int(node),
                    "edge_weight": weight,
                    "activation_cost": act,
                    "marginal_cost": weight + act,
                    "geometry": geom,
                }
            )
            _push_from(node)

        if not rows:
            return gpd.GeoDataFrame(columns=_EXPANSION_COLUMNS, crs=original_crs)

        result = gpd.GeoDataFrame(rows, geometry="geometry", crs=network_m.crs)
        if reproject and original_crs is not None:
            result = result.to_crs(original_crs)
        return result.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": ["string", "null"],
                    "description": "Frontier (root) points layer. Equivalent to ref_layers[0].",
                },
                "ref_layers": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "[frontier_alias, cost_alias] in that order; the cost "
                        "layer (second entry) is optional."
                    ),
                },
                "cost_col": {
                    "type": "string",
                    "default": "activation_cost",
                    "description": "Per-node activation-cost column on the cost layer.",
                },
                "weight_col": {
                    "type": ["string", "null"],
                    "description": "Arc weight column; defaults to geometric length.",
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Metric CRS for an angular network. Use EPSG:2154 in France.",
                },
            },
            # ``ref_layer`` / ``ref_layers`` are pipeline plumbing (stripped by
            # rules.validation._PLUMBING_KEYS before validation) so they cannot
            # appear in ``required`` — the runtime raises a clear ValueError
            # when no frontier is provided, preserving the contract.
        }
