"""WebSocket router for Phase 3 live sync.

Clients connect to ``/ws/events`` and receive JSON-encoded events whenever
data changes occur (trigger fires, job completes, layer updated, etc.).

Authentication: when ``GISPULSE_API_KEYS`` is set, clients must provide a
valid API key either as a ``token`` query parameter or in the first message.
When auth is disabled (dev mode), all connections are accepted.

Protocol (server -> client)::

    {"type": "layer_updated", "data": {"table": "public.parcelles"}, "timestamp": "..."}
    {"type": "trigger_fired", "data": {"trigger_id": "...", "operation": "INSERT"}, "timestamp": "..."}
    {"type": "job_completed", "data": {"job_id": "..."}, "timestamp": "..."}
"""

from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from gispulse.adapters.http.event_hub import get_event_hub
from core.config import settings as cfg
from core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["websocket"])


def _get_ws_api_keys() -> set[str] | None:
    return cfg.api.get_api_keys_set()


# Heartbeat interval in seconds
_HEARTBEAT_INTERVAL = 30
# Maximum payload size for outgoing WebSocket messages (1 MB)
_MAX_WS_PAYLOAD = 1_000_000


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    """Stream live events to the portal viewer with heartbeat ping/pong."""
    api_keys = _get_ws_api_keys()

    # Authenticate via query param ?token=<key>
    if api_keys:
        token = websocket.query_params.get("token", "")
        if not any(hmac.compare_digest(token, k) for k in api_keys):
            await websocket.close(code=4401, reason="Unauthorized")
            log.warning("ws_client_rejected", reason="invalid_token")
            return

    await websocket.accept()
    hub = get_event_hub()

    # Optional filters — see #452 / OSSi-C3
    # ?topics=trigger_fired,layer_updated   (csv, single param)
    # ?trigger_id=<uuid>                    (repeatable)
    # ?table=public.parcels                 (repeatable)
    qp = websocket.query_params
    topics_csv = qp.get("topics")
    topics = [t.strip() for t in topics_csv.split(",") if t.strip()] if topics_csv else None
    trigger_ids = qp.getlist("trigger_id") or None
    tables = qp.getlist("table") or None

    queue = hub.subscribe(topics=topics, trigger_ids=trigger_ids, tables=tables)
    log.info("ws_client_connected", clients=hub.subscriber_count)

    async def _heartbeat() -> None:
        """Send periodic pings to detect dead connections."""
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                await websocket.send_json({"type": "ping"})
        except (WebSocketDisconnect, asyncio.CancelledError, Exception):
            pass

    heartbeat_task = asyncio.create_task(_heartbeat())
    try:
        while True:
            payload = await queue.get()
            if len(payload) > _MAX_WS_PAYLOAD:
                log.warning("ws_payload_too_large", size=len(payload))
                continue
            await websocket.send_text(payload)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        heartbeat_task.cancel()
        hub.unsubscribe(queue)
        log.info("ws_client_disconnected", clients=hub.subscriber_count)
