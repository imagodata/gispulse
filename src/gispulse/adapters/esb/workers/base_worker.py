"""
BaseWorker — Abstract base class for GISPulse ESB workers.

Provides:
- Polling loop with wake-up on pg_notify events or timeout
- Heartbeat management (optional, DB-backed)
- Standardised error handling with backoff

Concrete workers inherit from this class and implement ``run_batch()``.

Adapted from Forge ESB reference (workers/base_worker.py).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from gispulse.core.logging import get_logger

log = get_logger(__name__)

try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ASYNCPG_AVAILABLE = False
    asyncpg = None  # type: ignore[assignment]

from gispulse.adapters.esb.enums import WorkerType

# Heartbeat interval in seconds (default; can be overridden in subclass)
DEFAULT_HEARTBEAT_INTERVAL_SEC = 30


class BaseWorker(ABC):
    """
    Abstract base for GISPulse ESB pipeline workers.

    Usage::

        class MyWorker(BaseWorker):
            worker_type = WorkerType.PROCESSING

            async def run_batch(self) -> int:
                # Process a batch of messages; return count processed
                return 0

        worker = MyWorker(db_pool=pool)
        await worker.start()
        # ... worker runs in background
        await worker.stop()
    """

    worker_type: WorkerType  # Must be defined in subclasses

    def __init__(
        self,
        worker_id: Optional[UUID] = None,
        db_pool: Optional["asyncpg.Pool"] = None,
        batch_size: int = 10,
        poll_interval_ms: int = 100,
        heartbeat_interval_sec: int = DEFAULT_HEARTBEAT_INTERVAL_SEC,
    ) -> None:
        self.worker_id = worker_id or uuid4()
        self.db_pool = db_pool
        self.batch_size = batch_size
        self.poll_interval_ms = poll_interval_ms
        self.heartbeat_interval_sec = heartbeat_interval_sec

        self._running = False
        self._wake_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._last_heartbeat: Optional[datetime] = None
        self._messages_processed = 0
        self._errors_count = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return f"gispulse-{self.worker_type.value.lower()}-{str(self.worker_id)[:8]}"

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the worker as a background asyncio task."""
        if self._running:
            return
        self._running = True
        await self._register()
        self._task = asyncio.create_task(self._run_loop(), name=self.name)

    async def stop(self) -> None:
        """Stop the worker gracefully (waits for the current batch to finish)."""
        if not self._running:
            return
        self._running = False
        self.wake()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
        await self._unregister()

    def wake(self) -> None:
        """Wake the worker immediately (called on pg_notify event)."""
        self._wake_event.set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Main polling loop."""
        last_heartbeat = datetime.now(timezone.utc)

        while self._running:
            try:
                processed = await self._process_batch()

                if processed > 0:
                    self._messages_processed += processed

                # Periodic heartbeat
                elapsed_hb = (datetime.now(timezone.utc) - last_heartbeat).total_seconds()
                if elapsed_hb > self.heartbeat_interval_sec:
                    await self._heartbeat()
                    last_heartbeat = datetime.now(timezone.utc)

                if processed == 0:
                    # No messages: wait for wake event or timeout
                    try:
                        await asyncio.wait_for(
                            self._wake_event.wait(),
                            timeout=self.poll_interval_ms / 1000.0,
                        )
                    except asyncio.TimeoutError:
                        pass
                    finally:
                        self._wake_event.clear()
                else:
                    # Messages were processed: continue quickly
                    await asyncio.sleep(0.01)

            except Exception as exc:
                self._errors_count += 1
                log.exception("worker_unexpected_error", worker=self.name, error=str(exc))
                await asyncio.sleep(1)

    async def _process_batch(self) -> int:
        return await self.run_batch()

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def run_batch(self) -> int:
        """Process one batch of messages.

        Returns:
            Number of messages processed. Return 0 when the queue is empty
            (the worker will enter polling mode).
        """
        ...

    # ------------------------------------------------------------------
    # DB registration / heartbeat (optional, no-op without db_pool)
    # ------------------------------------------------------------------

    async def _register(self) -> None:
        """Register this worker in the gispulse.esb_worker_pid table."""
        if not self.db_pool:
            return
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO gispulse.esb_worker_pid
                        (worker_id, actif, last_heartbeat, worker_type)
                    VALUES ($1, TRUE, NOW(), $2)
                    ON CONFLICT (worker_id) DO UPDATE SET
                        actif          = TRUE,
                        last_heartbeat = NOW(),
                        worker_type    = $2
                    """,
                    self.worker_id,
                    self.worker_type.value,
                )
        except Exception as exc:
            log.debug("worker_register_failed", worker=self.name, error=str(exc))

    async def _unregister(self) -> None:
        """Mark this worker as inactive in the registry."""
        if not self.db_pool:
            return
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE gispulse.esb_worker_pid
                    SET actif = FALSE, last_heartbeat = NOW()
                    WHERE worker_id = $1
                    """,
                    self.worker_id,
                )
        except Exception as exc:
            log.debug("worker_unregister_failed", worker=self.name, error=str(exc))

    async def _heartbeat(self) -> None:
        """Update the heartbeat timestamp in the registry."""
        if not self.db_pool:
            return
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO gispulse.esb_worker_heartbeat
                        (worker_id, status, messages_processed, checked_at)
                    VALUES ($1, 'RUNNING', $2, NOW())
                    ON CONFLICT (worker_id) DO UPDATE SET
                        status             = 'RUNNING',
                        messages_processed = $2,
                        checked_at         = NOW()
                    """,
                    self.worker_id,
                    self._messages_processed,
                )
            self._last_heartbeat = datetime.now(timezone.utc)
        except Exception as exc:
            log.debug("worker_heartbeat_failed", worker=self.name, error=str(exc))

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        return {
            "worker_id": str(self.worker_id),
            "worker_type": self.worker_type.value,
            "name": self.name,
            "is_running": self._running,
            "messages_processed": self._messages_processed,
            "errors_count": self._errors_count,
            "last_heartbeat": (
                self._last_heartbeat.isoformat() if self._last_heartbeat else None
            ),
        }
