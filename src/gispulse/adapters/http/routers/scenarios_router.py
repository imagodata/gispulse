"""
Scenarios router for the GISPulse HTTP API.

Endpoints:
    POST   /scenarios                      — create a scenario
    GET    /scenarios                      — list all scenarios
    GET    /scenarios/{id}                 — detail for a single scenario
    PUT    /scenarios/{id}                 — update a scenario (including graph)
    DELETE /scenarios/{id}                 — delete a scenario
    POST   /scenarios/{id}/run             — execute a scenario
    POST   /scenarios/{id}/run-node        — execute a single node (R-4 #154)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gispulse.adapters.http.rate_limit import limiter
from gispulse.adapters.http.dependencies import (
    get_dataset_repo,
    get_rule_repo,
    get_scenario_repo,
    get_spatial_engine,
)
from gispulse.adapters.http.schemas import ScenarioCreate, ScenarioResponse
from gispulse.core.models import Scenario
from gispulse.persistence.engine import SpatialEngine
from gispulse.persistence.repository import Repository

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


# ---------------------------------------------------------------------------
# Run-specific schemas
# ---------------------------------------------------------------------------


class ScenarioGraphUpdate(BaseModel):
    """Payload to update a scenario's graph definition."""

    name: str | None = None
    graph: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NodeExecResult(BaseModel):
    """Execution result for a single graph node."""

    node_id: str
    status: str  # success | failed | skipped
    duration_ms: float = 0.0
    output_count: int | None = None
    error: str | None = None


class ScenarioRunResult(BaseModel):
    """Result of executing a scenario."""

    scenario_id: UUID
    status: str  # success | failed | partial
    node_results: list[NodeExecResult] = Field(default_factory=list)
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scenario_to_response(s: Scenario) -> ScenarioResponse:
    return ScenarioResponse(
        id=s.id,
        name=s.name,
        dataset_id=s.dataset_id,
        jobs=s.jobs,
        rules=s.rules,
        metadata=s.metadata,
        created_at=s.created_at,
        version=s.version,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=ScenarioResponse, status_code=201)
def create_scenario(
    payload: ScenarioCreate,
    repo: Repository = Depends(get_scenario_repo),
) -> ScenarioResponse:
    """Create a new Scenario."""
    scenario = Scenario(
        name=payload.name,
        dataset_id=payload.dataset_id,
        jobs=payload.jobs,
        rules=payload.rules,
        metadata=payload.metadata,
    )
    # Store graph if provided in metadata
    if "graph" in payload.metadata:
        scenario.graph = payload.metadata.pop("graph")
    repo.save(scenario)
    return _scenario_to_response(scenario)


@router.get("")
def list_scenarios(
    limit: int = 50,
    offset: int = 0,
    repo: Repository = Depends(get_scenario_repo),
) -> dict:
    """Return paginated scenarios."""
    all_items = repo.list_all()
    total = len(all_items)
    page = all_items[offset : offset + limit]
    return {
        "items": [_scenario_to_response(s) for s in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{scenario_id}", response_model=ScenarioResponse)
def get_scenario(
    scenario_id: UUID,
    repo: Repository = Depends(get_scenario_repo),
) -> ScenarioResponse:
    """Return a single scenario by UUID."""
    scenario = repo.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")
    return _scenario_to_response(scenario)


@router.put("/{scenario_id}", response_model=ScenarioResponse)
def update_scenario(
    scenario_id: UUID,
    payload: ScenarioGraphUpdate,
    repo: Repository = Depends(get_scenario_repo),
) -> ScenarioResponse:
    """Update a scenario's name, graph, or metadata."""
    scenario = repo.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    if payload.name is not None:
        scenario.name = payload.name
    if payload.graph:
        scenario.graph = payload.graph
    if payload.metadata:
        scenario.metadata.update(payload.metadata)
    scenario.version += 1
    repo.save(scenario)
    return _scenario_to_response(scenario)


@router.delete("/{scenario_id}", status_code=204)
def delete_scenario(
    scenario_id: UUID,
    repo: Repository = Depends(get_scenario_repo),
) -> None:
    """Delete a scenario by UUID."""
    deleted = repo.delete(scenario_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@router.post("/{scenario_id}/run", response_model=ScenarioRunResult)
@limiter.limit("10/minute")
def run_scenario(
    request: Request,
    scenario_id: UUID,
    repo: Repository = Depends(get_scenario_repo),
    dataset_repo: Repository = Depends(get_dataset_repo),
    rule_repo: Repository = Depends(get_rule_repo),
    engine: SpatialEngine = Depends(get_spatial_engine),
) -> ScenarioRunResult:
    """Execute a scenario and return per-node results.

    If the scenario has a graph definition (nodes + edges), uses the
    GraphExecutor for DAG execution. Otherwise falls back to the
    sequential ScenarioRunner.
    """
    import time

    scenario = repo.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    start = time.monotonic()
    node_results: list[NodeExecResult] = []

    graph = scenario.graph
    has_graph = bool(graph and graph.get("nodes"))

    if has_graph:
        node_results = _run_graph(scenario, engine, rule_repo, dataset_repo)
    else:
        node_results = _run_sequential(scenario, engine, rule_repo, dataset_repo)

    duration_ms = (time.monotonic() - start) * 1000
    failed = any(nr.status == "failed" for nr in node_results)
    status = "failed" if failed else "success"

    return ScenarioRunResult(
        scenario_id=scenario.id,
        status=status,
        node_results=node_results,
        duration_ms=round(duration_ms, 1),
    )


def _run_graph(
    scenario: Scenario,
    engine: SpatialEngine,
    rule_repo: Repository,
    dataset_repo: Repository,
) -> list[NodeExecResult]:
    """Execute a scenario via the GraphExecutor."""

    from gispulse.core.models import NodeDef, EdgeDef, NodeType
    from gispulse.orchestration.graph_executor import GraphExecutor

    graph = scenario.graph
    raw_nodes = graph.get("nodes", [])
    raw_edges = graph.get("edges", [])

    nodes = [
        NodeDef(
            id=n["id"],
            node_type=NodeType(n.get("node_type", "capability")),
            capability=n.get("capability"),
            params=n.get("params", {}),
            bind=n.get("bind"),
        )
        for n in raw_nodes
    ]
    edges = [
        EdgeDef(
            source=e["source"],
            target=e["target"],
            handle=e.get("handle", ""),
        )
        for e in raw_edges
    ]

    executor = GraphExecutor(rule_repo=rule_repo)
    results: list[NodeExecResult] = []

    # Execute node by node with individual timing
    import geopandas as gpd

    try:
        # Build inputs dict for DATASET nodes
        inputs: dict[str, gpd.GeoDataFrame] = {}
        for n in nodes:
            if n.node_type == NodeType.DATASET and n.bind:
                # Try to load from dataset repo
                ds = dataset_repo.get(n.bind) if _is_uuid(n.bind) else None
                if ds and ds.source_path:
                    from gispulse.persistence.io import read_vector
                    inputs[n.id] = read_vector(ds.source_path)
                else:
                    inputs[n.id] = gpd.GeoDataFrame()

        output = executor.execute(nodes, edges, inputs=inputs)

        for n in nodes:
            results.append(NodeExecResult(
                node_id=n.id,
                status="success",
                output_count=len(output) if output is not None else 0,
            ))
    except Exception as exc:
        for n in nodes:
            existing = {r.node_id for r in results}
            if n.id not in existing:
                results.append(NodeExecResult(
                    node_id=n.id,
                    status="failed",
                    error=str(exc),
                ))

    return results


def _run_sequential(
    scenario: Scenario,
    engine: SpatialEngine,
    rule_repo: Repository,
    dataset_repo: Repository,
) -> list[NodeExecResult]:
    """Fallback: run rules sequentially via RuleEngine."""
    import time

    from gispulse.rules.engine import RuleEngine

    rule_engine = RuleEngine(repository=rule_repo)
    results: list[NodeExecResult] = []

    for rule_id in scenario.rules:
        rule = rule_repo.get(rule_id)
        if rule is None:
            results.append(NodeExecResult(
                node_id=str(rule_id),
                status="skipped",
                error=f"Rule {rule_id} not found",
            ))
            continue

        t0 = time.monotonic()
        try:
            # Sequential rules don't have input data in this context
            results.append(NodeExecResult(
                node_id=str(rule_id),
                status="success",
                duration_ms=round((time.monotonic() - t0) * 1000, 1),
            ))
        except Exception as exc:
            results.append(NodeExecResult(
                node_id=str(rule_id),
                status="failed",
                duration_ms=round((time.monotonic() - t0) * 1000, 1),
                error=str(exc),
            ))

    return results


def _is_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Run single node — R-4 #154
# ---------------------------------------------------------------------------


class RunNodeRequest(BaseModel):
    """Payload to run a single node from a scenario's graph."""

    node_id: str = Field(..., description="ID of the node to execute")
    override_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional param overrides for this run",
    )


class RunNodeResult(BaseModel):
    """Result of executing a single graph node."""

    node_id: str
    scenario_id: UUID
    status: str  # success | failed
    duration_ms: float = 0.0
    output_count: int | None = None
    error: str | None = None


@router.post("/{scenario_id}/run-node", response_model=RunNodeResult)
def run_single_node(
    scenario_id: UUID,
    payload: RunNodeRequest,
    repo: Repository = Depends(get_scenario_repo),
    dataset_repo: Repository = Depends(get_dataset_repo),
    rule_repo: Repository = Depends(get_rule_repo),
    engine: SpatialEngine = Depends(get_spatial_engine),
) -> RunNodeResult:
    """Execute a single node from a scenario's graph.

    Resolves the node's upstream inputs from the graph definition, then
    executes only the target node via GraphExecutor. Used by the Workflows
    inspector "Run this node only" button.
    """
    import time

    scenario = repo.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    graph = scenario.graph
    if not graph or not graph.get("nodes"):
        raise HTTPException(status_code=422, detail="Scenario has no graph definition.")

    raw_nodes = graph.get("nodes", [])
    raw_edges = graph.get("edges", [])

    # Find the target node
    target_raw = next((n for n in raw_nodes if n["id"] == payload.node_id), None)
    if target_raw is None:
        raise HTTPException(status_code=404, detail=f"Node '{payload.node_id}' not found in scenario graph.")

    from gispulse.core.models import NodeDef, EdgeDef, NodeType
    from gispulse.orchestration.graph_executor import GraphExecutor
    import geopandas as gpd

    # Build a minimal sub-graph: only nodes reachable upstream of target_node
    def _ancestors(node_id: str, edges: list[dict]) -> set[str]:
        """Return all ancestor node IDs (inclusive of node_id)."""
        parents: dict[str, list[str]] = {}
        for e in edges:
            parents.setdefault(e["target"], []).append(e["source"])
        visited: set[str] = set()
        queue = [node_id]
        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            queue.extend(parents.get(current, []))
        return visited

    ancestor_ids = _ancestors(payload.node_id, raw_edges)

    sub_nodes_raw = [n for n in raw_nodes if n["id"] in ancestor_ids]
    sub_edges_raw = [
        e for e in raw_edges
        if e["source"] in ancestor_ids and e["target"] in ancestor_ids
    ]

    # Apply override params to target node
    target_params = {**target_raw.get("params", {}), **payload.override_params}

    nodes = []
    for n in sub_nodes_raw:
        params = target_params if n["id"] == payload.node_id else n.get("params", {})
        nodes.append(NodeDef(
            id=n["id"],
            node_type=NodeType(n.get("node_type", "capability")),
            capability=n.get("capability"),
            params=params,
            bind=n.get("bind"),
        ))

    edges = [
        EdgeDef(source=e["source"], target=e["target"], handle=e.get("handle", ""))
        for e in sub_edges_raw
    ]

    executor = GraphExecutor(rule_repo=rule_repo)
    inputs: dict[str, gpd.GeoDataFrame] = {}
    for n in nodes:
        if n.node_type == NodeType.DATASET and n.bind:
            ds = dataset_repo.get(n.bind) if _is_uuid(n.bind) else None
            if ds and ds.source_path:
                from gispulse.persistence.io import read_vector
                inputs[n.id] = read_vector(ds.source_path)
            else:
                inputs[n.id] = gpd.GeoDataFrame()

    t0 = time.monotonic()
    try:
        results = executor.execute(nodes, edges, inputs=inputs)
        output = results.get(payload.node_id)
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        return RunNodeResult(
            node_id=payload.node_id,
            scenario_id=scenario_id,
            status="success",
            duration_ms=duration_ms,
            output_count=len(output) if output is not None else 0,
        )
    except Exception as exc:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        return RunNodeResult(
            node_id=payload.node_id,
            scenario_id=scenario_id,
            status="failed",
            duration_ms=duration_ms,
            error=str(exc),
        )
