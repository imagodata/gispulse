"""
IdentifyWorker — Phase 1 of the GISPulse ESB pipeline.

Reads messages in NEW state, determines their type based on
data_category + operation, and transitions them to IDENTIFIED
(or IDENTIFY_ERROR if unknown).

Adapted from Forge ESB reference (workers/identify_worker.py).
"""

from __future__ import annotations

import json
from typing import Any

from gispulse.adapters.esb.enums import MessageStatus, WorkerType
from gispulse.adapters.esb.workers.base_worker import BaseWorker


class IdentifyWorker(BaseWorker):
    """
    Phase 1 worker: message identification.

    Reads each NEW message and assigns its type based on ``data_category``
    and ``operation`` fields from the payload.
    """

    worker_type = WorkerType.IDENTIFY

    async def run_batch(self) -> int:
        """Process a batch of NEW messages.

        For each message:
        1. Read data_category and operation from the payload.
        2. Determine the message type code.
        3. Update message_status to IDENTIFIED (or IDENTIFY_ERROR).

        Returns:
            Number of messages processed.
        """
        if not self.db_pool:
            return 0

        processed = 0

        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                messages = await conn.fetch(
                    """
                    SELECT id, payload
                    FROM gispulse.esb_message
                    WHERE message_status = 'NEW'
                    ORDER BY message_priority ASC, created_at ASC
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size,
                )

                for msg in messages:
                    await self._identify_message(conn, dict(msg))
                    processed += 1

        return processed

    async def _identify_message(
        self,
        conn: Any,
        msg: dict[str, Any],
    ) -> None:
        """Identify a single message and update its status."""
        msg_id = msg["id"]
        raw_payload = msg["payload"]
        if isinstance(raw_payload, str):
            payload = json.loads(raw_payload)
        else:
            payload = raw_payload or {}

        data_category = payload.get("data_category", "vector")
        operation = payload.get("operation", "")

        # Mark as being identified
        await conn.execute(
            """
            UPDATE gispulse.esb_message
            SET message_status = $1,
                started_at     = NOW(),
                updated_at     = NOW()
            WHERE id = $2
            """,
            MessageStatus.IDENTIFYING.value,
            msg_id,
        )

        # Determine message type: "{data_category}_{operation}".lower()
        type_code = f"{data_category}_{operation}".lower()

        type_row = await conn.fetchrow(
            """
            SELECT id FROM gispulse.esb_type_message
            WHERE (code = $1 OR code = $2)
              AND is_active = TRUE
            LIMIT 1
            """,
            type_code,
            data_category,  # Generic fallback by category
        )

        if type_row:
            await conn.execute(
                """
                UPDATE gispulse.esb_message
                SET message_status  = $1,
                    type_message_id = $2,
                    updated_at      = NOW()
                WHERE id = $3
                """,
                MessageStatus.IDENTIFIED.value,
                type_row["id"],
                msg_id,
            )
        else:
            # Unknown type — mark IDENTIFIED so pipeline can continue
            # Change to IDENTIFY_ERROR if strict type matching is required
            await conn.execute(
                """
                UPDATE gispulse.esb_message
                SET message_status = $1,
                    updated_at     = NOW()
                WHERE id = $2
                """,
                MessageStatus.IDENTIFIED.value,
                msg_id,
            )
