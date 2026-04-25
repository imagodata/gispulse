"""In-process event hub for broadcasting live events to WebSocket clients.

Phase 3 live sync: when data changes (trigger fires, job completes, layer
updated), the hub broadcasts a JSON event to all connected viewers.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from core.logging import get_logger

log = get_logger(__name__)


class EventHub:
    """Fan-out broadcaster for WebSocket clients.

    Thread-safe: uses an asyncio Queue per subscriber so producers can
    call :meth:`broadcast` from any thread/task.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[str]] = []

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._subscribers.append(q)
        log.debug("event_hub_subscribe", total=len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass
        log.debug("event_hub_unsubscribe", total=len(self._subscribers))

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Push an event to every subscriber queue."""
        payload = json.dumps(
            {
                "type": event_type,
                "data": data or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning("event_hub_queue_full")

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Singleton — attached to app.state in create_app
_hub: EventHub | None = None


def get_event_hub() -> EventHub:
    global _hub
    if _hub is None:
        _hub = EventHub()
    return _hub
