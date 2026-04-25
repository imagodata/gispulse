"""Project endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from gispulse_sdk.models import ProjectResponse

if TYPE_CHECKING:
    from gispulse_sdk.client import GISPulseClient


class ProjectsEndpoint:
    def __init__(self, client: GISPulseClient):
        self._c = client

    def create(self, name: str, **kwargs: Any) -> ProjectResponse:
        resp = self._c._request("POST", "/projects", json={"name": name, **kwargs})
        return ProjectResponse.model_validate(resp)

    def list(self, limit: int = 100, offset: int = 0) -> list[ProjectResponse]:
        resp = self._c._request("GET", "/projects", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [ProjectResponse.model_validate(p) for p in items]

    def get(self, project_id: UUID | str) -> ProjectResponse:
        resp = self._c._request("GET", f"/projects/{project_id}")
        return ProjectResponse.model_validate(resp)

    def delete(self, project_id: UUID | str) -> dict:
        return self._c._request("DELETE", f"/projects/{project_id}")

    def update(self, project_id: UUID | str, **kwargs: Any) -> ProjectResponse:
        resp = self._c._request("PUT", f"/projects/{project_id}", json=kwargs)
        return ProjectResponse.model_validate(resp)

    def add_dataset(self, project_id: UUID | str, dataset_id: UUID | str) -> dict:
        return self._c._request("POST", f"/projects/{project_id}/datasets/{dataset_id}")

    def remove_dataset(self, project_id: UUID | str, dataset_id: UUID | str) -> dict:
        return self._c._request("DELETE", f"/projects/{project_id}/datasets/{dataset_id}")

    def layers(self, project_id: UUID | str) -> list[dict]:
        return self._c._request("GET", f"/projects/{project_id}/layers")

    def detect_relations(self, project_id: UUID | str) -> dict:
        return self._c._request("POST", f"/projects/{project_id}/detect-relations")

    def relations(self, project_id: UUID | str) -> list[dict]:
        return self._c._request("GET", f"/projects/{project_id}/relations")

    def stats(self, project_id: UUID | str) -> dict:
        return self._c._request("GET", f"/projects/{project_id}/stats")

    def activity(self, project_id: UUID | str) -> list[dict]:
        return self._c._request("GET", f"/projects/{project_id}/activity")
