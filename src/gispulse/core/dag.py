"""Shared DAG utilities — Kahn topological sort + cycle reporting.

ELT Lot 4B (issue #248, ADR 0005). The Kahn topological-sort algorithm
already lives inside :class:`gispulse.orchestration.graph_executor.GraphExecutor`
where it runs *at execution time* over the pipeline's
:class:`~gispulse.core.graph.NodeDef` / :class:`~gispulse.core.graph.EdgeDef`
graph. ADR 0005 calls for the same check at **load time** on the v3
manifest's inter-model dependency graph — same algorithm, different
node-type, called earlier.

This module extracts the algorithm into a reusable utility keyed on
opaque node-id strings, so:

- :class:`GraphExecutor` reuses it at run time (no change in behaviour).
- :func:`gispulse.core.manifest_v3.validate_manifest` reuses it at load
  time over the model→model dependency graph.

Anything that fails the sort raises :class:`CycleError` — a
:class:`ValueError` subtype carrying the precise cycle members so
callers can surface them in messages.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

__all__ = ["CycleError", "topological_sort"]


class CycleError(ValueError):
    """The graph has at least one cycle.

    Attributes:
        cycle: Set of node ids that participate in some cycle — the
            nodes Kahn's algorithm could not reach.
    """

    def __init__(self, cycle: set[str], message: str | None = None) -> None:
        self.cycle = cycle
        super().__init__(
            message
            or f"Cycle detected; unreachable nodes: {sorted(cycle)!r}"
        )


def topological_sort(
    nodes: Iterable[str],
    edges: Iterable[tuple[str, str]],
) -> list[str]:
    """Return *nodes* in a topological order (Kahn's algorithm).

    Args:
        nodes: Iterable of node ids. Order seeds the deterministic
            stable-tie behaviour — nodes are visited in input order
            when they share an in-degree of zero.
        edges: Iterable of ``(source, target)`` pairs. ``source`` and
            ``target`` must be ids from *nodes*; unknown targets land in
            ``in_degree`` with a zero base, matching the legacy
            ``GraphExecutor`` behaviour.

    Returns:
        The node ids in a stable topological order.

    Raises:
        CycleError: When a cycle prevents ordering all nodes. The
            exception's ``cycle`` attribute carries the participating
            node ids.
    """
    node_ids = list(nodes)
    adj: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    for src, dst in edges:
        adj[src].append(dst)
        in_degree.setdefault(dst, 0)
        in_degree[dst] += 1

    queue: deque[str] = deque(nid for nid in node_ids if in_degree.get(nid, 0) == 0)
    order: list[str] = []
    deg = dict(in_degree)
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for child in adj.get(nid, []):
            deg[child] -= 1
            if deg[child] == 0:
                queue.append(child)
    if len(order) != len(node_ids):
        unreachable = {nid for nid in node_ids if nid not in order}
        raise CycleError(unreachable)
    return order
