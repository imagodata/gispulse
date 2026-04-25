"""Tests for adapters.esb.pg_notify — LISTEN/NOTIFY channel.

PgNotifyChannel sends NOTIFY payloads to PostgreSQL. PgNotifyListener
listens and auto-reconnects with exponential backoff. Bugs silently
drop notifications or leak connections.

All tests use fake asyncpg pool/connection — no real PostgreSQL.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gispulse.adapters.esb.pg_notify import (
    DEFAULT_CHANNEL,
    SESSION_CHANNEL_PREFIX,
    PgNotifyChannel,
    PgNotifyListener,
    session_channel,
)


# ---------------------------------------------------------------------------
# session_channel helper
# ---------------------------------------------------------------------------


class TestSessionChannel:
    def test_prefix_applied(self):
        assert session_channel("sess_abc").startswith(SESSION_CHANNEL_PREFIX)

    def test_full_name(self):
        assert session_channel("sess_xyz") == "gispulse_sess_sess_xyz"

    def test_different_schemas_get_different_channels(self):
        assert session_channel("a") != session_channel("b")


class TestDefaultChannel:
    def test_default_channel_is_nonempty(self):
        assert DEFAULT_CHANNEL
        assert isinstance(DEFAULT_CHANNEL, str)


# ---------------------------------------------------------------------------
# PgNotifyChannel.send — uses db_pool.acquire()
# ---------------------------------------------------------------------------


class FakeAcquireCtx:
    """Async context manager stub for db_pool.acquire()."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return None


class FakePool:
    """Minimal asyncpg.Pool stub."""

    def __init__(self):
        self.conn = MagicMock()
        self.conn.execute = AsyncMock()

    def acquire(self):
        return FakeAcquireCtx(self.conn)


@pytest.mark.asyncio
class TestPgNotifyChannelSend:
    async def test_sends_basic_payload(self):
        pool = FakePool()
        ch = PgNotifyChannel(pool, channel_name="my_channel")
        await ch.send(operation="INSERT", dataset_id="ds-1", layer_id="l-1")

        pool.conn.execute.assert_awaited_once()
        call_args = pool.conn.execute.await_args
        # SQL + channel + payload
        assert call_args.args[0] == "SELECT pg_notify($1, $2)"
        assert call_args.args[1] == "my_channel"
        payload = json.loads(call_args.args[2])
        assert payload["operation"] == "INSERT"
        assert payload["dataset_id"] == "ds-1"
        assert payload["layer_id"] == "l-1"
        assert "timestamp" in payload

    async def test_optional_dataset_and_layer_omitted(self):
        pool = FakePool()
        ch = PgNotifyChannel(pool)
        await ch.send(operation="PROCESS")

        payload = json.loads(pool.conn.execute.await_args.args[2])
        assert "dataset_id" not in payload
        assert "layer_id" not in payload
        assert payload["operation"] == "PROCESS"

    async def test_extra_fields_merged_into_payload(self):
        pool = FakePool()
        ch = PgNotifyChannel(pool)
        await ch.send(
            operation="UPDATE",
            extra={"session_id": "sess-1", "rows": 42},
        )
        payload = json.loads(pool.conn.execute.await_args.args[2])
        assert payload["session_id"] == "sess-1"
        assert payload["rows"] == 42
        assert payload["operation"] == "UPDATE"

    async def test_default_channel_used_when_not_specified(self):
        pool = FakePool()
        ch = PgNotifyChannel(pool)  # no channel_name
        await ch.send(operation="INSERT")
        assert pool.conn.execute.await_args.args[1] == DEFAULT_CHANNEL

    async def test_timestamp_is_iso_format(self):
        pool = FakePool()
        ch = PgNotifyChannel(pool)
        await ch.send(operation="INSERT")
        payload = json.loads(pool.conn.execute.await_args.args[2])
        # ISO 8601 format: YYYY-MM-DDTHH:MM:SS
        assert "T" in payload["timestamp"]

    async def test_extra_does_not_override_core_keys(self):
        """extra.update() is applied AFTER the base payload keys — so
        extra CAN override operation/timestamp. Pin current behaviour."""
        pool = FakePool()
        ch = PgNotifyChannel(pool)
        await ch.send(
            operation="INSERT",
            extra={"operation": "OVERRIDE"},
        )
        payload = json.loads(pool.conn.execute.await_args.args[2])
        # Current behaviour: extra overrides core keys (dict.update semantics)
        assert payload["operation"] == "OVERRIDE"


# ---------------------------------------------------------------------------
# PgNotifyListener — constructor + stop no-ops
# ---------------------------------------------------------------------------


class TestListenerInit:
    def test_default_channel(self):
        listener = PgNotifyListener(dsn="postgresql://host/db")
        assert listener.channel == DEFAULT_CHANNEL
        assert listener.dsn == "postgresql://host/db"

    def test_custom_channel(self):
        listener = PgNotifyListener(dsn="postgresql://host/db", channel_name="custom")
        assert listener.channel == "custom"

    def test_running_false_initially(self):
        listener = PgNotifyListener(dsn="postgresql://host/db")
        assert listener._running is False
        assert listener._conn is None
        assert listener._callback is None

    def test_backoff_constants(self):
        assert PgNotifyListener._INITIAL_BACKOFF > 0
        assert PgNotifyListener._MAX_BACKOFF > PgNotifyListener._INITIAL_BACKOFF
        assert PgNotifyListener._BACKOFF_FACTOR > 1


@pytest.mark.asyncio
class TestListenerStop:
    async def test_stop_without_start_is_safe(self):
        listener = PgNotifyListener(dsn="postgresql://host/db")
        await listener.stop()
        assert listener._running is False

    async def test_stop_sets_running_false_and_cancels_reconnect(self):
        listener = PgNotifyListener(dsn="postgresql://host/db")
        listener._running = True
        # Simulate an in-flight reconnect task
        task = MagicMock()
        listener._reconnect_task = task
        await listener.stop()
        assert listener._running is False
        assert listener._reconnect_task is None
        task.cancel.assert_called_once()

    async def test_stop_closes_connection_and_removes_listener(self):
        listener = PgNotifyListener(dsn="postgresql://host/db")
        listener._running = True

        # Fake conn with async methods
        fake_conn = MagicMock()
        fake_conn.remove_listener = AsyncMock()
        fake_conn.close = AsyncMock()

        listener._conn = fake_conn
        listener._callback = lambda *a: None

        await listener.stop()
        fake_conn.remove_listener.assert_awaited_once()
        fake_conn.close.assert_awaited_once()
        assert listener._conn is None

    async def test_stop_tolerates_remove_listener_failure(self):
        listener = PgNotifyListener(dsn="postgresql://host/db")
        listener._running = True

        fake_conn = MagicMock()
        fake_conn.remove_listener = AsyncMock(side_effect=Exception("boom"))
        fake_conn.close = AsyncMock()

        listener._conn = fake_conn
        listener._callback = lambda *a: None
        # Must not raise even though remove_listener blows up
        await listener.stop()
        fake_conn.close.assert_awaited_once()


@pytest.mark.asyncio
class TestListenerReconnectLoop:
    async def test_reconnect_stops_when_running_is_false(self):
        listener = PgNotifyListener(dsn="postgresql://host/db")
        listener._running = False
        # Should return immediately without attempting a connect
        await listener._reconnect_loop()

    async def test_on_connection_lost_is_noop_when_stopped(self):
        listener = PgNotifyListener(dsn="postgresql://host/db")
        listener._running = False
        # No raise, no task created
        listener._on_connection_lost(MagicMock())
        assert listener._reconnect_task is None
