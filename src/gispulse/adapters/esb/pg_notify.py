"""
PgNotifyChannel — Real-time notification channel for GISPulse ESB.

Uses PostgreSQL LISTEN/NOTIFY to push spatial event notifications to
connected clients (Python workers, Studio, external webhooks).

Adapted from Forge ESB reference (channels/pg_notify.py).

Usage::

    # Sender side (DispatchWorker)
    channel = PgNotifyChannel(db_pool, channel_name="gispulse_events")
    await channel.send(
        dataset_id="<uuid>",
        layer_id="<uuid>",
        operation="UPDATE",
    )

    # Listener side (client)
    listener = PgNotifyListener(dsn="postgresql://...")
    await listener.start(callback=my_handler)
    # my_handler(connection, pid, channel, payload) called on each NOTIFY
    await listener.stop()
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from gispulse.core.logging import get_logger

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]

_log = get_logger(__name__)

# Default channel name — override via constructor
DEFAULT_CHANNEL = "gispulse_events"


class PgNotifyChannel:
    """
    Sends NOTIFY payloads to a PostgreSQL channel.

    The JSON payload structure::

        {
            "dataset_id": "<uuid>",
            "layer_id":   "<uuid>",
            "operation":  "INSERT" | "UPDATE" | "DELETE" | "PROCESS",
            "timestamp":  "2026-03-26T12:00:00"
        }
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        channel_name: str = DEFAULT_CHANNEL,
    ) -> None:
        self.db_pool = db_pool
        self.channel = channel_name

    async def send(
        self,
        operation: str,
        dataset_id: Optional[str] = None,
        layer_id: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """Send a NOTIFY to all LISTEN clients.

        Args:
            operation:  Spatial event type: 'INSERT', 'UPDATE', 'DELETE', 'PROCESS'.
            dataset_id: UUID string of the affected Dataset (optional).
            layer_id:   UUID string of the affected Layer (optional).
            extra:      Additional key/value pairs to include in the payload.
        """
        payload: dict[str, Any] = {
            "operation": operation,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if dataset_id:
            payload["dataset_id"] = dataset_id
        if layer_id:
            payload["layer_id"] = layer_id
        if extra:
            payload.update(extra)

        payload_str = json.dumps(payload)

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                "SELECT pg_notify($1, $2)",
                self.channel,
                payload_str,
            )


class PgNotifyListener:
    """
    Listens for NOTIFY events on a PostgreSQL channel.

    Features auto-reconnection with exponential backoff on connection loss.

    Usage::

        async def on_event(connection, pid, channel, payload):
            data = json.loads(payload)
            print(f"Event received: {data['operation']} on {data.get('dataset_id')}")

        listener = PgNotifyListener(dsn="postgresql+asyncpg://user:pass@host/db")
        await listener.start(callback=on_event)
        # ... listener runs in background, auto-reconnects on failure
        await listener.stop()
    """

    # Backoff config
    _INITIAL_BACKOFF = 1.0
    _MAX_BACKOFF = 60.0
    _BACKOFF_FACTOR = 2.0

    def __init__(
        self,
        dsn: str,
        channel_name: str = DEFAULT_CHANNEL,
    ) -> None:
        self.dsn = dsn
        self.channel = channel_name
        self._conn: Optional[asyncpg.Connection] = None
        self._callback: Optional[Callable] = None
        self._running = False
        self._reconnect_task: Optional[Any] = None

    async def start(self, callback: Callable) -> None:
        """Open a dedicated connection and register the LISTEN handler.

        Args:
            callback: Async or sync callable with signature
                      ``(connection, pid, channel, payload) -> None``.
        """
        self._callback = callback
        self._running = True
        await self._connect()

    async def _connect(self) -> None:
        """Establish connection and register listener."""
        self._conn = await asyncpg.connect(self.dsn)
        await self._conn.add_listener(self.channel, self._callback)

        # Register a connection termination handler for auto-reconnect
        self._conn.add_termination_listener(self._on_connection_lost)
        _log.info("pg_notify_listener_connected", channel=self.channel)

    def _on_connection_lost(self, conn: asyncpg.Connection) -> None:
        """Handle unexpected connection loss — schedule reconnection."""
        import asyncio

        if not self._running:
            return
        _log.warning("pg_notify_connection_lost", channel=self.channel)
        try:
            loop = asyncio.get_running_loop()
            self._reconnect_task = loop.create_task(self._reconnect_loop())
        except RuntimeError:
            pass

    async def _reconnect_loop(self) -> None:
        """Reconnect with exponential backoff."""
        import asyncio

        backoff = self._INITIAL_BACKOFF

        while self._running:
            _log.info("pg_notify_reconnecting", backoff=backoff, channel=self.channel)
            await asyncio.sleep(backoff)
            try:
                self._conn = None
                await self._connect()
                _log.info("pg_notify_reconnected", channel=self.channel)
                return
            except Exception as exc:
                _log.warning("pg_notify_reconnect_failed", error=str(exc), backoff=backoff)
                backoff = min(backoff * self._BACKOFF_FACTOR, self._MAX_BACKOFF)

    async def stop(self) -> None:
        """Remove the listener and close the connection."""
        self._running = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._conn:
            if self._callback:
                try:
                    await self._conn.remove_listener(self.channel, self._callback)
                except Exception as exc:
                    _log.warning("pg_notify_remove_listener_failed", error=str(exc))
            await self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# P-6 #75 — Session-scoped channel convention
# ---------------------------------------------------------------------------

SESSION_CHANNEL_PREFIX = "gispulse_sess_"


def session_channel(schema_name: str) -> str:
    """Return the pg_notify channel name for a session schema.

    Convention: ``gispulse_sess_{schema_name}``

    Example::

        channel = session_channel("sess_abc123")
        # → "gispulse_sess_sess_abc123"

        listener = PgNotifyListener(dsn, channel_name=channel)
        await listener.start(callback=on_change)
    """
    return f"{SESSION_CHANNEL_PREFIX}{schema_name}"
