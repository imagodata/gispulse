"""In-process event hub for broadcasting live events to WebSocket clients.

Phase 3 live sync: when data changes (trigger fires, job completes, layer
updated), the hub broadcasts a JSON event to all connected viewers.

Subscriptions can opt into per-topic, per-trigger, or per-table filtering
so a client only receives the events it cares about (saves bandwidth on
busy hubs and unblocks third-party webmapping integrations).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.logging import get_logger

log = get_logger(__name__)


@dataclass
class Subscription:
    """A single client subscription with optional filters.

    Each filter is ``None`` for "no filter" (= wildcard). When a filter
    is set, it applies; if the event's ``data`` lacks the expected key,
    the event is **not** excluded — defensive default (R3.1) so a
    payload that simply doesn't carry a ``trigger_id`` or ``table`` field
    isn't silently dropped.
    """

    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    topics: frozenset[str] | None = None
    trigger_ids: frozenset[str] | None = None
    tables: frozenset[str] | None = None


class EventHub:
    """Fan-out broadcaster for WebSocket clients with optional filtering.

    Thread-safe: uses an asyncio Queue per subscriber so producers can
    call :meth:`broadcast` from any thread/task.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscription] = []
        # P0-4b (Beta): drop-counter so QueueFull events are observable.
        # Slow subscribers silently lose events when their per-sub queue
        # saturates at maxsize=1000; we now log error + bump the counter.
        self._dropped_total: int = 0

    def subscribe(
        self,
        *,
        topics: frozenset[str] | set[str] | list[str] | None = None,
        trigger_ids: frozenset[str] | set[str] | list[str] | None = None,
        tables: frozenset[str] | set[str] | list[str] | None = None,
    ) -> asyncio.Queue[str]:
        """Register a subscriber and return its queue.

        All filters are optional — omit for wildcard (= every event).
        Backward-compat: ``hub.subscribe()`` (no args) keeps the
        pre-filter behavior of receiving every broadcast.
        """
        sub = Subscription(
            topics=frozenset(topics) if topics else None,
            trigger_ids=frozenset(str(t) for t in trigger_ids) if trigger_ids else None,
            tables=frozenset(tables) if tables else None,
        )
        self._subscribers.append(sub)
        log.debug(
            "event_hub_subscribe",
            total=len(self._subscribers),
            has_topic_filter=sub.topics is not None,
            has_trigger_filter=sub.trigger_ids is not None,
            has_table_filter=sub.tables is not None,
        )
        return sub.queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        """Drop the subscriber owning *queue*."""
        for sub in list(self._subscribers):
            if sub.queue is queue:
                self._subscribers.remove(sub)
                break
        log.debug("event_hub_unsubscribe", total=len(self._subscribers))

    @staticmethod
    def _should_send(sub: Subscription, event_type: str, data: dict[str, Any]) -> bool:
        """Decide whether an event matches a subscription's filters.

        Filters combine with AND. Missing fields in ``data`` are
        tolerated (they don't exclude the event) — see R3.1 in the spec.
        """
        if sub.topics is not None and event_type not in sub.topics:
            return False
        if sub.trigger_ids is not None:
            tid = data.get("trigger_id")
            if tid is not None and str(tid) not in sub.trigger_ids:
                return False
        if sub.tables is not None:
            tbl = data.get("table")
            if tbl is not None and tbl not in sub.tables:
                return False
        return True

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Push an event to every matching subscriber queue."""
        data = data or {}
        payload = json.dumps(
            {
                "type": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        for sub in self._subscribers:
            if not self._should_send(sub, event_type, data):
                continue
            try:
                sub.queue.put_nowait(payload)
            except asyncio.QueueFull:
                # P0-4b (Beta): bump from warning -> error and expose a
                # counter. A slow subscriber dropping events used to be
                # invisible; ops now have a metric to alert on.
                self._dropped_total += 1
                log.error(
                    "event_hub_queue_full_dropped",
                    subscriber_count=len(self._subscribers),
                    total_drops=self._dropped_total,
                )

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def dropped_total(self) -> int:
        """Cumulative number of events dropped because a subscriber queue
        was full. Reset only on process restart (not by ``unsubscribe``)."""
        return self._dropped_total


# Singleton — attached to app.state in create_app
_hub: EventHub | None = None


def get_event_hub() -> EventHub:
    global _hub
    if _hub is None:
        _hub = EventHub()
    return _hub
