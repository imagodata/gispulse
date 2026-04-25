"""Capabilities endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gispulse_sdk.models import CapabilityInfo

if TYPE_CHECKING:
    from gispulse_sdk.client import GISPulseClient


class CapabilitiesEndpoint:
    def __init__(self, client: GISPulseClient):
        self._c = client

    def list(self) -> list[CapabilityInfo]:
        resp = self._c._request("GET", "/capabilities")
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [CapabilityInfo.model_validate(c) for c in items]

    def get(self, name: str) -> CapabilityInfo:
        resp = self._c._request("GET", f"/capabilities/{name}")
        return CapabilityInfo.model_validate(resp)

    def sql_preview(self, query: str) -> dict:
        return self._c._request("POST", "/capabilities/sql-preview", json={"query": query})
