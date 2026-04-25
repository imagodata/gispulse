"""Multi-watcher registry for per-dataset GPKG live-sync.

Lot 2 v2 (P0-2b): the original lifespan instantiated a single
:class:`ChangeLogWatcher` against the project engine. That left uploaded
GPKGs unreachable — their ``_gispulse_change_log`` table sat next to the
data but no thread polled it.

The :class:`WatcherRegistry` flips that: one ``GeoPackageEngine`` +
``ChangeLogWatcher`` pair per *registered* dataset. The registry is held
on ``app.state.watcher_registry`` and driven by the explicit
``POST /datasets/{id}/enable_tracking`` endpoint (Q1).

Threading
---------
:class:`ChangeLogWatcher` runs on a daemon thread and the
``GeoPackageEngine`` it owns has its own SQLite connection lock. Register
/ unregister calls are serialised by an internal :class:`threading.Lock`
so the FastAPI worker pool doesn't race during bursty enable/disable
calls. None of the public methods block on the watcher's poll cycle.

Lifecycle
---------
* :meth:`register`        — open engine, start watcher (idempotent).
* :meth:`unregister`      — stop watcher, close engine (best-effort).
* :meth:`shutdown_all`    — called from the FastAPI lifespan on shutdown.

Note: this registry never installs the SQLite triggers. That is the
caller's responsibility (the ``enable_tracking`` endpoint calls
``engine.enable_change_tracking(layer)`` for each layer before
:meth:`register`).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from persistence.change_log_watcher import ChangeLogWatcher

logger = logging.getLogger(__name__)


class WatcherRegistry:
    """Manage one :class:`ChangeLogWatcher` per tracked GPKG dataset.

    Args:
        event_hub: Shared :class:`EventHub`. Every watcher created by the
            registry broadcasts to this single hub — clients on
            ``/ws/events`` see all datasets unless they apply topic /
            table filters.
    """

    def __init__(self, event_hub: Any) -> None:
        self._hub = event_hub
        # dataset_id -> (engine, watcher, layers)
        # ``layers`` is a snapshot of the tracked layer names, captured at
        # register() time. Cached so the idempotency short-circuit in
        # /enable_tracking can return ``layers_tracked`` without re-opening
        # the GPKG (re-opening while the watcher holds a SQLite handle on
        # the same file produces "disk I/O error" under WAL contention).
        self._entries: dict[str, tuple[Any, ChangeLogWatcher, list[str]]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        dataset_id: str,
        gpkg_path: Path,
        *,
        triggers_provider: Callable[[], list] | None = None,
        poll_interval: float = 0.2,
        batch_limit: int = 100,
        layers: list[str] | None = None,
    ) -> bool:
        """Open a :class:`GeoPackageEngine` on *gpkg_path* and start a
        watcher.

        Idempotent: re-registering the same ``dataset_id`` is a no-op
        and **does not** re-open the engine or restart the watcher.
        Returns ``True`` when a fresh registration happened, ``False``
        when the call was a no-op (already registered).

        Re-registering with a *different* path raises ``ValueError`` —
        you must :meth:`unregister` first to avoid leaking a thread.

        Args:
            dataset_id:        Stable handle (UUID string or ``"__project__"``
                               for the lifespan-bound project GPKG).
            gpkg_path:         Path to the .gpkg file. Must already contain
                               the ``_gispulse_change_log`` table — call
                               ``engine.enable_change_tracking(layer)`` on
                               each tracked layer before registering.
            triggers_provider: Optional callable returning the active list
                               of :class:`Trigger` to evaluate per-batch.
            poll_interval:     Forwarded to ChangeLogWatcher.
            batch_limit:       Forwarded to ChangeLogWatcher.
            layers:            Optional snapshot of the layer names just
                               tracked. Stored for later retrieval via
                               :meth:`get_layers` so callers don't need
                               to re-open the GPKG.
        """
        gpkg_path = Path(gpkg_path)
        layers_snapshot: list[str] = list(layers or [])
        with self._lock:
            existing = self._entries.get(dataset_id)
            if existing is not None:
                engine, _watcher, _layers = existing
                existing_path = getattr(engine, "path", None)
                if existing_path is not None and Path(existing_path) != gpkg_path:
                    raise ValueError(
                        f"watcher_registry: dataset {dataset_id!r} is already "
                        f"registered with a different path "
                        f"({existing_path!r} vs {gpkg_path!r}). "
                        f"Call unregister() first."
                    )
                logger.debug(
                    "watcher_registry_register_noop dataset_id=%s path=%s",
                    dataset_id,
                    gpkg_path,
                )
                # True idempotence: do NOT re-open the engine, do NOT
                # spawn a second watcher. Returning early avoids the
                # "disk I/O error" that pops up when a second SQLite
                # handle is opened on a GPKG already held by the
                # running watcher in WAL mode.
                return False

            # Lazy import to keep the persistence layer free of a hard
            # GeoPackageEngine import at module load time.
            from persistence.gpkg_engine import GeoPackageEngine

            engine = GeoPackageEngine(gpkg_path)
            engine.open()
            watcher = ChangeLogWatcher(
                engine=engine,
                event_hub=self._hub,
                poll_interval=poll_interval,
                batch_limit=batch_limit,
                triggers_provider=triggers_provider,
            )
            watcher.start()
            self._entries[dataset_id] = (engine, watcher, layers_snapshot)
            logger.info(
                "watcher_registry_registered dataset_id=%s path=%s total=%d",
                dataset_id,
                gpkg_path,
                len(self._entries),
            )
            return True

    def unregister(self, dataset_id: str) -> None:
        """Stop and drop the watcher attached to *dataset_id*.

        No-op if the dataset is not registered.
        """
        with self._lock:
            entry = self._entries.pop(dataset_id, None)
        if entry is None:
            logger.debug(
                "watcher_registry_unregister_noop dataset_id=%s", dataset_id
            )
            return
        engine, watcher, _layers = entry
        try:
            watcher.stop()
        except Exception as exc:
            logger.warning(
                "watcher_registry_watcher_stop_failed dataset_id=%s err=%s",
                dataset_id,
                exc,
            )
        try:
            engine.close()
        except Exception as exc:
            logger.warning(
                "watcher_registry_engine_close_failed dataset_id=%s err=%s",
                dataset_id,
                exc,
            )
        logger.info("watcher_registry_unregistered dataset_id=%s", dataset_id)

    def is_registered(self, dataset_id: str) -> bool:
        """Return True if a watcher exists for *dataset_id*."""
        with self._lock:
            return dataset_id in self._entries

    def list_registered(self) -> list[str]:
        """Return a snapshot of currently registered dataset ids."""
        with self._lock:
            return list(self._entries.keys())

    def get_engine(self, dataset_id: str) -> Any | None:
        """Return the :class:`GeoPackageEngine` for *dataset_id* (or None).

        Useful for the disable_tracking endpoint which needs to call
        ``engine.disable_change_tracking(layer)`` on every layer before
        unregistering.
        """
        with self._lock:
            entry = self._entries.get(dataset_id)
            return entry[0] if entry else None

    def get_layers(self, dataset_id: str) -> list[str]:
        """Return the cached list of tracked layer names for *dataset_id*.

        Empty list if the dataset is not registered or no layer snapshot
        was provided at registration time. Used by the idempotent path
        of ``POST /enable_tracking`` to avoid re-opening the GPKG.
        """
        with self._lock:
            entry = self._entries.get(dataset_id)
            return list(entry[2]) if entry else []

    def shutdown_all(self) -> None:
        """Stop every watcher and close every engine. Called from the
        FastAPI lifespan on shutdown. Best-effort: errors are logged
        but never re-raised so a hung watcher cannot block uvicorn's
        graceful exit.
        """
        with self._lock:
            entries = list(self._entries.items())
            self._entries.clear()
        for dataset_id, (engine, watcher, _layers) in entries:
            try:
                watcher.stop()
            except Exception as exc:
                logger.warning(
                    "watcher_registry_shutdown_watcher_stop_failed "
                    "dataset_id=%s err=%s",
                    dataset_id,
                    exc,
                )
            try:
                engine.close()
            except Exception as exc:
                logger.warning(
                    "watcher_registry_shutdown_engine_close_failed "
                    "dataset_id=%s err=%s",
                    dataset_id,
                    exc,
                )
        logger.info("watcher_registry_shutdown_all count=%d", len(entries))


__all__ = ["WatcherRegistry"]
