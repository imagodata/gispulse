"""
Tests unitaires pour adapters/esb/dlq.py — DeadLetterQueue.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from gispulse.adapters.esb.bus_message import BusMessage
from gispulse.adapters.esb.dlq import DeadLetterQueue, DLQEntry
from gispulse.adapters.esb.enums import MessageStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_message(operation: str = "PROCESS") -> BusMessage:
    """Crée un BusMessage minimal pour les tests."""
    return BusMessage(
        id=uuid4(),
        channel_id=uuid4(),
        payload={"operation": operation, "data_category": "vector"},
        message_status=MessageStatus.FAILED,
    )


@pytest.fixture
def dlq() -> DeadLetterQueue:
    return DeadLetterQueue(max_size=10, max_retries=3)


@pytest.fixture
def msg() -> BusMessage:
    return make_message()


# ---------------------------------------------------------------------------
# push / pop FIFO
# ---------------------------------------------------------------------------

class TestPushPop:
    def test_push_increases_size(self, dlq, msg):
        assert dlq.size == 0
        dlq.push(msg, reason="test", error="oops")
        assert dlq.size == 1

    def test_pop_returns_oldest_first(self, dlq):
        m1 = make_message("INSERT")
        m2 = make_message("UPDATE")
        dlq.push(m1, reason="r1", error="e1")
        dlq.push(m2, reason="r2", error="e2")
        entry = dlq.pop()
        assert entry is not None
        assert entry.message.id == m1.id

    def test_pop_decreases_size(self, dlq, msg):
        dlq.push(msg, reason="r", error="e")
        dlq.pop()
        assert dlq.size == 0

    def test_pop_empty_returns_none(self, dlq):
        assert dlq.pop() is None

    def test_is_empty_on_new_dlq(self, dlq):
        assert dlq.is_empty is True

    def test_is_not_empty_after_push(self, dlq, msg):
        dlq.push(msg, reason="r", error="e")
        assert dlq.is_empty is False

    def test_fifo_order_preserved(self, dlq):
        messages = [make_message() for _ in range(5)]
        for m in messages:
            dlq.push(m, reason="r", error="e")
        popped_ids = [dlq.pop().message.id for _ in range(5)]
        assert popped_ids == [m.id for m in messages]


# ---------------------------------------------------------------------------
# peek
# ---------------------------------------------------------------------------

class TestPeek:
    def test_peek_does_not_remove(self, dlq, msg):
        dlq.push(msg, reason="r", error="e")
        dlq.peek(10)
        assert dlq.size == 1

    def test_peek_returns_oldest_first(self, dlq):
        messages = [make_message() for _ in range(3)]
        for m in messages:
            dlq.push(m, reason="r", error="e")
        entries = dlq.peek(3)
        assert [e.message.id for e in entries] == [m.id for m in messages]

    def test_peek_count_limits_results(self, dlq):
        for _ in range(5):
            dlq.push(make_message(), reason="r", error="e")
        entries = dlq.peek(2)
        assert len(entries) == 2

    def test_peek_count_exceeds_size(self, dlq):
        dlq.push(make_message(), reason="r", error="e")
        entries = dlq.peek(100)
        assert len(entries) == 1

    def test_peek_empty(self, dlq):
        assert dlq.peek(10) == []


# ---------------------------------------------------------------------------
# retry
# ---------------------------------------------------------------------------

class TestRetry:
    def test_retry_increments_count(self, dlq, msg):
        dlq.push(msg, reason="r", error="e")
        entry = dlq.pop()
        assert dlq.retry(entry) is True
        assert entry.retry_count == 1

    def test_retry_multiple_times(self, dlq, msg):
        dlq.push(msg, reason="r", error="e")
        entry = dlq.pop()
        dlq.retry(entry)
        dlq.retry(entry)
        assert entry.retry_count == 2

    def test_retry_returns_false_at_max(self):
        dlq = DeadLetterQueue(max_retries=3)
        msg = make_message()
        dlq.push(msg, reason="r", error="e")
        entry = dlq.pop()
        # 3 retries réussis
        assert dlq.retry(entry) is True
        assert dlq.retry(entry) is True
        assert dlq.retry(entry) is True
        # 4ème doit retourner False
        assert dlq.retry(entry) is False

    def test_retry_count_not_incremented_when_max_reached(self):
        dlq = DeadLetterQueue(max_retries=1)
        msg = make_message()
        dlq.push(msg, reason="r", error="e")
        entry = dlq.pop()
        dlq.retry(entry)   # retry_count = 1
        dlq.retry(entry)   # max atteint, retourne False
        assert entry.retry_count == 1

    def test_retry_zero_max(self):
        dlq = DeadLetterQueue(max_retries=0)
        msg = make_message()
        dlq.push(msg, reason="r", error="e")
        entry = dlq.pop()
        assert dlq.retry(entry) is False


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------

class TestPurge:
    def test_purge_all(self, dlq):
        for _ in range(3):
            dlq.push(make_message(), reason="r", error="e")
        removed = dlq.purge()
        assert removed == 3
        assert dlq.size == 0

    def test_purge_older_than_removes_old_entries(self):
        dlq = DeadLetterQueue()
        now = datetime.now(timezone.utc)

        # Entrée ancienne (simulée via moved_at patch)
        old_msg = make_message()
        dlq.push(old_msg, reason="old", error="e")
        # Modifier le moved_at de la première entrée pour qu'il soit dans le passé
        dlq._entries[0].moved_at = now - timedelta(hours=2)

        # Entrée récente
        new_msg = make_message()
        dlq.push(new_msg, reason="new", error="e")

        cutoff = now - timedelta(hours=1)
        removed = dlq.purge(older_than=cutoff)
        assert removed == 1
        assert dlq.size == 1
        # L'entrée restante doit être la récente
        entry = dlq.pop()
        assert entry.message.id == new_msg.id

    def test_purge_older_than_nothing_to_remove(self, dlq):
        dlq.push(make_message(), reason="r", error="e")
        future = datetime.now(timezone.utc) - timedelta(hours=1)
        removed = dlq.purge(older_than=future)
        # Entrée récente ne doit pas être retirée
        assert removed == 0
        assert dlq.size == 1

    def test_purge_empty_dlq(self, dlq):
        assert dlq.purge() == 0

    def test_purge_all_older_than_future(self, dlq):
        for _ in range(3):
            dlq.push(make_message(), reason="r", error="e")
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        removed = dlq.purge(older_than=future)
        assert removed == 3
        assert dlq.size == 0


# ---------------------------------------------------------------------------
# max_size (éjection des anciens)
# ---------------------------------------------------------------------------

class TestMaxSize:
    def test_max_size_respected(self):
        dlq = DeadLetterQueue(max_size=3)
        messages = [make_message() for _ in range(5)]
        for m in messages:
            dlq.push(m, reason="r", error="e")
        assert dlq.size == 3

    def test_oldest_ejected_when_full(self):
        dlq = DeadLetterQueue(max_size=3)
        messages = [make_message() for _ in range(5)]
        for m in messages:
            dlq.push(m, reason="r", error="e")
        # Les 3 derniers messages doivent être conservés
        entries = dlq.peek(3)
        retained_ids = [e.message.id for e in entries]
        assert messages[2].id in retained_ids
        assert messages[3].id in retained_ids
        assert messages[4].id in retained_ids
        # Les 2 premiers sont éjectés
        assert messages[0].id not in retained_ids
        assert messages[1].id not in retained_ids


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    def test_stats_empty(self, dlq):
        stats = dlq.get_stats()
        assert stats["count"] == 0
        assert stats["oldest"] is None
        assert stats["newest"] is None
        assert stats["reasons"] == {}

    def test_stats_count(self, dlq):
        dlq.push(make_message(), reason="r1", error="e")
        dlq.push(make_message(), reason="r1", error="e")
        dlq.push(make_message(), reason="r2", error="e")
        stats = dlq.get_stats()
        assert stats["count"] == 3

    def test_stats_reasons_breakdown(self, dlq):
        dlq.push(make_message(), reason="processing_error", error="e")
        dlq.push(make_message(), reason="processing_error", error="e")
        dlq.push(make_message(), reason="max_retries", error="e")
        stats = dlq.get_stats()
        assert stats["reasons"]["processing_error"] == 2
        assert stats["reasons"]["max_retries"] == 1

    def test_stats_oldest_newest(self, dlq):
        dlq.push(make_message(), reason="r", error="e")
        dlq.push(make_message(), reason="r", error="e")
        stats = dlq.get_stats()
        assert stats["oldest"] is not None
        assert stats["newest"] is not None
        # oldest <= newest (ordre chronologique)
        assert stats["oldest"] <= stats["newest"]

    def test_stats_single_entry(self, dlq, msg):
        dlq.push(msg, reason="solo", error="e")
        stats = dlq.get_stats()
        assert stats["count"] == 1
        assert stats["oldest"] == stats["newest"]
        assert stats["reasons"] == {"solo": 1}


# ---------------------------------------------------------------------------
# DLQEntry dataclass
# ---------------------------------------------------------------------------

class TestDLQEntry:
    def test_entry_stores_message(self, msg):
        entry = DLQEntry(message=msg, reason="test", original_error="boom")
        assert entry.message is msg
        assert entry.reason == "test"
        assert entry.original_error == "boom"
        assert entry.retry_count == 0

    def test_entry_moved_at_defaults_to_now(self, msg):
        before = datetime.now(timezone.utc)
        entry = DLQEntry(message=msg, reason="r", original_error="e")
        after = datetime.now(timezone.utc)
        assert before <= entry.moved_at <= after


# ---------------------------------------------------------------------------
# ESB __init__ exports
# ---------------------------------------------------------------------------

class TestESBExports:
    def test_dlq_importable_from_module(self):
        """DLQ is experimental — import directly from gispulse.adapters.esb.dlq."""
        from gispulse.adapters.esb.dlq import DeadLetterQueue as DLQ, DLQEntry as Entry
        assert DLQ is DeadLetterQueue
        assert Entry is DLQEntry
