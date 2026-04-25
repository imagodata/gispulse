"""WebSocket and SSE streaming helpers.

WebSocket support requires the ``ws`` extra::

    pip install gispulse-sdk[ws]

## At-least-once delivery

The server's ``dml.changed`` events are at-least-once: a bad subscriber,
a transient ``mark_changes_processed`` failure (e.g. read-only GPKG), or
a watcher restart with un-acked rows can replay the same ``change_id``
to clients. Use :func:`dedupe_by_change_id` (or :meth:`subscribe_events`
with the default ``dedupe=True``) to filter duplicates client-side.
"""

from __future__ import annotations

import collections
import json
import threading
import time
from typing import Any, AsyncIterator, Callable, Iterator, Optional


class WebSocketListener:
    """Background WebSocket listener that dispatches events via a callback.

    Usage::

        def on_event(event: dict):
            print(event["type"], event.get("data"))

        listener = client.connect_ws(on_event=on_event)
        listener.start()
        # ... do work ...
        listener.stop()
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        on_event: Callable[[dict], Any] | None = None,
        reconnect_delay: float = 3.0,
        max_reconnect_delay: float = 60.0,
    ):
        ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self._url = f"{ws_url}/ws/events"
        if api_key:
            self._url += f"?token={api_key}"
        self._on_event = on_event or (lambda e: None)
        self._reconnect_delay = reconnect_delay
        self._max_delay = max_reconnect_delay
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the listener in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the listener to stop."""
        self._running = False

    def _loop(self) -> None:
        try:
            import websockets.sync.client as ws_client
        except ImportError:
            raise ImportError(
                "WebSocket support requires the 'ws' extra: "
                "pip install gispulse-sdk[ws]"
            )

        delay = self._reconnect_delay
        while self._running:
            try:
                with ws_client.connect(self._url) as ws:
                    delay = self._reconnect_delay  # reset on success
                    while self._running:
                        try:
                            raw = ws.recv(timeout=5)
                        except TimeoutError:
                            continue
                        try:
                            event = json.loads(raw)
                            self._on_event(event)
                        except (json.JSONDecodeError, Exception):
                            pass
            except Exception:
                if not self._running:
                    break
                time.sleep(delay)
                delay = min(delay * 2, self._max_delay)


async def dedupe_by_change_id(
    events: AsyncIterator[dict],
    *,
    window_size: int = 1024,
) -> AsyncIterator[dict]:
    """Drop duplicate ``dml.changed`` events using a fixed-size LRU window.

    The GISPulse server emits at-least-once: the same ``change_id`` may
    appear more than once after a transient broadcast or ack failure.
    Wrap your event iterator with this helper to get exactly-once
    semantics from the application's perspective.

    Args:
        events:      Async iterator of event dicts (matches the
                     ``{"type", "data": {...}, "timestamp"}`` envelope
                     produced by ``EventHub.broadcast`` and forwarded
                     through ``/ws/events``).
        window_size: Maximum number of recently-seen ``change_id`` values
                     to remember. FIFO eviction once the window is full.
                     Defaults to 1024 — big enough for typical bursts,
                     small enough to keep memory bounded.

    Yields:
        Events whose ``change_id`` was not seen inside the current window.
        Events lacking a ``change_id`` (heartbeats, ``trigger.fired``
        without an associated change) pass through unchanged.

    Example::

        async for event in dedupe_by_change_id(ws_events()):
            print(event["data"]["change_id"], event["type"])
    """
    if window_size <= 0:
        raise ValueError("window_size must be > 0")

    seen_order: collections.deque = collections.deque(maxlen=window_size)
    seen_set: set = set()

    async for ev in events:
        try:
            cid = ev.get("data", {}).get("change_id")
        except AttributeError:
            cid = None

        if cid is None:
            yield ev
            continue

        if cid in seen_set:
            # Duplicate within the current window — drop silently.
            continue

        if len(seen_order) == window_size:
            # FIFO eviction: oldest id falls out of both structures.
            oldest = seen_order[0]
            seen_set.discard(oldest)
        seen_order.append(cid)
        seen_set.add(cid)
        yield ev


def iter_sse(response) -> Iterator[dict]:
    """Parse a Server-Sent Events stream from an httpx streaming response.

    Usage::

        with client._http.stream("GET", url) as resp:
            for event in iter_sse(resp):
                print(event)
    """
    for line in response.iter_lines():
        if line.startswith("data:"):
            try:
                yield json.loads(line[5:].strip())
            except json.JSONDecodeError:
                pass
