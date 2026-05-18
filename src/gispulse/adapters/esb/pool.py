"""
WorkerPool — GISPulse ESB worker pool manager.

Manages a pool of BaseWorker instances with:
- Lifecycle management (start / stop)
- Manual and automatic scaling (scale_up / scale_down)
- Periodic health checks
- Auto-scaling based on queue depth

Usage::

    pool = WorkerPool(config=WorkerPoolConfig(min_workers=2, max_workers=8))
    await pool.start()
    # ... pool manages workers in background
    status = pool.get_pool_status()
    await pool.stop(graceful=True)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from gispulse.adapters.esb.enums import WorkerType
from gispulse.adapters.esb.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class WorkerPoolConfig:
    """Configuration for WorkerPool scaling and health check behaviour."""

    min_workers: int = 1
    max_workers: int = 4
    scale_up_threshold: int = 100   # Queue depth above which scale_up triggers
    scale_down_threshold: int = 10  # Queue depth below which scale_down triggers
    health_check_interval: float = 30.0  # Seconds between health checks

    # Default worker type when none is specified at spawn time
    default_worker_type: WorkerType = WorkerType.IDENTIFY

    # Optional asyncpg pool injected into each spawned worker
    db_pool: Optional[Any] = field(default=None, repr=False)

    # Factory callable: (worker_id: str, db_pool) -> BaseWorker
    # When None, a minimal no-op worker is used (useful for testing / dry-run)
    worker_factory: Optional[Any] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# WorkerInfo — observability snapshot
# ---------------------------------------------------------------------------


@dataclass
class WorkerInfo:
    """Snapshot of a worker's state, safe to serialise."""

    worker_id: str
    worker_type: str        # WorkerType.value
    status: str             # "running" | "stopped" | "error"
    started_at: datetime
    messages_processed: int = 0
    last_heartbeat: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Internal no-op worker used when no factory is provided
# ---------------------------------------------------------------------------


class _NoOpWorker(BaseWorker):
    """No-operation worker for dry-run / testing scenarios."""

    worker_type = WorkerType.IDENTIFY

    async def run_batch(self) -> int:  # pragma: no cover
        await asyncio.sleep(0.05)
        return 0


# ---------------------------------------------------------------------------
# WorkerPool
# ---------------------------------------------------------------------------


class WorkerPool:
    """
    Gestionnaire de pool de workers ESB.

    Gère le cycle de vie, le scaling et le health check des workers.

    Exemple d'utilisation::

        pool = WorkerPool(WorkerPoolConfig(min_workers=2, max_workers=8))
        await pool.start()
        await pool.scale_up(2)
        status = pool.get_pool_status()
        await pool.stop()
    """

    def __init__(self, config: Optional[WorkerPoolConfig] = None) -> None:
        self.config = config or WorkerPoolConfig()
        # worker_id (str) -> WorkerInfo
        self._infos: dict[str, WorkerInfo] = {}
        # worker_id (str) -> BaseWorker instance
        self._workers: dict[str, BaseWorker] = {}
        # worker_id (str) -> asyncio.Task
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self._health_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Démarre le pool avec ``min_workers`` workers."""
        if self._running:
            return
        self._running = True
        logger.info("WorkerPool starting (min=%d, max=%d)", self.config.min_workers, self.config.max_workers)

        # Spawn minimum workers
        await self.scale_up(self.config.min_workers)

        # Start background health-check loop
        self._health_task = asyncio.create_task(
            self._health_check_loop(), name="gispulse-pool-health"
        )

    async def stop(self, graceful: bool = True, timeout: float = 30.0) -> None:
        """
        Arrête tous les workers.

        Parameters
        ----------
        graceful:
            Si True, attend que chaque worker termine son batch courant
            (dans la limite de ``timeout`` secondes).
        timeout:
            Durée maximale d'attente pour un arrêt graceful.
        """
        if not self._running:
            return
        self._running = False
        logger.info("WorkerPool stopping (graceful=%s, timeout=%s)", graceful, timeout)

        # Cancel health-check loop
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except (asyncio.CancelledError, Exception):
                pass
        self._health_task = None

        # Stop all workers
        worker_ids = list(self._workers.keys())
        await self._stop_workers(worker_ids, graceful=graceful, timeout=timeout)

    # ------------------------------------------------------------------
    # Scaling
    # ------------------------------------------------------------------

    async def scale_up(self, count: int = 1) -> list[str]:
        """
        Ajoute ``count`` workers au pool.

        Respecte ``max_workers`` : le nombre de workers créés est plafonné.

        Returns
        -------
        list[str]
            IDs des workers effectivement créés.
        """
        current = len(self._workers)
        available_slots = self.config.max_workers - current
        actual_count = max(0, min(count, available_slots))

        if actual_count == 0:
            logger.debug("scale_up: already at max_workers=%d", self.config.max_workers)
            return []

        created: list[str] = []
        for _ in range(actual_count):
            worker_id = await self._spawn_worker()
            created.append(worker_id)

        logger.info("WorkerPool scaled up by %d (total=%d)", actual_count, len(self._workers))
        return created

    async def scale_down(self, count: int = 1) -> list[str]:
        """
        Retire ``count`` workers du pool.

        Respecte ``min_workers`` : on ne descend jamais en dessous du minimum.

        Returns
        -------
        list[str]
            IDs des workers effectivement retirés.
        """
        current = len(self._workers)
        removable = current - self.config.min_workers
        actual_count = max(0, min(count, removable))

        if actual_count == 0:
            logger.debug("scale_down: already at min_workers=%d", self.config.min_workers)
            return []

        # Pick the most recently added workers (LIFO)
        candidates = list(self._workers.keys())[-actual_count:]
        await self._stop_workers(candidates, graceful=True, timeout=5.0)

        logger.info("WorkerPool scaled down by %d (total=%d)", actual_count, len(self._workers))
        return candidates

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def get_pool_status(self) -> dict:
        """
        Retourne un snapshot de l'état du pool.

        Returns
        -------
        dict
            Clés: ``running``, ``total_workers``, ``active_count``,
            ``min_workers``, ``max_workers``, ``workers``.
        """
        return {
            "running": self._running,
            "total_workers": len(self._workers),
            "active_count": self.active_count,
            "min_workers": self.config.min_workers,
            "max_workers": self.config.max_workers,
            "workers": {
                wid: {
                    "worker_id": info.worker_id,
                    "worker_type": info.worker_type,
                    "status": info.status,
                    "started_at": info.started_at.isoformat(),
                    "messages_processed": info.messages_processed,
                    "last_heartbeat": (
                        info.last_heartbeat.isoformat() if info.last_heartbeat else None
                    ),
                }
                for wid, info in self._infos.items()
            },
        }

    def get_worker_info(self, worker_id: str) -> Optional[WorkerInfo]:
        """Retourne les infos d'un worker ou None s'il n'existe pas."""
        return self._infos.get(worker_id)

    @property
    def active_count(self) -> int:
        """Nombre de workers dont le statut est ``"running"``."""
        return sum(1 for info in self._infos.values() if info.status == "running")

    # ------------------------------------------------------------------
    # Health check loop
    # ------------------------------------------------------------------

    async def _health_check_loop(self) -> None:
        """Boucle périodique de health check et mise à jour des heartbeats."""
        while self._running:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                if not self._running:
                    break
                self._refresh_worker_stats()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("health_check_loop error: %s", exc)

    def _refresh_worker_stats(self) -> None:
        """Synchronise les métriques WorkerInfo depuis les workers actifs."""
        now = datetime.now(timezone.utc)
        for wid, worker in list(self._workers.items()):
            info = self._infos.get(wid)
            if info is None:
                continue
            task = self._tasks.get(wid)
            if task is not None and task.done():
                exc = task.exception() if not task.cancelled() else None
                info.status = "error" if exc else "stopped"
            else:
                info.status = "running" if worker.is_running else "stopped"
                info.messages_processed = worker._messages_processed
                if worker._last_heartbeat:
                    info.last_heartbeat = worker._last_heartbeat
                else:
                    # Use current time as proxy (worker is alive)
                    info.last_heartbeat = now

    # ------------------------------------------------------------------
    # Auto-scaling
    # ------------------------------------------------------------------

    async def _auto_scale(self, queue_size: int) -> None:
        """
        Auto-scaling basé sur la profondeur de la file de messages.

        - Scale up si ``queue_size >= scale_up_threshold``.
        - Scale down si ``queue_size <= scale_down_threshold``.
        """
        if queue_size >= self.config.scale_up_threshold:
            logger.info("auto_scale: queue_size=%d >= threshold=%d → scale_up", queue_size, self.config.scale_up_threshold)
            await self.scale_up(1)
        elif queue_size <= self.config.scale_down_threshold:
            logger.info("auto_scale: queue_size=%d <= threshold=%d → scale_down", queue_size, self.config.scale_down_threshold)
            await self.scale_down(1)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _spawn_worker(self) -> str:
        """Crée, enregistre et démarre un nouveau worker. Retourne son ID."""
        worker_id_str = str(uuid4())

        if self.config.worker_factory is not None:
            worker: BaseWorker = self.config.worker_factory(worker_id_str, self.config.db_pool)
        else:
            worker = _NoOpWorker(db_pool=self.config.db_pool)

        await worker.start()

        # Register in internal state
        self._workers[worker_id_str] = worker
        self._tasks[worker_id_str] = worker._task  # type: ignore[assignment]
        self._infos[worker_id_str] = WorkerInfo(
            worker_id=worker_id_str,
            worker_type=worker.worker_type.value,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        return worker_id_str

    async def _stop_workers(
        self,
        worker_ids: list[str],
        graceful: bool = True,
        timeout: float = 30.0,
    ) -> None:
        """Arrête les workers désignés et nettoie les registres."""
        stop_coros = [self._stop_single(wid, graceful, timeout) for wid in worker_ids]
        await asyncio.gather(*stop_coros, return_exceptions=True)

    async def _stop_single(
        self,
        worker_id: str,
        graceful: bool,
        timeout: float,
    ) -> None:
        """Arrête un worker individuel."""
        worker = self._workers.pop(worker_id, None)
        task = self._tasks.pop(worker_id, None)
        info = self._infos.get(worker_id)

        if worker is None:
            return

        try:
            if graceful:
                # worker.stop() already waits for the current batch (up to 5 s)
                await asyncio.wait_for(worker.stop(), timeout=timeout)
            else:
                worker._running = False
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
        except asyncio.TimeoutError:
            logger.warning("WorkerPool: worker %s did not stop within %.1f s — cancelling", worker_id, timeout)
            if task and not task.done():
                task.cancel()
        except Exception as exc:
            logger.warning("WorkerPool: error stopping worker %s: %s", worker_id, exc)
        finally:
            if info:
                info.status = "stopped"
