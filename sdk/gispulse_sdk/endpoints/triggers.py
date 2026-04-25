"""Trigger endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator
from uuid import UUID

from gispulse_sdk.models import FiredTriggerOut, TriggerCreate, TriggerResponse

if TYPE_CHECKING:
    from gispulse_sdk.client import GISPulseClient


class TriggersEndpoint:
    def __init__(self, client: GISPulseClient):
        self._c = client

    def create(self, trigger: TriggerCreate) -> TriggerResponse:
        resp = self._c._request("POST", "/triggers", json=trigger.model_dump(mode="json"))
        return TriggerResponse.model_validate(resp)

    def list(self, limit: int = 100, offset: int = 0) -> list[TriggerResponse]:
        resp = self._c._request("GET", "/triggers", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [TriggerResponse.model_validate(t) for t in items]

    def get(self, trigger_id: UUID | str) -> TriggerResponse:
        resp = self._c._request("GET", f"/triggers/{trigger_id}")
        return TriggerResponse.model_validate(resp)

    def update(self, trigger_id: UUID | str, **kwargs: Any) -> TriggerResponse:
        resp = self._c._request("PUT", f"/triggers/{trigger_id}", json=kwargs)
        return TriggerResponse.model_validate(resp)

    def delete(self, trigger_id: UUID | str) -> dict:
        return self._c._request("DELETE", f"/triggers/{trigger_id}")

    def toggle(self, trigger_id: UUID | str) -> TriggerResponse:
        resp = self._c._request("POST", f"/triggers/{trigger_id}/toggle")
        return TriggerResponse.model_validate(resp)

    def evaluate(self, trigger_id: UUID | str, records: list[dict]) -> list[FiredTriggerOut]:
        resp = self._c._request(
            "POST",
            f"/triggers/{trigger_id}/evaluate",
            json={"records": records},
        )
        items = resp if isinstance(resp, list) else [resp]
        return [FiredTriggerOut.model_validate(r) for r in items]

    def eval_stream(self, trigger_id: UUID | str) -> Iterator[dict]:
        """Stream trigger evaluation results via SSE.

        Yields parsed event dicts. Caller should iterate in a loop::

            for event in client.triggers.eval_stream(tid):
                print(event)
        """
        url = f"{self._c._base_url}/triggers/eval-stream"
        with self._c._http.stream("GET", url, params={"trigger_id": str(trigger_id)}) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    import json
                    yield json.loads(line[5:].strip())
