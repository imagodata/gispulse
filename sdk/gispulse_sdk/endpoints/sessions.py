"""Session endpoints for ephemeral PostGIS sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from gispulse_sdk.models import SessionCreate, SessionResponse

if TYPE_CHECKING:
    from gispulse_sdk.client import GISPulseClient


class SessionsEndpoint:
    def __init__(self, client: GISPulseClient):
        self._c = client

    def create(
        self,
        source_client: str = "sdk",
        ttl_hours: int = 8,
    ) -> SessionResponse:
        payload = SessionCreate(source_client=source_client, ttl_hours=ttl_hours)
        resp = self._c._request("POST", "/sessions", json=payload.model_dump())
        return SessionResponse.model_validate(resp)

    def list(self) -> list[SessionResponse]:
        resp = self._c._request("GET", "/sessions")
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [SessionResponse.model_validate(s) for s in items]

    def get(self, session_id: UUID | str) -> SessionResponse:
        resp = self._c._request("GET", f"/sessions/{session_id}")
        return SessionResponse.model_validate(resp)

    def delete(self, session_id: UUID | str) -> dict:
        """Tear down an ephemeral session (DROP SCHEMA + ROLE)."""
        return self._c._request("DELETE", f"/sessions/{session_id}")
