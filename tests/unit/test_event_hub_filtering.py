"""Topic / trigger_id / table filtering on EventHub subscriptions (#453)."""

from __future__ import annotations

import asyncio
import json

import pytest

from gispulse.adapters.http.event_hub import EventHub


def _drain(queue: asyncio.Queue[str]) -> list[dict]:
    out: list[dict] = []
    while not queue.empty():
        out.append(json.loads(queue.get_nowait()))
    return out


@pytest.mark.asyncio
async def test_no_filter_receives_every_event():
    hub = EventHub()
    q = hub.subscribe()  # wildcard — pre-filter behavior preserved
    hub.broadcast("trigger_fired", {"trigger_id": "t1"})
    hub.broadcast("layer_updated", {"table": "public.parcels"})
    hub.broadcast("job_completed", {"job_id": "j1"})

    received = _drain(q)
    assert [e["type"] for e in received] == [
        "trigger_fired",
        "layer_updated",
        "job_completed",
    ]


@pytest.mark.asyncio
async def test_topics_filter_isolates_event_types():
    hub = EventHub()
    q = hub.subscribe(topics={"trigger_fired"})
    hub.broadcast("trigger_fired", {"trigger_id": "t1"})
    hub.broadcast("layer_updated", {"table": "public.parcels"})
    hub.broadcast("job_completed", {})

    received = _drain(q)
    assert len(received) == 1
    assert received[0]["type"] == "trigger_fired"


@pytest.mark.asyncio
async def test_trigger_ids_filter_isolates_specific_trigger():
    hub = EventHub()
    q = hub.subscribe(trigger_ids={"abc-123"})
    hub.broadcast("trigger_fired", {"trigger_id": "abc-123"})
    hub.broadcast("trigger_fired", {"trigger_id": "xyz-999"})
    # R3.1: missing key → not excluded
    hub.broadcast("trigger_fired", {})

    received = _drain(q)
    assert len(received) == 2
    assert received[0]["data"]["trigger_id"] == "abc-123"
    assert "trigger_id" not in received[1]["data"]


@pytest.mark.asyncio
async def test_tables_filter_isolates_specific_tables():
    hub = EventHub()
    q = hub.subscribe(tables={"public.parcels", "public.batiments"})
    hub.broadcast("layer_updated", {"table": "public.parcels"})
    hub.broadcast("layer_updated", {"table": "public.batiments"})
    hub.broadcast("layer_updated", {"table": "public.routes"})
    # R3.1: missing key → not excluded
    hub.broadcast("layer_updated", {})

    received = _drain(q)
    tables = [e["data"].get("table") for e in received]
    assert tables == ["public.parcels", "public.batiments", None]


@pytest.mark.asyncio
async def test_combined_filters_apply_with_and():
    """100 broadcast events; only those matching topic AND table reach the queue."""
    hub = EventHub()
    q = hub.subscribe(
        topics={"trigger_fired"},
        tables={"public.parcels"},
    )

    # 25 each of 4 combinations — only one combination passes both filters.
    for i in range(25):
        hub.broadcast("trigger_fired", {"table": "public.parcels", "n": i})  # PASS
        hub.broadcast("trigger_fired", {"table": "public.routes", "n": i})   # FAIL on table
        hub.broadcast("layer_updated", {"table": "public.parcels", "n": i})  # FAIL on topic
        hub.broadcast("job_completed", {"n": i})                              # FAIL on both

    received = _drain(q)
    assert len(received) == 25
    assert all(e["type"] == "trigger_fired" for e in received)
    assert all(e["data"]["table"] == "public.parcels" for e in received)
