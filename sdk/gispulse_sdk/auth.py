"""Authentication helpers for the GISPulse SDK."""

from __future__ import annotations

import httpx


class APIKeyAuth(httpx.Auth):
    """Inject ``X-API-Key`` header into every request."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def auth_flow(self, request: httpx.Request):
        request.headers["X-API-Key"] = self._api_key
        yield request
