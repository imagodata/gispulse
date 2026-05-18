"""SourceWatcherRegistry — poll external data-source freshness (issue #187).

The freshness-detection counterpart of :class:`WatcherRegistry` (which
watches local DML in ``_gispulse_change_log``). Where the change-log
watcher polls a SQLite table every 200 ms, the source watcher polls a
:class:`~core.sources.DataSource`'s ``revision()`` on a *human*
timescale — hours to days, never sub-minute — because external open
data (cadastre millésimes, BD TOPO releases, PLU revisions) changes
that slowly.

On a revision change it broadcasts a ``source.changed`` event into the
EventHub; the EventRouter then matches it against ``SOURCE_CHANGED``
triggers (issue #186)::

    DataSource.revision()  ──poll──▶  SourceWatcherRegistry
        │ revision differs
        ▼
    EventHub.broadcast("source.changed", {...})  ──▶  EventRouter
        ▼
    SOURCE_CHANGED trigger fires  ──▶  pipeline re-run

``revision()`` must be cheap (HTTP HEAD / ETag / millésime token) — the
watcher never calls ``fetch()``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol

from gispulse.core.logging import get_logger

log = get_logger(__name__)

# Sources change on a human timescale. The floor is deliberate: a
# sub-minute poll of an external API is abuse, not freshness detection.
_MIN_INTERVAL_S = 60.0
_DEFAULT_INTERVAL_S = 6 * 3600.0  # 6 hours

# Map a catalog ``update_frequency`` label to a poll interval (seconds).
_FREQUENCY_INTERVALS: dict[str, float] = {
    "temps-reel": 300.0,
    "quotidien": 3600.0,
    "hebdomadaire": 6 * 3600.0,
    "mensuel": 24 * 3600.0,
    "trimestriel": 24 * 3600.0,
    "annuel": 24 * 3600.0,
    "pluriannuel": 24 * 3600.0,
}


def interval_from_frequency(frequency: str | None) -> float:
    """Resolve a poll interval (seconds) from a catalog frequency label."""
    if not frequency:
        return _DEFAULT_INTERVAL_S
    return _FREQUENCY_INTERVALS.get(frequency.strip().lower(), _DEFAULT_INTERVAL_S)


class _EventHub(Protocol):
    def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> Any: ...


@dataclass
class _WatchEntry:
    source: Any  # DataSource
    entry_id: str
    interval_s: float
    last_revision: str | None = None


class SourceWatcherRegistry:
    """Polls registered data-source entries and emits ``source.changed``.

    One :class:`_WatchEntry` per ``(source, entry_id)`` pair. The baseline
    revision is captured at registration so only *subsequent* changes
    emit an event.
    """

    def __init__(self, event_hub: _EventHub | None = None) -> None:
        self._hub = event_hub
        self._entries: dict[str, _WatchEntry] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ API

    def register(
        self,
        source: Any,
        entry_id: str,
        *,
        interval_s: float | None = None,
        frequency: str | None = None,
    ) -> str:
        """Watch ``entry_id`` of ``source``; return the registration key.

        ``interval_s`` wins over ``frequency``; absent both, a 6 h default
        applies. Sub-minute intervals are rejected.
        """
        interval = interval_s if interval_s is not None else interval_from_frequency(frequency)
        if interval < _MIN_INTERVAL_S:
            raise ValueError(
                f"source poll interval must be >= {_MIN_INTERVAL_S:.0f}s — "
                f"external sources do not change sub-minute"
            )
        key = f"{source.name}:{entry_id}"
        with self._lock:
            self._entries[key] = _WatchEntry(
                source=source,
                entry_id=entry_id,
                interval_s=interval,
                last_revision=source.revision(entry_id),
            )
        log.info("source_watch_registered", source=key, interval_s=interval)
        return key

    def unregister(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def list_watched(self) -> list[str]:
        return sorted(self._entries)

    def poll(self) -> list[dict[str, Any]]:
        """Poll every registered entry once; broadcast + return the changes.

        A failing ``revision()`` is logged and skipped — one unreachable
        source never stalls the others.
        """
        changes: list[dict[str, Any]] = []
        for key, entry in list(self._entries.items()):
            try:
                current = entry.source.revision(entry.entry_id)
            except Exception as exc:  # noqa: BLE001 — isolate one bad source
                log.warning("source_revision_poll_failed", source=key, error=str(exc))
                continue
            if current is None or current == entry.last_revision:
                continue
            payload = {
                "source": f"{entry.source.name}://{entry.entry_id}",
                "entry": entry.entry_id,
                "revision": current,
                "previous": entry.last_revision,
            }
            entry.last_revision = current
            changes.append(payload)
            log.info("source_changed", **payload)
            if self._hub is not None:
                try:
                    self._hub.broadcast("source.changed", payload)
                except Exception as exc:  # noqa: BLE001
                    log.warning("source_changed_broadcast_failed", source=key, error=str(exc))
        return changes

    # ------------------------------------------------------------- daemon

    def start(self, tick_s: float | None = None) -> None:
        """Run :meth:`poll` on a daemon thread until :meth:`stop`."""
        if self._thread is not None and self._thread.is_alive():
            return
        interval = tick_s or min(
            (e.interval_s for e in self._entries.values()), default=_DEFAULT_INTERVAL_S
        )
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.poll()
                except Exception as exc:  # noqa: BLE001
                    log.warning("source_watch_tick_failed", error=str(exc))

        self._thread = threading.Thread(
            target=_loop, name="source-watcher", daemon=True
        )
        self._thread.start()
        log.info("source_watcher_started", tick_s=interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


__all__ = ["SourceWatcherRegistry", "interval_from_frequency"]
