"""WebSocket router for Phase 3 live sync.

Clients connect to ``/ws/events`` and receive JSON-encoded events whenever
data changes occur (trigger fires, job completes, layer updated, etc.).

Authentication: when ``GISPULSE_API_KEYS`` is set, clients must provide a
valid API key either as a ``token`` query parameter or in the first message.
When auth is disabled (dev mode), all connections are accepted.

In ``GISPULSE_ENV=production`` we fail-closed: an unconfigured server
(no API keys, no OIDC) refuses every WS connection with close code 1008
(Policy Violation) — see P0-1 below.

## Delivery semantics: at-least-once

Each ``dml.changed`` event includes a monotonically-increasing ``change_id``
(scoped per dataset_id). Clients MUST deduplicate by ``change_id`` to handle
replay (transient broadcast errors, ``mark_changes_processed`` retries on
read-only GPKG, watcher restart with un-acked rows in ``_gispulse_change_log``).

The reference SDK helper is ``gispulse_sdk.streaming.dedupe_by_change_id``
(applied by default in ``subscribe_events()``).

## WARNING: single-tenant only

Events are broadcast to ALL subscribers without project/tenant isolation.
GISPulse Community is single-tenant — running multiple users/projects
on the same instance LEAKS DML metadata (table name, fid, timestamp,
operation) across them.

For multi-tenant deployment, use Pro tier (``pro_tenant_isolation``,
V1.2+).

Protocol (server -> client)::

    {"type": "layer_updated", "data": {"table": "public.parcelles"}, "timestamp": "..."}
    {"type": "trigger_fired", "data": {"trigger_id": "...", "operation": "INSERT"}, "timestamp": "..."}
    {"type": "job_completed", "data": {"job_id": "..."}, "timestamp": "..."}
    {"type": "dml.changed", "data": {"table": "...", "op": "INSERT", "fid": "42", "change_id": 1, "ts": "..."}, "timestamp": "..."}
"""

from __future__ import annotations

import asyncio
import hmac
import os

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

# P0-1: log a single warning at first dev-open WS connection so operators
# see the unauthenticated config without spamming logs on every connect.
_DEV_OPEN_WS_WARNED = False


def _is_oidc_configured(websocket: WebSocket) -> bool:
    """Return True when an OIDC provider is wired on app.state."""
    return getattr(websocket.app.state, "oidc_provider", None) is not None


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    """Stream live events to the portal viewer with heartbeat ping/pong."""
    global _DEV_OPEN_WS_WARNED

    api_keys = _get_ws_api_keys()
    env_is_production = os.environ.get("GISPULSE_ENV") == "production"
    has_oidc = _is_oidc_configured(websocket)

    # P0-1: fail-closed in production when nothing authenticates the WS.
    # Use 1008 (Policy Violation) to align with other WS rejects below
    # (4401 = custom unauthorized, 1008 = generic policy reject before
    # accept). 1011 is reserved for unexpected server failures.
    if env_is_production and not api_keys and not has_oidc:
        log.error(
            "ws_fail_closed_production",
            reason="no_api_keys_and_no_oidc",
        )
        await websocket.close(code=1008, reason="server_not_configured")
        return

    # Dev / test convenience: when there's no auth at all, log a one-shot
    # WARNING so engineers running locally don't accidentally ship an
    # open WS to staging.
    if not api_keys and not has_oidc and not _DEV_OPEN_WS_WARNED:
        log.warning(
            "ws_dev_open_no_auth",
            detail=(
                "GISPULSE_API_KEYS empty and no OIDC provider — /ws/events "
                "is unauthenticated. This is fine for development; in "
                "GISPULSE_ENV=production the server fails closed."
            ),
        )
        _DEV_OPEN_WS_WARNED = True

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
