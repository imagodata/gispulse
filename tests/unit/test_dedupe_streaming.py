"""Unit tests for :func:`gispulse_sdk.streaming.dedupe_events`.

Tests cover:
- Standard dedup over a small window keyed on ``(dataset_id, change_id)``.
- Multi-tenant collision: events with the same ``change_id`` but
  different ``dataset_id`` are NOT considered duplicates (Lot 2 v2 fix).
- Events without ``change_id`` or without ``dataset_id`` pass through.
- LRU eviction when the window is full.
- ValueError on invalid window_size.
- Backwards-compat alias ``dedupe_by_change_id``.
"""

from __future__ import annotations

import os
import sys
from typing import AsyncIterator

import pytest

# SDK may not be installed as a package; add sdk/ to sys.path
_SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sdk")
if _SDK_PATH not in sys.path:
    sys.path.insert(0, _SDK_PATH)

from gispulse_sdk.streaming import (  # noqa: E402
    dedupe_by_change_id,
    dedupe_events,
)


async def _aiter(items: list[dict]) -> AsyncIterator[dict]:
    for item in items:
        yield item


def _ev(
    cid: int | None,
    *,
    ds: str | None = "ds-default",
    ev_type: str = "dml.changed",
) -> dict:
    """Build an event envelope for tests.

    ``ds=None`` means "no dataset_id in the payload" (heartbeat-shape).
    ``cid=None`` means "no change_id" (heartbeat / trigger.fired).
    """
    data: dict = {}
    if ds is not None:
        data["dataset_id"] = ds
    if cid is not None:
        data["change_id"] = cid
    return {
        "type": ev_type,
        "data": data,
        "timestamp": "2026-04-25T00:00:00",
    }


# ---------------------------------------------------------------------------
# Standard dedup behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedupe_drops_duplicates_in_order() -> None:
    """Feed [1,2,1,3,2] (same dataset) → expect [1,2,3]."""
    src = _aiter([_ev(1), _ev(2), _ev(1), _ev(3), _ev(2)])
    out = [ev async for ev in dedupe_events(src)]
    cids = [ev["data"]["change_id"] for ev in out]
    assert cids == [1, 2, 3]


@pytest.mark.asyncio
async def test_events_without_change_id_pass_through() -> None:
    """Heartbeats / events with no change_id are forwarded unchanged."""
    items = [
        _ev(1),
        _ev(None, ev_type="ping"),
        _ev(1),
        _ev(None, ev_type="ping"),
    ]
    src = _aiter(items)
    out = [ev async for ev in dedupe_events(src)]
    types = [ev["type"] for ev in out]
    # First dml passes, dup dropped, both pings pass through.
    assert types == ["dml.changed", "ping", "ping"]


@pytest.mark.asyncio
async def test_events_without_dataset_id_pass_through() -> None:
    """Pre-Lot 2 v2 envelopes (no dataset_id) bypass dedup so we don't
    silently drop legitimate events from older servers."""
    items = [_ev(1, ds=None), _ev(1, ds=None), _ev(2, ds=None)]
    src = _aiter(items)
    out = [ev async for ev in dedupe_events(src)]
    # Without dataset_id we can't dedup safely; every event passes.
    assert len(out) == 3


# ---------------------------------------------------------------------------
# Multi-tenant collision (Lot 2 v2 — Beta E2E)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_tenant_change_id_collision_keeps_both() -> None:
    """Two datasets sharing change_id=1 must both pass.

    Reproducer for the Beta E2E bug: each ChangeLogWatcher numbers
    change_id from 1 inside its own GPKG, so two tenants both emit
    ``change_id=1``. Keying dedup on ``change_id`` alone silently drops
    the second tenant's event. Keying on ``(dataset_id, change_id)``
    keeps both; only a true duplicate within the same dataset is dropped.
    """
    src = _aiter(
        [
            _ev(1, ds="A"),
            _ev(2, ds="A"),
            _ev(1, ds="B"),  # same cid, different tenant — must pass.
            _ev(1, ds="A"),  # true duplicate inside dataset A — must drop.
        ]
    )
    out = [ev async for ev in dedupe_events(src)]
    keys = [
        (ev["data"]["dataset_id"], ev["data"]["change_id"]) for ev in out
    ]
    assert keys == [("A", 1), ("A", 2), ("B", 1)]


# ---------------------------------------------------------------------------
# LRU window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lru_eviction_allows_old_id_to_resurface() -> None:
    """With window_size=2: feed [1,2,3,1] (same dataset) → expect [1,2,3,1]
    because (ds, 1) fell out of the window after (ds, 2), (ds, 3)."""
    src = _aiter([_ev(1), _ev(2), _ev(3), _ev(1)])
    out = [ev async for ev in dedupe_events(src, window_size=2)]
    cids = [ev["data"]["change_id"] for ev in out]
    assert cids == [1, 2, 3, 1]


@pytest.mark.asyncio
async def test_lru_within_window_drops_dupe() -> None:
    """With window_size=3: feed [1,2,3,1] → 1 still in window → drop."""
    src = _aiter([_ev(1), _ev(2), _ev(3), _ev(1)])
    out = [ev async for ev in dedupe_events(src, window_size=3)]
    cids = [ev["data"]["change_id"] for ev in out]
    assert cids == [1, 2, 3]


@pytest.mark.asyncio
async def test_invalid_window_size_raises() -> None:
    src = _aiter([_ev(1)])
    gen = dedupe_events(src, window_size=0)
    with pytest.raises(ValueError):
        async for _ in gen:
            pass


@pytest.mark.asyncio
async def test_malformed_event_treated_as_no_change_id() -> None:
    """Event with non-dict ``data`` should pass through (we never assume
    perfect server payloads)."""
    items = [{"type": "weird"}, {"type": "weird", "data": None}, _ev(1)]
    src = _aiter(items)
    out = [ev async for ev in dedupe_events(src)]
    assert len(out) == 3


# ---------------------------------------------------------------------------
# Backwards-compat alias
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedupe_by_change_id_alias_routes_to_dedupe_events() -> None:
    """The legacy name must keep working and use the new tuple key."""
    src = _aiter([_ev(1, ds="A"), _ev(1, ds="B"), _ev(1, ds="A")])
    out = [ev async for ev in dedupe_by_change_id(src)]
    keys = [
        (ev["data"]["dataset_id"], ev["data"]["change_id"]) for ev in out
    ]
    # Same multi-tenant semantics — third event drops as a dup of (A, 1).
    assert keys == [("A", 1), ("B", 1)]
    assert dedupe_by_change_id is dedupe_events
