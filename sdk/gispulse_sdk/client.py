"""Main GISPulse SDK client."""

from __future__ import annotations

from functools import cached_property
from typing import Any

import httpx

from gispulse_sdk.auth import APIKeyAuth
from gispulse_sdk.exceptions import raise_for_status
from gispulse_sdk.models import CapabilityInfo, HealthResponse


class GISPulseClient:
    """Synchronous Python client for the GISPulse REST API.

    Usage::

        from gispulse_sdk import GISPulseClient

        client = GISPulseClient("https://gispulse.example.com", api_key="sk-...")
        datasets = client.datasets.list()
        print(datasets)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http = httpx.Client(
            base_url=self._base_url,
            auth=APIKeyAuth(api_key) if api_key else None,
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GISPulseClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
        files: Any = None,
    ) -> Any:
        """Send a request and return parsed JSON, raising on errors."""
        resp = self._http.request(method, path, json=json, params=params, files=files)
        if resp.status_code == 204:
            return {}
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        raise_for_status(resp.status_code, body)
        return body

    # ------------------------------------------------------------------
    # Top-level endpoints
    # ------------------------------------------------------------------

    def health(self) -> HealthResponse:
        resp = self._request("GET", "/health")
        return HealthResponse.model_validate(resp)

    def capabilities(self) -> list[CapabilityInfo]:
        resp = self._request("GET", "/capabilities")
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [CapabilityInfo.model_validate(c) for c in items]

    # ------------------------------------------------------------------
    # Endpoint groups (lazy-loaded singletons)
    # ------------------------------------------------------------------

    @cached_property
    def datasets(self):
        from gispulse_sdk.endpoints.datasets import DatasetsEndpoint
        return DatasetsEndpoint(self)

    @cached_property
    def projects(self):
        from gispulse_sdk.endpoints.projects import ProjectsEndpoint
        return ProjectsEndpoint(self)

    @cached_property
    def rules(self):
        from gispulse_sdk.endpoints.rules import RulesEndpoint
        return RulesEndpoint(self)

    @cached_property
    def triggers(self):
        from gispulse_sdk.endpoints.triggers import TriggersEndpoint
        return TriggersEndpoint(self)

    @cached_property
    def jobs(self):
        from gispulse_sdk.endpoints.jobs import JobsEndpoint
        return JobsEndpoint(self)

    @cached_property
    def scenarios(self):
        from gispulse_sdk.endpoints.scenarios import ScenariosEndpoint
        return ScenariosEndpoint(self)

    @cached_property
    def sessions(self):
        from gispulse_sdk.endpoints.sessions import SessionsEndpoint
        return SessionsEndpoint(self)

    @cached_property
    def ogc(self):
        from gispulse_sdk.endpoints.ogc import OGCEndpoint
        return OGCEndpoint(self)

    @cached_property
    def catalog(self):
        from gispulse_sdk.endpoints.catalog import CatalogEndpoint
        return CatalogEndpoint(self)

    # ------------------------------------------------------------------
    # WebSocket (requires `gispulse-sdk[ws]`)
    # ------------------------------------------------------------------

    def connect_ws(self, on_event=None):
        """Connect to the WebSocket event stream.

        Requires the ``ws`` extra: ``pip install gispulse-sdk[ws]``
        """
        from gispulse_sdk.streaming import WebSocketListener
        return WebSocketListener(
            base_url=self._base_url,
            api_key=self._api_key,
            on_event=on_event,
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def tiles_url(self, collection_id: str) -> str:
        """Return the MVT tiles URL template for use in mapping libraries."""
        return f"{self._base_url}/tiles/{collection_id}/{{z}}/{{x}}/{{y}}.mvt"

    def __repr__(self) -> str:
        return f"GISPulseClient(base_url={self._base_url!r})"
