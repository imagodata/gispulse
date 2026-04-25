"""Unit tests for :func:`gispulse_sdk.streaming.dedupe_by_change_id`.

Tests cover:
- Standard dedup over a small window.
- Events without ``change_id`` pass through unchanged.
- LRU eviction when the window is full.
- ValueError on invalid window_size.
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

from gispulse_sdk.streaming import dedupe_by_change_id  # noqa: E402


async def _aiter(items: list[dict]) -> AsyncIterator[dict]:
    for item in items:
        yield item


def _ev(cid: int | None, ev_type: str = "dml.changed") -> dict:
    if cid is None:
        return {"type": ev_type, "data": {}, "timestamp": "2026-04-25T00:00:00"}
    return {
        "type": ev_type,
        "data": {"change_id": cid},
        "timestamp": "2026-04-25T00:00:00",
    }


@pytest.mark.asyncio
async def test_dedupe_drops_duplicates_in_order() -> None:
    """Feed [1,2,1,3,2] → expect [1,2,3]."""
    src = _aiter([_ev(1), _ev(2), _ev(1), _ev(3), _ev(2)])
    out = [ev async for ev in dedupe_by_change_id(src)]
    cids = [ev["data"]["change_id"] for ev in out]
    assert cids == [1, 2, 3]


@pytest.mark.asyncio
async def test_events_without_change_id_pass_through() -> None:
    """Heartbeats / events with no change_id are forwarded unchanged."""
    items = [_ev(1), _ev(None, ev_type="ping"), _ev(1), _ev(None, ev_type="ping")]
    src = _aiter(items)
    out = [ev async for ev in dedupe_by_change_id(src)]
    types = [ev["type"] for ev in out]
    # First dml passes, dup dropped, both pings pass through.
    assert types == ["dml.changed", "ping", "ping"]


@pytest.mark.asyncio
async def test_lru_eviction_allows_old_id_to_resurface() -> None:
    """With window_size=2: feed [1,2,3,1] → expect [1,2,3,1] because 1
    fell out of the window after 2,3 were ingested.
    """
    src = _aiter([_ev(1), _ev(2), _ev(3), _ev(1)])
    out = [ev async for ev in dedupe_by_change_id(src, window_size=2)]
    cids = [ev["data"]["change_id"] for ev in out]
    assert cids == [1, 2, 3, 1]


@pytest.mark.asyncio
async def test_lru_within_window_drops_dupe() -> None:
    """With window_size=3: feed [1,2,3,1] → 1 still in window → drop."""
    src = _aiter([_ev(1), _ev(2), _ev(3), _ev(1)])
    out = [ev async for ev in dedupe_by_change_id(src, window_size=3)]
    cids = [ev["data"]["change_id"] for ev in out]
    assert cids == [1, 2, 3]


@pytest.mark.asyncio
async def test_invalid_window_size_raises() -> None:
    src = _aiter([_ev(1)])
    gen = dedupe_by_change_id(src, window_size=0)
    with pytest.raises(ValueError):
        async for _ in gen:
            pass


@pytest.mark.asyncio
async def test_malformed_event_treated_as_no_change_id() -> None:
    """Event with non-dict ``data`` should pass through (we never assume
    perfect server payloads)."""
    items = [{"type": "weird"}, {"type": "weird", "data": None}, _ev(1)]
    src = _aiter(items)
    out = [ev async for ev in dedupe_by_change_id(src)]
    assert len(out) == 3
