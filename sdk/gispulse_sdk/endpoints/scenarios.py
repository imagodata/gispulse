"""Scenario endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from gispulse_sdk.models import ScenarioCreate, ScenarioResponse

if TYPE_CHECKING:
    from gispulse_sdk.client import GISPulseClient


class ScenariosEndpoint:
    def __init__(self, client: GISPulseClient):
        self._c = client

    def create(self, scenario: ScenarioCreate) -> ScenarioResponse:
        resp = self._c._request("POST", "/scenarios", json=scenario.model_dump(mode="json"))
        return ScenarioResponse.model_validate(resp)

    def list(self, limit: int = 100, offset: int = 0) -> list[ScenarioResponse]:
        resp = self._c._request("GET", "/scenarios", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [ScenarioResponse.model_validate(s) for s in items]

    def get(self, scenario_id: UUID | str) -> ScenarioResponse:
        resp = self._c._request("GET", f"/scenarios/{scenario_id}")
        return ScenarioResponse.model_validate(resp)

    def update(self, scenario_id: UUID | str, **kwargs: Any) -> ScenarioResponse:
        resp = self._c._request("PUT", f"/scenarios/{scenario_id}", json=kwargs)
        return ScenarioResponse.model_validate(resp)

    def delete(self, scenario_id: UUID | str) -> dict:
        return self._c._request("DELETE", f"/scenarios/{scenario_id}")

    def run(self, scenario_id: UUID | str) -> dict:
        """Execute the full scenario pipeline."""
        return self._c._request("POST", f"/scenarios/{scenario_id}/run")

    def run_node(self, scenario_id: UUID | str, node_id: str) -> dict:
        """Execute a single node within a scenario."""
        return self._c._request(
            "POST",
            f"/scenarios/{scenario_id}/run-node",
            json={"node_id": node_id},
        )
