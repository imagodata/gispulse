"""WebSocket and SSE streaming helpers.

WebSocket support requires the ``ws`` extra::

    pip install gispulse-sdk[ws]
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Iterator, Optional


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
