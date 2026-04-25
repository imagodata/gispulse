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

    Cross-thread broadcast contract (Lot 2 v2 — Beta E2E)
    -----------------------------------------------------
    ``ChangeLogWatcher`` runs on a daemon ``threading.Thread`` and calls
    :meth:`broadcast` from outside the asyncio event loop. ``asyncio.Queue``
    is **not** thread-safe — calling ``put_nowait`` from another thread
    races against the loop's selector and silently loses events under
    contention (Beta saw 1-2/3 events arrive on a 3-tenant burst).

    The fix: at app startup the FastAPI lifespan calls :meth:`bind_loop`
    with the running loop. From that point on, :meth:`broadcast` routes
    every push through ``loop.call_soon_threadsafe`` so the actual
    ``put_nowait`` runs on the loop thread. ``QueueFull`` is caught by
    the wrapper :meth:`_safe_put` (synchronous, scheduled on the loop)
    and bumps :attr:`dropped_total`.

    Backwards-compat: when no loop is bound (unit tests instantiate the
    hub directly inside ``asyncio.run``), :meth:`broadcast` falls back
    to direct ``put_nowait``. In production the lifespan ALWAYS binds.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscription] = []
        # P0-4b (Beta): drop-counter so QueueFull events are observable.
        # Slow subscribers silently lose events when their per-sub queue
        # saturates at maxsize=1000; we now log error + bump the counter.
        self._dropped_total: int = 0
        # Lot 2 v2 (Beta E2E): captured at startup by ``bind_loop`` so
        # cross-thread producers (ChangeLogWatcher) can hand the push
        # back to the loop thread via ``call_soon_threadsafe``. Keeping
        # this nullable preserves the legacy in-loop behaviour for tests
        # that build a hub inside ``asyncio.run`` without binding.
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the asyncio loop for thread-safe broadcasting.

        Called from the FastAPI lifespan once the loop is running. After
        the call, every :meth:`broadcast` invoked from a producer thread
        routes through ``loop.call_soon_threadsafe`` instead of touching
        the queue directly.

        Idempotent: re-binding the same loop is a no-op. Re-binding a
        different loop (would only happen during teardown / test churn)
        replaces the reference.
        """
        self._loop = loop
        log.debug("event_hub_loop_bound")

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

    def _safe_put(self, queue: asyncio.Queue[str], payload: str) -> None:
        """Synchronous queue push that swallows ``QueueFull`` and counts drops.

        Designed to be scheduled on the loop thread via
        ``loop.call_soon_threadsafe`` from a producer thread. Running on
        the loop side keeps every queue mutation single-threaded.
        """
        try:
            queue.put_nowait(payload)
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
        except Exception as exc:  # pragma: no cover — defensive
            log.error("event_hub_broadcast_failed", error=str(exc))

    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Push an event to every matching subscriber queue.

        Thread-safe in production: when an event loop has been bound via
        :meth:`bind_loop`, the actual queue push is scheduled on the loop
        thread through ``call_soon_threadsafe``. This is critical because
        ``ChangeLogWatcher`` calls ``broadcast()`` from a daemon thread,
        and ``asyncio.Queue`` only guarantees thread-safety when mutated
        from the loop that owns it.
        """
        data = data or {}
        payload = json.dumps(
            {
                "type": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        # Snapshot the bound loop once: avoids a race where ``bind_loop``
        # reassigns ``self._loop`` mid-iteration.
        loop = self._loop
        loop_running = loop is not None and loop.is_running()

        for sub in self._subscribers:
            if not self._should_send(sub, event_type, data):
                continue
            if loop_running:
                # Cross-thread safe path: the loop pickups the closure and
                # runs ``_safe_put`` on its thread. ``QueueFull`` is caught
                # inside ``_safe_put`` (call_soon_threadsafe never raises
                # the user callback's exceptions back to the producer).
                try:
                    loop.call_soon_threadsafe(self._safe_put, sub.queue, payload)
                except RuntimeError as exc:
                    # Loop was closed between the snapshot and the call;
                    # fall back to the in-loop path so the event isn't
                    # silently dropped.
                    log.warning("event_hub_loop_closed_fallback", error=str(exc))
                    self._safe_put(sub.queue, payload)
            else:
                # In-loop / unit-test path: legacy behaviour preserved so
                # existing tests that build EventHub() directly inside
                # ``asyncio.run`` keep working without bind_loop().
                self._safe_put(sub.queue, payload)

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
