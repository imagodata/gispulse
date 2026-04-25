"""Async endpoint classes for the GISPulseAsyncClient.

Each class mirrors its sync counterpart but uses ``await`` for I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from gispulse_sdk.models import (
    DatasetResponse,
    FiredTriggerOut,
    JobCreate,
    JobResponse,
    ProjectResponse,
    RuleCreate,
    RuleResponse,
    ScenarioCreate,
    ScenarioResponse,
    SessionCreate,
    SessionResponse,
    TriggerCreate,
    TriggerResponse,
    ValidationErrorResponse,
)

if TYPE_CHECKING:
    from gispulse_sdk.async_client import GISPulseAsyncClient


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


class AsyncDatasetsEndpoint:
    def __init__(self, client: GISPulseAsyncClient):
        self._c = client

    async def list(self, limit: int = 100, offset: int = 0) -> list[DatasetResponse]:
        resp = await self._c._request("GET", "/datasets", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [DatasetResponse.model_validate(d) for d in items]

    async def get(self, dataset_id: UUID | str) -> DatasetResponse:
        resp = await self._c._request("GET", f"/datasets/{dataset_id}")
        return DatasetResponse.model_validate(resp)

    async def upload(self, file_path: str | Path, name: str | None = None) -> DatasetResponse:
        p = Path(file_path)
        fname = name or p.name
        with open(p, "rb") as f:
            resp = await self._c._request("POST", "/datasets/upload", files={"file": (fname, f)})
        return DatasetResponse.model_validate(resp)

    async def delete(self, dataset_id: UUID | str) -> dict:
        return await self._c._request("DELETE", f"/api/portal/datasets/{dataset_id}")

    async def features(self, dataset_id: UUID | str, layer: str | None = None, limit: int = 100, offset: int = 0) -> dict:
        layer_name = layer or "default"
        return await self._c._request(
            "GET",
            f"/api/portal/datasets/{dataset_id}/layers/{layer_name}/features",
            params={"limit": limit, "offset": offset},
        )

    async def sql(self, query: str) -> dict:
        return await self._c._request("POST", "/api/portal/sql/execute", json={"query": query})


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class AsyncProjectsEndpoint:
    def __init__(self, client: GISPulseAsyncClient):
        self._c = client

    async def create(self, name: str, **kwargs: Any) -> ProjectResponse:
        resp = await self._c._request("POST", "/projects", json={"name": name, **kwargs})
        return ProjectResponse.model_validate(resp)

    async def list(self, limit: int = 100, offset: int = 0) -> list[ProjectResponse]:
        resp = await self._c._request("GET", "/projects", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [ProjectResponse.model_validate(p) for p in items]

    async def get(self, project_id: UUID | str) -> ProjectResponse:
        resp = await self._c._request("GET", f"/projects/{project_id}")
        return ProjectResponse.model_validate(resp)

    async def delete(self, project_id: UUID | str) -> dict:
        return await self._c._request("DELETE", f"/projects/{project_id}")

    async def stats(self, project_id: UUID | str) -> dict:
        return await self._c._request("GET", f"/projects/{project_id}/stats")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class AsyncRulesEndpoint:
    def __init__(self, client: GISPulseAsyncClient):
        self._c = client

    async def create(self, rule: RuleCreate) -> RuleResponse:
        resp = await self._c._request("POST", "/rules", json=rule.model_dump())
        return RuleResponse.model_validate(resp)

    async def list(self, limit: int = 100, offset: int = 0) -> list[RuleResponse]:
        resp = await self._c._request("GET", "/rules", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [RuleResponse.model_validate(r) for r in items]

    async def get(self, rule_id: UUID | str) -> RuleResponse:
        resp = await self._c._request("GET", f"/rules/{rule_id}")
        return RuleResponse.model_validate(resp)

    async def update(self, rule_id: UUID | str, **kwargs: Any) -> RuleResponse:
        resp = await self._c._request("PUT", f"/rules/{rule_id}", json=kwargs)
        return RuleResponse.model_validate(resp)

    async def delete(self, rule_id: UUID | str) -> dict:
        return await self._c._request("DELETE", f"/rules/{rule_id}")

    async def validate(self, rule_id: UUID | str) -> ValidationErrorResponse:
        resp = await self._c._request("POST", f"/rules/{rule_id}/validate")
        return ValidationErrorResponse.model_validate(resp)


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


class AsyncTriggersEndpoint:
    def __init__(self, client: GISPulseAsyncClient):
        self._c = client

    async def create(self, trigger: TriggerCreate) -> TriggerResponse:
        resp = await self._c._request("POST", "/triggers", json=trigger.model_dump(mode="json"))
        return TriggerResponse.model_validate(resp)

    async def list(self, limit: int = 100, offset: int = 0) -> list[TriggerResponse]:
        resp = await self._c._request("GET", "/triggers", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [TriggerResponse.model_validate(t) for t in items]

    async def get(self, trigger_id: UUID | str) -> TriggerResponse:
        resp = await self._c._request("GET", f"/triggers/{trigger_id}")
        return TriggerResponse.model_validate(resp)

    async def toggle(self, trigger_id: UUID | str) -> TriggerResponse:
        resp = await self._c._request("POST", f"/triggers/{trigger_id}/toggle")
        return TriggerResponse.model_validate(resp)

    async def delete(self, trigger_id: UUID | str) -> dict:
        return await self._c._request("DELETE", f"/triggers/{trigger_id}")

    async def evaluate(self, trigger_id: UUID | str, records: list[dict]) -> list[FiredTriggerOut]:
        resp = await self._c._request("POST", f"/triggers/{trigger_id}/evaluate", json={"records": records})
        items = resp if isinstance(resp, list) else [resp]
        return [FiredTriggerOut.model_validate(r) for r in items]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class AsyncJobsEndpoint:
    def __init__(self, client: GISPulseAsyncClient):
        self._c = client

    async def create(self, job: JobCreate) -> JobResponse:
        resp = await self._c._request("POST", "/jobs", json=job.model_dump(mode="json"))
        return JobResponse.model_validate(resp)

    async def list(self, limit: int = 100, offset: int = 0) -> list[JobResponse]:
        resp = await self._c._request("GET", "/jobs", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [JobResponse.model_validate(j) for j in items]

    async def get(self, job_id: UUID | str) -> JobResponse:
        resp = await self._c._request("GET", f"/jobs/{job_id}")
        return JobResponse.model_validate(resp)

    async def cancel(self, job_id: UUID | str) -> dict:
        return await self._c._request("POST", f"/jobs/{job_id}/cancel")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


class AsyncScenariosEndpoint:
    def __init__(self, client: GISPulseAsyncClient):
        self._c = client

    async def create(self, scenario: ScenarioCreate) -> ScenarioResponse:
        resp = await self._c._request("POST", "/scenarios", json=scenario.model_dump(mode="json"))
        return ScenarioResponse.model_validate(resp)

    async def list(self, limit: int = 100, offset: int = 0) -> list[ScenarioResponse]:
        resp = await self._c._request("GET", "/scenarios", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [ScenarioResponse.model_validate(s) for s in items]

    async def get(self, scenario_id: UUID | str) -> ScenarioResponse:
        resp = await self._c._request("GET", f"/scenarios/{scenario_id}")
        return ScenarioResponse.model_validate(resp)

    async def run(self, scenario_id: UUID | str) -> dict:
        return await self._c._request("POST", f"/scenarios/{scenario_id}/run")

    async def delete(self, scenario_id: UUID | str) -> dict:
        return await self._c._request("DELETE", f"/scenarios/{scenario_id}")


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class AsyncSessionsEndpoint:
    def __init__(self, client: GISPulseAsyncClient):
        self._c = client

    async def create(self, source_client: str = "sdk", ttl_hours: int = 8) -> SessionResponse:
        payload = SessionCreate(source_client=source_client, ttl_hours=ttl_hours)
        resp = await self._c._request("POST", "/sessions", json=payload.model_dump())
        return SessionResponse.model_validate(resp)

    async def list(self) -> list[SessionResponse]:
        resp = await self._c._request("GET", "/sessions")
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [SessionResponse.model_validate(s) for s in items]

    async def get(self, session_id: UUID | str) -> SessionResponse:
        resp = await self._c._request("GET", f"/sessions/{session_id}")
        return SessionResponse.model_validate(resp)

    async def delete(self, session_id: UUID | str) -> dict:
        return await self._c._request("DELETE", f"/sessions/{session_id}")


# ---------------------------------------------------------------------------
# OGC
# ---------------------------------------------------------------------------


class AsyncOGCEndpoint:
    def __init__(self, client: GISPulseAsyncClient):
        self._c = client

    async def collections(self) -> list[dict]:
        resp = await self._c._request("GET", "/ogc/collections")
        return resp.get("collections", resp) if isinstance(resp, dict) else resp

    async def items(self, collection_id: str, limit: int = 100, offset: int = 0) -> dict:
        return await self._c._request(
            "GET", f"/ogc/collections/{collection_id}/items",
            params={"limit": limit, "offset": offset},
        )

    async def item(self, collection_id: str, feature_id: str) -> dict:
        return await self._c._request("GET", f"/ogc/collections/{collection_id}/items/{feature_id}")

    def items_url(self, collection_id: str) -> str:
        return f"{self._c._base_url}/ogc/collections/{collection_id}/items"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class AsyncCatalogEndpoint:
    def __init__(self, client: GISPulseAsyncClient):
        self._c = client

    async def basemaps(self) -> list[dict]:
        return await self._c._request("GET", "/api/catalog/basemaps")

    async def search(self, query: str) -> list[dict]:
        return await self._c._request("GET", "/api/catalog/search", params={"q": query})

    async def providers(self) -> list[dict]:
        return await self._c._request("GET", "/api/catalog/providers")
