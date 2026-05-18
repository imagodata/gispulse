"""
Rules router for the GISPulse HTTP API.

Endpoints:
    POST   /rules                  — create a rule
    GET    /rules                  — list all rules
    GET    /rules/{id}             — detail for a single rule
    PUT    /rules/{id}             — update a rule
    DELETE /rules/{id}             — delete a rule
    POST   /rules/{id}/validate    — validate a rule's configuration
    GET    /rules/{id}/to-node     — convert rule to node definition (R-4 #155)
    POST   /rules/from-node        — create a rule from a node template (R-4 #155)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gispulse.adapters.http.rate_limit import limiter
from gispulse.adapters.http.dependencies import get_rule_repo
from gispulse.adapters.http.schemas import (
    RuleCreate,
    RuleResponse,
    ValidationErrorItem,
    ValidationErrorResponse,
)
from gispulse.core.models import Rule
from gispulse.persistence.repository import Repository
from gispulse.rules.validation import validate_rule

router = APIRouter(prefix="/rules", tags=["rules"])


def _rule_to_response(rule: Rule) -> RuleResponse:
    return RuleResponse(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        scope=rule.scope,
        capability=rule.capability,
        config=rule.config,
        enabled=rule.enabled,
    )


def _ensure_valid(rule: Rule) -> None:
    """Validate a rule before persisting; raise 400 with details on failure."""
    result = validate_rule(rule)
    if not result.valid:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Rule validation failed.",
                "errors": [
                    {"field": e.field, "message": e.message} for e in result.errors
                ],
            },
        )


@router.post("", response_model=RuleResponse, status_code=201)
@limiter.limit("10/minute")
def create_rule(
    request: Request,
    payload: RuleCreate,
    repo: Repository = Depends(get_rule_repo),
) -> RuleResponse:
    """Create a new Rule and persist it in the in-memory repository."""
    rule = Rule(
        name=payload.name,
        description=payload.description,
        scope=payload.scope,
        capability=payload.capability,
        config=payload.config,
        enabled=payload.enabled,
    )
    _ensure_valid(rule)
    repo.save(rule)
    return _rule_to_response(rule)


@router.get("")
def list_rules(
    limit: int = 50,
    offset: int = 0,
    repo: Repository = Depends(get_rule_repo),
) -> dict:
    """Return paginated rules."""
    all_items = repo.list_all()
    total = len(all_items)
    page = all_items[offset : offset + limit]
    return {
        "items": [_rule_to_response(r) for r in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{rule_id}", response_model=RuleResponse)
def get_rule(
    rule_id: UUID,
    repo: Repository = Depends(get_rule_repo),
) -> RuleResponse:
    """Return a single rule by UUID.

    Raises:
        404: If the rule does not exist.
    """
    rule = repo.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    return _rule_to_response(rule)  # type: ignore[arg-type]


@router.put("/{rule_id}", response_model=RuleResponse)
def update_rule(
    rule_id: UUID,
    payload: RuleCreate,
    repo: Repository = Depends(get_rule_repo),
) -> RuleResponse:
    """Update an existing rule.

    Raises:
        404: If the rule does not exist.
    """
    rule = repo.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")

    rule.name = payload.name
    rule.description = payload.description
    rule.scope = payload.scope
    rule.capability = payload.capability
    rule.config = payload.config
    rule.enabled = payload.enabled
    _ensure_valid(rule)
    repo.save(rule)
    return _rule_to_response(rule)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(
    rule_id: UUID,
    repo: Repository = Depends(get_rule_repo),
) -> None:
    """Delete a rule by UUID.

    Raises:
        404: If the rule does not exist.
    """
    deleted = repo.delete(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")


@router.post("/{rule_id}/validate", response_model=ValidationErrorResponse)
def validate_rule_endpoint(
    rule_id: UUID,
    repo: Repository = Depends(get_rule_repo),
) -> ValidationErrorResponse:
    """Validate the configuration of an existing rule.

    Raises:
        404: If the rule does not exist.
    """
    rule = repo.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")

    result = validate_rule(rule)  # type: ignore[arg-type]
    return ValidationErrorResponse(
        valid=result.valid,
        errors=[
            ValidationErrorItem(field=e.field, message=e.message)
            for e in result.errors
        ],
    )


# ---------------------------------------------------------------------------
# Rule <-> Node template conversion — R-4 #155
# ---------------------------------------------------------------------------


class NodeDefinition(BaseModel):
    """A node definition compatible with the Workflows canvas."""

    node_type: str = "capability"
    capability: str
    label: str
    params: dict[str, Any] = Field(default_factory=dict)
    rule_id: str | None = None
    rule_name: str | None = None
    description: str | None = None


class NodeFromRulePayload(BaseModel):
    """Payload to create a Rule from a node template."""

    node_type: str = "capability"
    capability: str
    label: str
    params: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    scope: str | None = None


@router.get("/{rule_id}/to-node", response_model=NodeDefinition)
def rule_to_node(
    rule_id: UUID,
    repo: Repository = Depends(get_rule_repo),
) -> NodeDefinition:
    """Convert a Rule into a pre-configured node definition.

    The returned object can be dropped directly onto the Workflows canvas
    as a CapabilityNode pre-filled with the rule's capability and config.

    Raises:
        404: If the rule does not exist.
    """
    rule = repo.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")

    return NodeDefinition(
        node_type="capability",
        capability=rule.capability,
        label=rule.name,
        params=dict(rule.config),
        rule_id=str(rule.id),
        rule_name=rule.name,
        description=rule.description,
    )


@router.post("/from-node", response_model=RuleResponse, status_code=201)
def rule_from_node(
    payload: NodeFromRulePayload,
    repo: Repository = Depends(get_rule_repo),
) -> RuleResponse:
    """Create a Rule from a node template (Save as Rule).

    Converts a Workflows canvas node into a reusable Rule that appears
    in the My Rules section of the node palette.
    """
    rule = Rule(
        name=payload.label,
        description=payload.description or f"Rule saved from node '{payload.label}'",
        scope=payload.scope or "",
        capability=payload.capability,
        config=payload.params,
        enabled=True,
    )
    _ensure_valid(rule)
    repo.save(rule)
    return _rule_to_response(rule)
