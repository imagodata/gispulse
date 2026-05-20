"""Unit tests for the shared DAG utility (ELT Lot 4B — issue #248).

The Kahn topological-sort algorithm used at execution time by
:class:`GraphExecutor` lives in :mod:`gispulse.core.dag` so the v3
manifest loader can reuse it for *load-time* cycle detection. These
tests pin both the happy path and the cycle-reporting contract.
"""

from __future__ import annotations

import pytest

from gispulse.core.dag import CycleError, topological_sort


def test_topological_sort_orders_a_simple_chain():
    assert topological_sort(
        nodes=["a", "b", "c"],
        edges=[("a", "b"), ("b", "c")],
    ) == ["a", "b", "c"]


def test_topological_sort_is_stable_on_independent_nodes():
    # Input order seeds the tie-break — three roots stay in declaration order.
    assert topological_sort(
        nodes=["x", "y", "z"], edges=[]
    ) == ["x", "y", "z"]


def test_topological_sort_handles_diamond():
    order = topological_sort(
        nodes=["root", "left", "right", "join"],
        edges=[("root", "left"), ("root", "right"), ("left", "join"), ("right", "join")],
    )
    assert order[0] == "root"
    assert order[-1] == "join"
    assert set(order[1:3]) == {"left", "right"}


def test_topological_sort_raises_cycle_error_with_members():
    with pytest.raises(CycleError) as exc:
        topological_sort(
            nodes=["a", "b", "c"],
            edges=[("a", "b"), ("b", "c"), ("c", "a")],
        )
    assert exc.value.cycle == {"a", "b", "c"}


def test_cycle_error_subclasses_value_error():
    # Existing callers catch ValueError — keep the inheritance chain.
    err = CycleError({"x"})
    assert isinstance(err, ValueError)


def test_graph_executor_topo_sort_still_raises_legacy_message():
    """The :class:`GraphExecutor` wrapper preserves its historical
    ``Cycle detected in graph, unreachable nodes: …`` message so old
    tests / log scrapers keep matching."""
    from gispulse.core.graph import NodeDef, NodeType
    from gispulse.orchestration.graph_executor import GraphExecutor

    nodes = [
        NodeDef(id="a", node_type=NodeType.CAPABILITY),
        NodeDef(id="b", node_type=NodeType.CAPABILITY),
    ]
    adj = {"a": ["b"], "b": ["a"]}
    in_degree = {"a": 1, "b": 1}
    with pytest.raises(ValueError, match="Cycle detected in graph"):
        GraphExecutor._topo_sort(nodes, adj, in_degree)
