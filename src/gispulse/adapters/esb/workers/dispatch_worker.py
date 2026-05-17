"""
DispatchWorker — Phase 3 of the GISPulse ESB pipeline.

Reads messages in PROCESSED state and dispatches spatial event
notifications via configured channels (pg_notify, webhook, etc.).

Non-blocking: notification errors do not fail the pipeline.

Adapted from Forge ESB reference (workers/dispatch_worker.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from gispulse.adapters.esb.enums import MessageStatus, WorkerType
from gispulse.adapters.esb.workers.base_worker import BaseWorker
from gispulse.core.logging import get_logger

log = get_logger(__name__)

# Default pg_notify channel name (override via constructor)
DEFAULT_PG_CHANNEL = "gispulse_events"


class DispatchWorker(BaseWorker):
    """
    Phase 3 worker: notification dispatch.

    For each PROCESSED message:
    1. Sends a pg_notify event to real-time listeners.
    2. Marks the message as COMPLETED.
    """

    worker_type = WorkerType.DISPATCH

    def __init__(
        self,
        pg_notify_channel: str = DEFAULT_PG_CHANNEL,
        pg_notify_enabled: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.pg_notify_channel = pg_notify_channel
        self.pg_notify_enabled = pg_notify_enabled

    async def run_batch(self) -> int:
        """Dispatch a batch of PROCESSED messages.

        Returns:
            Number of messages dispatched.
        """
        if not self.db_pool:
            return 0

        processed = 0

        async with self.db_pool.acquire() as conn:
            messages = await conn.fetch(
                """
                SELECT id, payload
                FROM gispulse.esb_message
                WHERE message_status = $1
                  AND dispatched = FALSE
                ORDER BY message_priority ASC, created_at ASC
                LIMIT $2
                FOR UPDATE SKIP LOCKED
                """,
                MessageStatus.PROCESSED.value,
                self.batch_size,
            )

            for msg in messages:
                await self._dispatch_message(conn, dict(msg))
                processed += 1

        return processed

    async def _dispatch_message(
        self,
        conn: Any,
        msg: dict[str, Any],
    ) -> None:
        """Dispatch a single message and mark it completed."""
        msg_id = msg["id"]
        raw_payload = msg["payload"]
        if isinstance(raw_payload, str):
            payload = json.loads(raw_payload)
        else:
            payload = raw_payload or {}

        # Mark as dispatching
        await conn.execute(
            """
            UPDATE gispulse.esb_message
            SET message_status = $1,
                updated_at     = NOW()
            WHERE id = $2
            """,
            MessageStatus.DISPATCHING.value,
            msg_id,
        )

        # Send pg_notify (non-blocking — errors are swallowed)
        if self.pg_notify_enabled:
            await self._send_pg_notify(conn, payload, msg_id)

        # Mark as completed
        await conn.execute(
            """
            UPDATE gispulse.esb_message
            SET message_status = $1,
                dispatched     = TRUE,
                dispatched_at  = NOW(),
                completed_at   = NOW(),
                updated_at     = NOW()
            WHERE id = $2
            """,
            MessageStatus.COMPLETED.value,
            msg_id,
        )

    async def _send_pg_notify(
        self,
        conn: Any,
        payload: dict[str, Any],
        message_id: Any,
    ) -> None:
        """Send a NOTIFY on the configured channel.

        The JSON payload structure::

            {
                "dataset_id":  "<uuid>",
                "layer_id":    "<uuid>",
                "operation":   "UPDATE",
                "message_id":  "<uuid>",
                "timestamp":   "ISO-8601"
            }
        """
        try:
            notify_payload = json.dumps(
                {
                    "dataset_id": str(payload.get("dataset_id", "")),
                    "layer_id": str(payload.get("layer_id", "")),
                    "operation": payload.get("operation", ""),
                    "message_id": str(message_id),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            await conn.execute(
                "SELECT pg_notify($1, $2)",
                self.pg_notify_channel,
                notify_payload,
            )
        except Exception as exc:
            # Notification failure is non-fatal
            log.warning("pg_notify_failed", worker=self.name, error=str(exc))
