"""WebSocket and SSE streaming helpers.

WebSocket support requires the ``ws`` extra::

    pip install gispulse-sdk[ws]

## At-least-once delivery

The server's ``dml.changed`` events are at-least-once: a bad subscriber,
a transient ``mark_changes_processed`` failure (e.g. read-only GPKG), or
a watcher restart with un-acked rows can replay the same ``change_id``
to clients. Use :func:`dedupe_events` (or :meth:`subscribe_events` with
the default ``dedupe=True``) to filter duplicates client-side.

## Multi-tenant change_id collision (Lot 2 v2)

Each ``ChangeLogWatcher`` numbers ``change_id`` from 1 inside its own
GPKG. Two datasets emitting INSERTs concurrently both produce
``change_id=1`` from the client's perspective. The dedup key MUST
therefore combine ``(dataset_id, change_id)`` — using ``change_id``
alone silently drops legitimate events from a second tenant.
:func:`dedupe_events` does this by default. ``dedupe_by_change_id`` is
preserved as a deprecated alias for backwards-compatibility.
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


async def dedupe_events(
    events: AsyncIterator[dict],
    *,
    window_size: int = 1024,
) -> AsyncIterator[dict]:
    """Drop duplicate ``dml.changed`` events using a fixed-size LRU window.

    The GISPulse server emits at-least-once: the same ``change_id`` may
    appear more than once after a transient broadcast or ack failure.
    Wrap your event iterator with this helper to get exactly-once
    semantics from the application's perspective.

    Multi-tenant key (Lot 2 v2 — Beta E2E)
    --------------------------------------
    The dedup key is the tuple ``(dataset_id, change_id)``. Each
    ``ChangeLogWatcher`` on the server numbers ``change_id`` from 1
    inside its own GPKG, so collisions across datasets are guaranteed
    on a multi-tenant deployment. Keying on ``change_id`` alone (the
    pre-Lot 2 v2 behaviour) silently drops legitimate events from a
    second tenant.

    Events that lack ``dataset_id`` *or* ``change_id`` (heartbeats,
    ``trigger.fired`` without an associated change, malformed envelope)
    pass through unchanged — we never assume perfect server payloads.

    Args:
        events:      Async iterator of event dicts (matches the
                     ``{"type", "data": {...}, "timestamp"}`` envelope
                     produced by ``EventHub.broadcast`` and forwarded
                     through ``/ws/events``).
        window_size: Maximum number of recently-seen
                     ``(dataset_id, change_id)`` tuples to remember.
                     FIFO eviction once the window is full. Defaults to
                     1024 — sufficient for ~1024 events / dataset within
                     the active window. Tune up if your client buffers
                     larger bursts before consuming.

    Yields:
        Events whose ``(dataset_id, change_id)`` tuple was not seen
        inside the current window.

    Example::

        async for event in dedupe_events(ws_events()):
            print(event["data"]["dataset_id"], event["data"]["change_id"])
    """
    if window_size <= 0:
        raise ValueError("window_size must be > 0")

    seen_order: collections.deque = collections.deque(maxlen=window_size)
    seen_set: set = set()

    async for ev in events:
        try:
            data = ev.get("data") or {}
            cid = data.get("change_id")
            ds_id = data.get("dataset_id")
        except AttributeError:
            cid = None
            ds_id = None

        if cid is None or ds_id is None:
            # No dedup key — heartbeat, malformed event, or pre-Lot 2 v2
            # server that didn't inject ``dataset_id``. Always pass
            # through to avoid silently dropping events.
            yield ev
            continue

        key = (ds_id, cid)

        if key in seen_set:
            # Duplicate within the current window — drop silently.
            continue

        if len(seen_order) == window_size:
            # FIFO eviction: oldest tuple falls out of both structures.
            oldest = seen_order[0]
            seen_set.discard(oldest)
        seen_order.append(key)
        seen_set.add(key)
        yield ev


# ---------------------------------------------------------------------------
# Backwards-compat alias (Lot 2 v2)
# ---------------------------------------------------------------------------
#
# ``dedupe_by_change_id`` was the pre-Lot 2 v2 name. The function now
# keys on ``(dataset_id, change_id)`` because ``change_id`` alone is not
# unique across multi-tenant deployments. The alias is preserved so
# existing imports keep working — emit a DeprecationWarning the first
# time it is used so SDK consumers migrate.
dedupe_by_change_id = dedupe_events
"""Deprecated alias for :func:`dedupe_events`.

Kept for backwards-compatibility with the Lot 2 v1 SDK. The function
now keys on ``(dataset_id, change_id)`` to avoid collisions across
multi-tenant deployments. Migrate imports to :func:`dedupe_events`."""


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
