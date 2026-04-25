"""DAG-based graph executor for GISPulse pipeline nodes.

Executes a directed acyclic graph (DAG) of :class:`NodeDef` connected by
:class:`EdgeDef`.  Supports linear pipelines, multi-input operations,
loop/branch/parallel composite nodes, and dataset/artifact endpoints.
"""

from __future__ import annotations

from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import geopandas as gpd
import pandas as pd

from core.logging import get_logger
from core.models import EdgeDef, NodeDef, NodeType

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class GraphExecutor:
    """Execute a DAG of :class:`NodeDef` nodes.

    The executor topologically sorts the nodes, resolves multi-input edges,
    and delegates each node to the appropriate handler (capability call,
    loop expansion, branch evaluation, parallel fork, etc.).

    Args:
        capability_getter: ``(name: str) -> Capability`` — resolve a
            registered capability by name.  Typically
            ``capabilities.registry.get``.
        max_workers: thread-pool size for ``ParallelNode`` execution.
    """

    def __init__(
        self,
        capability_getter: Callable[[str], Any],
        *,
        max_workers: int = 4,
        execution_context: Any | None = None,
    ) -> None:
        self._get_cap = capability_getter
        self._max_workers = max_workers
        self._execution_context = execution_context

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def execute(
        self,
        nodes: list[NodeDef],
        edges: list[EdgeDef],
        inputs: dict[str, gpd.GeoDataFrame],
        params: dict[str, Any] | None = None,
    ) -> dict[str, gpd.GeoDataFrame]:
        """Execute the full graph.

        Args:
            nodes:  Flat list of node definitions.
            edges:  Directed edges connecting node ids.
            inputs: Pre-loaded GeoDataFrames keyed by *DatasetNode* id.
            params: Template parameters (``$var`` substitution).

        Returns:
            Mapping of node-id → result GeoDataFrame for every node that
            produced output.
        """
        params = params or {}
        node_map = {n.id: n for n in nodes}
        adj, in_degree = self._build_adjacency(nodes, edges)
        order = self._topo_sort(nodes, adj, in_degree)
        edge_index = self._build_edge_index(edges)

        results: dict[str, gpd.GeoDataFrame] = dict(inputs)

        for nid in order:
            node = node_map[nid]
            node_inputs = self._collect_inputs(nid, edge_index, results)

            # Pass the live ``results`` dict (not just initial ``inputs``) so
            # ``ref_layer`` can resolve to a prior step's output — enables
            # patterns like "filter a ref layer first, then use it as ref".
            result = self._execute_node(node, node_inputs, params, results)
            if result is not None:
                results[nid] = result

        return results

    # ------------------------------------------------------------------
    # Node dispatch
    # ------------------------------------------------------------------

    def _execute_node(
        self,
        node: NodeDef,
        node_inputs: dict[str, gpd.GeoDataFrame],
        params: dict[str, Any],
        global_inputs: dict[str, gpd.GeoDataFrame],
    ) -> gpd.GeoDataFrame | None:
        handler = _NODE_HANDLERS.get(node.node_type)
        if handler is None:
            log.warning("graph_unknown_node_type", node_id=node.id, node_type=node.node_type)
            return None
        return handler(self, node, node_inputs, params, global_inputs)

    # ------------------------------------------------------------------
    # Handlers per NodeType
    # ------------------------------------------------------------------

    def _handle_dataset(
        self, node: NodeDef, inputs: dict, params: dict, global_inputs: dict
    ) -> gpd.GeoDataFrame | None:
        bind = _resolve_param(node.bind, params)
        if node.id in global_inputs:
            return global_inputs[node.id]
        if bind and bind in global_inputs:
            return global_inputs[bind]
        log.warning("graph_dataset_missing", node_id=node.id, bind=bind)
        return None

    def _handle_capability(
        self, node: NodeDef, inputs: dict, params: dict, global_inputs: dict
    ) -> gpd.GeoDataFrame | None:
        if not node.capability:
            log.error("graph_capability_missing", node_id=node.id)
            return None
        cap = self._get_cap(node.capability)
        resolved = _resolve_params(node.params, params)

        # Resolve ref_layer → ref_gdf from the global inputs (loaded ref
        # layers). Mirrors the linear-pipeline executor so capabilities that
        # expect a reference layer (filter, isochrone, spatial_join, …)
        # receive it in DAG mode too. The spec_to_graph converter keys
        # dataset nodes as ``_input_<alias>`` so we probe both forms.
        ref_alias = resolved.get("ref_layer")
        if ref_alias:
            if ref_alias in global_inputs:
                resolved["ref_gdf"] = global_inputs[ref_alias]
            elif f"_input_{ref_alias}" in global_inputs:
                resolved["ref_gdf"] = global_inputs[f"_input_{ref_alias}"]

        # Plural variant: ref_layers (list) → ref_gdfs (list). Mirrors the
        # singular resolution above; used by merge_layers to stack N layers.
        ref_aliases = resolved.get("ref_layers")
        if isinstance(ref_aliases, list) and ref_aliases:
            resolved["ref_gdfs"] = [
                global_inputs[a] if a in global_inputs else global_inputs[f"_input_{a}"]
                for a in ref_aliases
                if a in global_inputs or f"_input_{a}" in global_inputs
            ]

        from orchestration.pipeline_executor import _auto_inject_crs_meters, _validate_step_params

        primary_gdf = next(iter(inputs.values())) if inputs else None
        if primary_gdf is not None:
            _auto_inject_crs_meters(cap, node.id, resolved, primary_gdf)
        _validate_step_params(cap, node.id, resolved)

        if len(inputs) == 1:
            gdf = next(iter(inputs.values()))
            # Use engine-accelerated path when execution_context is available
            if self._execution_context is not None and hasattr(cap, "execute_with_context"):
                from capabilities.strategy import ExecutionContext
                ctx = ExecutionContext(
                    engine=self._execution_context.engine,
                    feature_count=len(gdf),
                    has_spatial_index=self._execution_context.has_spatial_index,
                    params=resolved,
                )
                return cap.execute_with_context(gdf, ctx)
            return cap.execute(gdf, **resolved)

        # multi-input: pass as named kwargs
        return cap.execute(**inputs, **resolved)

    def _handle_rule(
        self, node: NodeDef, inputs: dict, params: dict, global_inputs: dict
    ) -> gpd.GeoDataFrame | None:
        # A RuleNode wraps a capability call with extra config
        return self._handle_capability(node, inputs, params, global_inputs)

    def _handle_loop(
        self, node: NodeDef, inputs: dict, params: dict, global_inputs: dict
    ) -> gpd.GeoDataFrame | None:
        """Execute sub-graph once per item in the collection."""
        over = _resolve_param(node.params.get("over"), params)
        items: list[str] = over if isinstance(over, list) else [over]
        primary_gdf = next(iter(inputs.values())) if inputs else None

        collected: list[gpd.GeoDataFrame] = []
        for item in items:
            iter_params = {**params, "$item": item}
            iter_inputs: dict[str, gpd.GeoDataFrame] = {}
            if primary_gdf is not None:
                # feed the first body node with the upstream GDF
                for bnode in node.body:
                    if bnode.node_type == NodeType.DATASET:
                        if bnode.bind == "$item" and item in global_inputs:
                            iter_inputs[bnode.id] = global_inputs[item]
                    else:
                        break
                # also expose the primary input
                first_non_ds = next(
                    (b for b in node.body if b.node_type != NodeType.DATASET), None
                )
                if first_non_ds and first_non_ds.id not in iter_inputs:
                    iter_inputs[first_non_ds.id] = primary_gdf

            sub_results = self.execute(
                node.body, node.body_edges, {**global_inputs, **iter_inputs}, iter_params
            )
            # collect last node output
            if node.body:
                last_id = node.body[-1].id
                if last_id in sub_results:
                    collected.append(sub_results[last_id])

        if not collected:
            return primary_gdf
        return gpd.GeoDataFrame(pd.concat(collected, ignore_index=True))

    def _handle_branch(
        self, node: NodeDef, inputs: dict, params: dict, _gi: dict
    ) -> gpd.GeoDataFrame | None:
        """Evaluate condition and return the matching input."""
        condition_field = node.params.get("condition_field", "")
        condition_op = node.params.get("condition_op", "eq")
        condition_value = _resolve_param(node.params.get("condition_value"), params)

        gdf = next(iter(inputs.values())) if inputs else None
        if gdf is None or gdf.empty:
            return gdf

        if condition_field and condition_field in gdf.columns:
            if condition_op == "eq":
                mask = gdf[condition_field] == condition_value
            elif condition_op == "neq":
                mask = gdf[condition_field] != condition_value
            elif condition_op == "gt":
                mask = gdf[condition_field] > condition_value
            elif condition_op == "lt":
                mask = gdf[condition_field] < condition_value
            else:
                mask = gdf[condition_field] == condition_value
            return gpd.GeoDataFrame(gdf[mask])

        return gdf

    def _handle_parallel(
        self, node: NodeDef, inputs: dict, params: dict, global_inputs: dict
    ) -> gpd.GeoDataFrame | None:
        """Fork N sub-graphs in parallel threads."""
        primary_gdf = next(iter(inputs.values())) if inputs else None
        if not node.body:
            return primary_gdf

        # Each body node is a root of an independent branch
        results: list[gpd.GeoDataFrame] = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {}
            for bnode in node.body:
                sub_inputs = dict(global_inputs)
                if primary_gdf is not None:
                    sub_inputs[bnode.id] = primary_gdf
                fut = pool.submit(
                    self.execute, [bnode], [], sub_inputs, params
                )
                futures[fut] = bnode.id

            for fut in as_completed(futures):
                bid = futures[fut]
                sub = fut.result()
                if bid in sub:
                    results.append(sub[bid])

        if not results:
            return primary_gdf
        return gpd.GeoDataFrame(pd.concat(results, ignore_index=True))

    def _handle_aggregate(
        self, node: NodeDef, inputs: dict, params: dict, _gi: dict
    ) -> gpd.GeoDataFrame | None:
        """Merge multiple GeoDataFrames into one."""
        gdfs = list(inputs.values())
        if not gdfs:
            return None
        if len(gdfs) == 1:
            return gdfs[0]
        return gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True))

    def _handle_artifact(
        self, node: NodeDef, inputs: dict, params: dict, _gi: dict
    ) -> gpd.GeoDataFrame | None:
        # Artifact nodes are sinks; they pass through for now.
        # Actual export is handled by the caller after execution.
        return next(iter(inputs.values())) if inputs else None

    def _handle_trigger(
        self, node: NodeDef, inputs: dict, params: dict, _gi: dict
    ) -> gpd.GeoDataFrame | None:
        # Trigger nodes in a graph context just pass data through.
        return next(iter(inputs.values())) if inputs else None

    # ------------------------------------------------------------------
    # Graph utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _build_adjacency(
        nodes: list[NodeDef], edges: list[EdgeDef]
    ) -> tuple[dict[str, list[str]], dict[str, int]]:
        adj: dict[str, list[str]] = defaultdict(list)
        in_degree: dict[str, int] = {n.id: 0 for n in nodes}
        for e in edges:
            adj[e.source].append(e.target)
            in_degree.setdefault(e.target, 0)
            in_degree[e.target] += 1
        return adj, in_degree

    @staticmethod
    def _topo_sort(
        nodes: list[NodeDef],
        adj: dict[str, list[str]],
        in_degree: dict[str, int],
    ) -> list[str]:
        """Kahn's algorithm for topological ordering."""
        q: deque[str] = deque()
        for n in nodes:
            if in_degree.get(n.id, 0) == 0:
                q.append(n.id)
        order: list[str] = []
        deg = dict(in_degree)
        while q:
            nid = q.popleft()
            order.append(nid)
            for child in adj.get(nid, []):
                deg[child] -= 1
                if deg[child] == 0:
                    q.append(child)
        if len(order) != len(nodes):
            missing = {n.id for n in nodes} - set(order)
            raise ValueError(f"Cycle detected in graph, unreachable nodes: {missing}")
        return order

    @staticmethod
    def _build_edge_index(edges: list[EdgeDef]) -> dict[str, list[EdgeDef]]:
        """Index edges by target node."""
        idx: dict[str, list[EdgeDef]] = defaultdict(list)
        for e in edges:
            idx[e.target].append(e)
        return idx

    @staticmethod
    def _collect_inputs(
        node_id: str,
        edge_index: dict[str, list[EdgeDef]],
        results: dict[str, gpd.GeoDataFrame],
    ) -> dict[str, gpd.GeoDataFrame]:
        """Gather upstream outputs for a node, keyed by handle or source id.

        Edges with handle ``"ref_dep"`` are ordering-only (used for ref_layer
        chaining) and are *not* passed as a data input to the capability.
        """
        out: dict[str, gpd.GeoDataFrame] = {}
        for edge in edge_index.get(node_id, []):
            if edge.handle == "ref_dep":
                continue
            gdf = results.get(edge.source)
            if gdf is not None:
                key = edge.handle.split(":")[-1] if edge.handle else edge.source
                out[key] = gdf
        return out


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_NODE_HANDLERS: dict[NodeType, Callable] = {
    NodeType.DATASET:    GraphExecutor._handle_dataset,
    NodeType.CAPABILITY: GraphExecutor._handle_capability,
    NodeType.RULE:       GraphExecutor._handle_rule,
    NodeType.LOOP:       GraphExecutor._handle_loop,
    NodeType.BRANCH:     GraphExecutor._handle_branch,
    NodeType.PARALLEL:   GraphExecutor._handle_parallel,
    NodeType.AGGREGATE:  GraphExecutor._handle_aggregate,
    NodeType.ARTIFACT:   GraphExecutor._handle_artifact,
    NodeType.TRIGGER:    GraphExecutor._handle_trigger,
}


# ---------------------------------------------------------------------------
# Parameter resolution helpers
# ---------------------------------------------------------------------------


def _resolve_param(value: Any, params: dict[str, Any]) -> Any:
    """Resolve a single ``$param`` reference."""
    if isinstance(value, str) and value.startswith("$"):
        return params.get(value.lstrip("$"), params.get(value, value))
    return value


def _resolve_params(cfg: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Resolve all ``$param`` references in a config dict."""
    return {k: _resolve_param(v, params) for k, v in cfg.items()}
