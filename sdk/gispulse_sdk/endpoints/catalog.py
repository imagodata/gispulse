"""Catalog endpoints for external data discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gispulse_sdk.client import GISPulseClient


class CatalogEndpoint:
    def __init__(self, client: GISPulseClient):
        self._c = client

    def providers(self) -> list[dict]:
        return self._c._request("GET", "/api/catalog/providers")

    def projections(self, query: str = "") -> list[dict]:
        return self._c._request("GET", "/api/catalog/projections", params={"q": query})

    def basemaps(self) -> list[dict]:
        return self._c._request("GET", "/api/catalog/basemaps")

    def flux(self, query: str = "") -> list[dict]:
        return self._c._request("GET", "/api/catalog/flux", params={"q": query})

    def opendata(self, query: str = "") -> list[dict]:
        return self._c._request("GET", "/api/catalog/opendata", params={"q": query})

    def search(self, query: str) -> list[dict]:
        return self._c._request("GET", "/api/catalog/search", params={"q": query})

    def entry(self, entry_id: str) -> dict:
        return self._c._request("GET", f"/api/catalog/entry/{entry_id}")
