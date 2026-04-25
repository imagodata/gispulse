"""Thread-safety tests for :class:`EventHub` (Lot 2 v2 — Beta E2E).

Background
----------
Beta's E2E sidecar (real subprocess + httpx + websockets) found that
``EventHub.broadcast()`` called from N daemon threads (one per
``ChangeLogWatcher``) silently lost events under contention: 3
concurrent INSERTs across 3 GPKGs produced 1-2 events on /ws/events
instead of 3. Root cause: ``asyncio.Queue.put_nowait`` is not safe to
call from outside the loop that owns the queue — the selector races
with the producer thread.

Marco's fix: when ``bind_loop`` was called at startup, ``broadcast``
hands the actual queue push back to the loop thread via
``loop.call_soon_threadsafe(_safe_put, queue, payload)``. These tests
exercise that path directly.

In-process tests can reproduce the race because:
  * The async consumer awaits on the queue (running on the loop thread).
  * The producer threads call ``hub.broadcast`` concurrently.
Without the fix, the original ``put_nowait`` from N=3 threads would
miss events under load. With the fix, every event is delivered.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from gispulse.adapters.http.event_hub import EventHub


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain_queue_to_list(queue: asyncio.Queue[str]) -> list[dict]:
    """Pull every payload currently sitting in ``queue`` and decode it."""
    out: list[dict] = []
    while not queue.empty():
        raw = queue.get_nowait()
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            out.append({"_raw": raw})
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_from_thread_is_thread_safe() -> None:
    """3 producer threads × 100 events each = 300 events delivered.

    Without ``bind_loop`` + ``call_soon_threadsafe``, this races and
    drops events under load on multi-threaded watchers (Beta's reproducer).
    With the fix, every event lands in the subscriber queue.
    """
    hub = EventHub()
    loop = asyncio.get_running_loop()
    hub.bind_loop(loop)

    queue = hub.subscribe()

    n_threads = 3
    n_per_thread = 100

    def _producer(tid: int) -> None:
        for i in range(n_per_thread):
            hub.broadcast(
                "dml.changed",
                {
                    "dataset_id": f"ds-{tid}",
                    "table": "parcels",
                    "op": "INSERT",
                    "fid": str(i),
                    "change_id": i,
                    "ts": "2026-04-25T00:00:00",
                },
            )

    threads = [
        threading.Thread(target=_producer, args=(tid,))
        for tid in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive(), "producer thread hung"

    # Yield to the loop so every scheduled call_soon_threadsafe gets a
    # chance to run before we drain. One ``asyncio.sleep(0)`` per
    # scheduled call would be enough; sleep(0.05) is safer on CI.
    await asyncio.sleep(0.05)

    received = _drain_queue_to_list(queue)
    expected = n_threads * n_per_thread
    assert len(received) == expected, (
        f"thread-safety regression: produced {expected} events from "
        f"{n_threads} threads, queue holds {len(received)}. "
        f"This is the Beta E2E bug (Lot 2 v2)."
    )

    # Every dataset_id must be represented (no whole-thread starvation).
    seen_datasets = {ev["data"]["dataset_id"] for ev in received}
    assert seen_datasets == {f"ds-{i}" for i in range(n_threads)}

    assert hub.dropped_total == 0


@pytest.mark.asyncio
async def test_bind_loop_is_idempotent() -> None:
    hub = EventHub()
    loop = asyncio.get_running_loop()
    hub.bind_loop(loop)
    hub.bind_loop(loop)
    queue = hub.subscribe()

    def _producer() -> None:
        hub.broadcast("dml.changed", {"dataset_id": "ds-x", "change_id": 1})

    t = threading.Thread(target=_producer)
    t.start()
    t.join(timeout=2.0)

    await asyncio.sleep(0.02)
    received = _drain_queue_to_list(queue)
    assert len(received) == 1


def test_broadcast_without_bound_loop_falls_back() -> None:
    """When no loop is bound (legacy unit-test path), ``broadcast`` keeps
    working synchronously inside ``asyncio.run``."""

    async def _scenario() -> int:
        hub = EventHub()  # no bind_loop call
        queue = hub.subscribe()
        # In-loop call — direct put_nowait via _safe_put.
        hub.broadcast("dml.changed", {"dataset_id": "ds-y", "change_id": 1})
        return queue.qsize()

    assert asyncio.run(_scenario()) == 1
