"""Async GISPulse SDK client for use with asyncio (FastAPI, Jupyter, etc.)."""

from __future__ import annotations

from functools import cached_property
from typing import Any

import httpx

from gispulse_sdk.auth import APIKeyAuth
from gispulse_sdk.exceptions import raise_for_status
from gispulse_sdk.models import CapabilityInfo, HealthResponse


class GISPulseAsyncClient:
    """Asynchronous Python client for the GISPulse REST API.

    Usage::

        from gispulse_sdk.async_client import GISPulseAsyncClient

        async with GISPulseAsyncClient("https://gispulse.example.com", api_key="sk-...") as client:
            datasets = await client.datasets.list()
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            auth=APIKeyAuth(api_key) if api_key else None,
            timeout=timeout,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> GISPulseAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
        files: Any = None,
    ) -> Any:
        resp = await self._http.request(method, path, json=json, params=params, files=files)
        if resp.status_code == 204:
            return {}
        ct = resp.headers.get("content-type", "")
        body = resp.json() if ct.startswith("application/json") else resp.text
        raise_for_status(resp.status_code, body)
        return body

    # ------------------------------------------------------------------
    # Top-level endpoints
    # ------------------------------------------------------------------

    async def health(self) -> HealthResponse:
        resp = await self._request("GET", "/health")
        return HealthResponse.model_validate(resp)

    async def capabilities(self) -> list[CapabilityInfo]:
        resp = await self._request("GET", "/capabilities")
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [CapabilityInfo.model_validate(c) for c in items]

    # ------------------------------------------------------------------
    # Async endpoint groups
    # ------------------------------------------------------------------

    @cached_property
    def datasets(self):
        from gispulse_sdk._async_endpoints import AsyncDatasetsEndpoint
        return AsyncDatasetsEndpoint(self)

    @cached_property
    def projects(self):
        from gispulse_sdk._async_endpoints import AsyncProjectsEndpoint
        return AsyncProjectsEndpoint(self)

    @cached_property
    def rules(self):
        from gispulse_sdk._async_endpoints import AsyncRulesEndpoint
        return AsyncRulesEndpoint(self)

    @cached_property
    def triggers(self):
        from gispulse_sdk._async_endpoints import AsyncTriggersEndpoint
        return AsyncTriggersEndpoint(self)

    @cached_property
    def jobs(self):
        from gispulse_sdk._async_endpoints import AsyncJobsEndpoint
        return AsyncJobsEndpoint(self)

    @cached_property
    def scenarios(self):
        from gispulse_sdk._async_endpoints import AsyncScenariosEndpoint
        return AsyncScenariosEndpoint(self)

    @cached_property
    def sessions(self):
        from gispulse_sdk._async_endpoints import AsyncSessionsEndpoint
        return AsyncSessionsEndpoint(self)

    @cached_property
    def ogc(self):
        from gispulse_sdk._async_endpoints import AsyncOGCEndpoint
        return AsyncOGCEndpoint(self)

    @cached_property
    def catalog(self):
        from gispulse_sdk._async_endpoints import AsyncCatalogEndpoint
        return AsyncCatalogEndpoint(self)

    def tiles_url(self, collection_id: str) -> str:
        return f"{self._base_url}/tiles/{collection_id}/{{z}}/{{x}}/{{y}}.mvt"

    def __repr__(self) -> str:
        return f"GISPulseAsyncClient(base_url={self._base_url!r})"
