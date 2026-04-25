"""Rule endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from gispulse_sdk.models import RuleCreate, RuleResponse, ValidationErrorResponse

if TYPE_CHECKING:
    from gispulse_sdk.client import GISPulseClient


class RulesEndpoint:
    def __init__(self, client: GISPulseClient):
        self._c = client

    def create(self, rule: RuleCreate) -> RuleResponse:
        resp = self._c._request("POST", "/rules", json=rule.model_dump())
        return RuleResponse.model_validate(resp)

    def list(self, limit: int = 100, offset: int = 0) -> list[RuleResponse]:
        resp = self._c._request("GET", "/rules", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [RuleResponse.model_validate(r) for r in items]

    def get(self, rule_id: UUID | str) -> RuleResponse:
        resp = self._c._request("GET", f"/rules/{rule_id}")
        return RuleResponse.model_validate(resp)

    def update(self, rule_id: UUID | str, **kwargs: Any) -> RuleResponse:
        resp = self._c._request("PUT", f"/rules/{rule_id}", json=kwargs)
        return RuleResponse.model_validate(resp)

    def delete(self, rule_id: UUID | str) -> dict:
        return self._c._request("DELETE", f"/rules/{rule_id}")

    def validate(self, rule_id: UUID | str) -> ValidationErrorResponse:
        resp = self._c._request("POST", f"/rules/{rule_id}/validate")
        return ValidationErrorResponse.model_validate(resp)

    def to_node(self, rule_id: UUID | str) -> dict:
        return self._c._request("GET", f"/rules/{rule_id}/to-node")

    def from_node(self, node_data: dict) -> RuleResponse:
        resp = self._c._request("POST", "/rules/from-node", json=node_data)
        return RuleResponse.model_validate(resp)
