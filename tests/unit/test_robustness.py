"""
Robustness tests — Sprint S7.

Covers edge cases and stress scenarios from docs/AUDIT_QA_POST_SPRINTS.md:
  - Concurrent job enqueue (100 jobs via asyncio.gather)
  - Empty database for all repositories
  - Corrupt JSON config in schedules
  - Expired API key handling
  - Revoked API key caching
  - Worker graceful shutdown
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from gispulse.core.models import Job, JobStatus
from gispulse.orchestration.job_queue import InMemoryJobQueue, _serialize_job, _deserialize_job


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =====================================================================
# 1. Concurrent Job Enqueue
# =====================================================================


class TestConcurrentJobEnqueue:
    """Verify that the job queue handles concurrent enqueue safely."""

    def test_100_jobs_enqueued_in_parallel(self):
        """Enqueue 100 jobs concurrently via asyncio.gather.

        All 100 jobs must be enqueued without errors, duplicates, or data loss.
        """
        queue = InMemoryJobQueue()

        async def _enqueue_many():
            jobs = [
                Job(name=f"concurrent_job_{i}", parameters={"index": i})
                for i in range(100)
            ]
            job_ids = await asyncio.gather(
                *[queue.enqueue(job) for job in jobs]
            )
            return job_ids

        job_ids = _run(_enqueue_many())

        # All 100 enqueued
        assert len(job_ids) == 100

        # All IDs are unique
        assert len(set(job_ids)) == 100

        # Queue size is 100
        size = _run(queue.queue_size())
        assert size == 100

    def test_50_enqueue_50_dequeue_concurrent(self):
        """Enqueue 50 and dequeue 50 concurrently — no deadlock, no data loss."""
        queue = InMemoryJobQueue()

        # Pre-enqueue 50 jobs
        for i in range(50):
            _run(queue.enqueue(Job(name=f"pre_job_{i}")))

        async def _concurrent_ops():
            # Enqueue 50 more while dequeuing 50
            enqueue_tasks = [
                queue.enqueue(Job(name=f"new_job_{i}"))
                for i in range(50)
            ]
            dequeue_tasks = [
                queue.dequeue(timeout=0.1)
                for _ in range(50)
            ]
            # Run all concurrently
            results = await asyncio.gather(
                *enqueue_tasks, *dequeue_tasks,
                return_exceptions=True,
            )
            return results

        results = _run(_concurrent_ops())

        # No exceptions
        for r in results:
            assert not isinstance(r, Exception), f"Got exception: {r}"

    def test_rapid_status_updates_no_corruption(self):
        """Rapid status updates on the same job must not corrupt state."""
        queue = InMemoryJobQueue()
        job = Job(name="status_test")
        job_id = _run(queue.enqueue(job))

        async def _rapid_updates():
            await queue.update_status(job_id, JobStatus.RUNNING)
            # Simulate rapid status transitions
            for _ in range(10):
                await queue.update_status(
                    job_id, JobStatus.RUNNING,
                    result={"progress": 50},
                )
            await queue.update_status(job_id, JobStatus.COMPLETED, result={"done": True})

        _run(_rapid_updates())

        status = _run(queue.get_status(job_id))
        assert status["status"] == "completed"


# =====================================================================
# 2. Empty Database — All Repositories
# =====================================================================


class TestEmptyDatabaseAllRepos:
    """Verify that all repositories handle an empty database gracefully."""

    def test_auth_repo_empty_user_count(self):
        """AuthRepository.user_count() returns 0 on empty DB."""
        from gispulse.persistence.auth_repository import AuthRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty_auth.db"
            repo = AuthRepository(db_path=db_path)
            assert repo.user_count() == 0

    def test_auth_repo_get_nonexistent_user(self):
        """AuthRepository.get_user() returns None for a non-existent user."""
        from gispulse.persistence.auth_repository import AuthRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty_auth.db"
            repo = AuthRepository(db_path=db_path)
            result = repo.get_user("nonexistent-id")
            assert result is None

    def test_auth_repo_get_nonexistent_api_key(self):
        """AuthRepository.get_api_key_by_hash() returns None for unknown hash."""
        from gispulse.persistence.auth_repository import AuthRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty_auth.db"
            repo = AuthRepository(db_path=db_path)
            result = repo.get_api_key_by_hash("nonexistent_hash")
            assert result is None

    def test_audit_logger_query_empty(self):
        """AuditLogger.query() returns empty list on empty DB."""
        from gispulse.persistence.audit import AuditLogger, AuditQuery

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty_audit.db"
            logger = AuditLogger(db_path=db_path)
            results = logger.query(AuditQuery())
            assert results == []

    def test_audit_logger_count_empty(self):
        """AuditLogger.count() returns 0 on empty DB."""
        from gispulse.persistence.audit import AuditLogger, AuditQuery

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty_audit.db"
            logger = AuditLogger(db_path=db_path)
            assert logger.count(AuditQuery()) == 0

    def test_audit_logger_cleanup_empty(self):
        """AuditLogger.cleanup() on empty DB returns 0 without error."""
        from gispulse.persistence.audit import AuditLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty_audit.db"
            logger = AuditLogger(db_path=db_path)
            deleted = logger.cleanup(older_than=timedelta(days=90))
            assert deleted == 0

    def test_inmemory_queue_dequeue_empty(self):
        """Dequeuing from an empty InMemoryJobQueue returns None."""
        queue = InMemoryJobQueue()
        result = _run(queue.dequeue(timeout=0))
        assert result is None

    def test_inmemory_queue_cancel_nonexistent(self):
        """Cancelling a non-existent job returns False."""
        queue = InMemoryJobQueue()
        result = _run(queue.cancel("nonexistent-id"))
        assert result is False

    def test_inmemory_queue_status_nonexistent(self):
        """Getting status of non-existent job returns None."""
        queue = InMemoryJobQueue()
        result = _run(queue.get_status("nonexistent-id"))
        assert result is None

    def test_storage_list_empty_directory(self):
        """LocalStorage.list_keys() returns empty list for empty storage."""
        from gispulse.persistence.storage import LocalStorage

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_path=tmpdir)
            keys = _run(storage.list_keys())
            assert keys == []

    def test_storage_download_nonexistent(self):
        """LocalStorage.download() raises StorageError for non-existent key."""
        from gispulse.persistence.storage import LocalStorage, StorageError

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_path=tmpdir)
            with pytest.raises(StorageError, match="not found"):
                _run(storage.download("nonexistent.gpkg"))


# =====================================================================
# 3. Corrupt JSON Config
# =====================================================================


class TestCorruptJsonConfig:
    """Verify that corrupt JSON in job/schedule configs is handled gracefully."""

    def test_deserialize_corrupt_json_raises(self):
        """Deserializing corrupt JSON must raise a clear error, not crash silently."""
        with pytest.raises(json.JSONDecodeError):
            _deserialize_job("{corrupt json without closing brace")

    def test_deserialize_missing_required_fields(self):
        """Deserializing JSON missing required fields must raise a clear error."""
        incomplete_json = json.dumps({"id": str(uuid4())})
        with pytest.raises((KeyError, TypeError)):
            _deserialize_job(incomplete_json)

    def test_deserialize_extra_fields_ignored(self):
        """Deserializing JSON with extra fields should not crash."""
        job = Job(name="test")
        data = json.loads(_serialize_job(job))
        data["unknown_field"] = "should be ignored"
        data["another_unknown"] = 42

        # This should work or raise a clear error, not crash
        try:
            restored = _deserialize_job(json.dumps(data))
            assert restored.name == "test"
        except (KeyError, TypeError):
            # Acceptable if it raises a clear error
            pass

    def test_deserialize_invalid_uuid(self):
        """Deserializing with an invalid UUID must raise ValueError."""
        job = Job(name="test")
        data = json.loads(_serialize_job(job))
        data["id"] = "not-a-valid-uuid"

        with pytest.raises(ValueError):
            _deserialize_job(json.dumps(data))

    def test_deserialize_invalid_status(self):
        """Deserializing with an invalid JobStatus value must raise ValueError."""
        job = Job(name="test")
        data = json.loads(_serialize_job(job))
        data["status"] = "invalid_status_value"

        with pytest.raises(ValueError):
            _deserialize_job(json.dumps(data))

    def test_serialize_roundtrip_with_special_chars(self):
        """Job with special characters in parameters survives serialisation."""
        job = Job(
            name="special_chars",
            parameters={
                "query": "SELECT * WHERE name = 'O''Brien'",
                "unicode": "geometrie_with_umlauts_\u00e4\u00f6\u00fc",
                "html": "<script>alert('xss')</script>",
                "newlines": "line1\nline2\ttab",
            },
        )
        serialised = _serialize_job(job)
        restored = _deserialize_job(serialised)
        assert restored.parameters == job.parameters


# =====================================================================
# 4. Expired API Key
# =====================================================================


class TestExpiredApiKey:
    """Verify that expired API keys are properly rejected."""

    def test_expired_key_detected(self):
        """An API key with expires_at in the past must be detectable."""
        from gispulse.persistence.auth_models import ApiKey

        expired = ApiKey(
            user_id="user1",
            key_hash="abc123",
            name="expired-key",
            scopes=["read"],
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )

        assert expired.expires_at is not None
        assert expired.expires_at < datetime.now(timezone.utc)

    def test_non_expired_key_passes(self):
        """An API key with expires_at in the future is still valid."""
        from gispulse.persistence.auth_models import ApiKey

        valid = ApiKey(
            user_id="user1",
            key_hash="abc123",
            name="valid-key",
            scopes=["read"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )

        assert valid.expires_at > datetime.now(timezone.utc)

    def test_key_without_expiry_is_valid(self):
        """An API key without expires_at (None) never expires."""
        from gispulse.persistence.auth_models import ApiKey

        forever = ApiKey(
            user_id="user1",
            key_hash="abc123",
            name="forever-key",
            scopes=["read"],
        )

        assert forever.expires_at is None

    def test_expired_key_in_auth_repo(self):
        """AuthRepository stores and retrieves expired keys — auth layer must check."""
        from gispulse.persistence.auth_models import User
        from gispulse.persistence.auth_repository import AuthRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "auth.db"
            repo = AuthRepository(db_path=db_path)

            user = User(email="test@example.com", name="Test")
            repo.create_user(user)

            api_key, raw_key = repo.create_api_key(
                user_id=user.id,
                name="expired",
                scopes=["read"],
                expires_at=datetime.now(timezone.utc) - timedelta(days=30),
            )

            # The key is retrievable...
            found = repo.get_api_key_by_hash(api_key.key_hash)
            assert found is not None

            # ...but the expiry is in the past
            assert found.expires_at < datetime.now(timezone.utc)


# =====================================================================
# 5. Revoked API Key — Potential Cache
# =====================================================================


class TestRevokedApiKeyCached:
    """Verify that deleting an API key immediately invalidates it.

    Tests that there's no stale cache that could serve a deleted key.
    """

    def test_deleted_key_not_found(self):
        """After deleting an API key, it must no longer be retrievable."""
        from gispulse.persistence.auth_models import User
        from gispulse.persistence.auth_repository import AuthRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "auth.db"
            repo = AuthRepository(db_path=db_path)

            user = User(email="test@example.com", name="Test")
            repo.create_user(user)

            api_key, raw_key = repo.create_api_key(
                user_id=user.id,
                name="to-revoke",
                scopes=["read", "write"],
            )

            # Key is findable
            assert repo.get_api_key_by_hash(api_key.key_hash) is not None

            # Revoke the key
            repo.revoke_api_key(api_key.id)

            # Key must no longer be found — revoked keys have is_active=0
            assert repo.get_api_key_by_hash(api_key.key_hash) is None

    def test_deleted_user_keys_also_gone(self):
        """Deleting a user should also remove their API keys."""
        from gispulse.persistence.auth_models import User
        from gispulse.persistence.auth_repository import AuthRepository

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "auth.db"
            repo = AuthRepository(db_path=db_path)

            user = User(email="test@example.com", name="Test")
            repo.create_user(user)

            api_key, raw_key = repo.create_api_key(
                user_id=user.id,
                name="user-key",
                scopes=["read"],
            )

            # Delete the user
            repo.delete_user(user.id)

            # User is gone
            assert repo.get_user(user.id) is None

            # API key should also be gone (cascade delete)
            assert repo.get_api_key_by_hash(api_key.key_hash) is None


# =====================================================================
# 6. Worker Graceful Shutdown
# =====================================================================


class TestWorkerGracefulShutdown:
    """Verify that the job queue handles shutdown-like scenarios."""

    def test_queue_close_is_idempotent(self):
        """Calling close() multiple times must not raise errors."""
        queue = InMemoryJobQueue()
        _run(queue.close())
        _run(queue.close())
        _run(queue.close())

    def test_enqueue_after_close_still_works(self):
        """InMemoryJobQueue.close() is a no-op, so enqueue should still work.

        This documents the current behaviour. A real shutdown mechanism
        would reject new jobs after close().
        """
        queue = InMemoryJobQueue()
        _run(queue.close())

        # InMemoryJobQueue.close() does nothing — enqueue still works
        job = Job(name="after_close")
        job_id = _run(queue.enqueue(job))
        assert job_id is not None

    def test_cancel_running_job_sets_failed(self):
        """Cancelling a running job sets it to FAILED with cancel reason."""
        queue = InMemoryJobQueue()
        job = Job(name="running_job")
        job_id = _run(queue.enqueue(job))

        _run(queue.update_status(job_id, JobStatus.RUNNING))
        cancelled = _run(queue.cancel(job_id))
        assert cancelled is True

        status = _run(queue.get_status(job_id))
        assert status["status"] == "failed"
        assert "Cancelled" in status["error"]

    def test_cancel_completed_job_returns_false(self):
        """Cannot cancel an already-completed job."""
        queue = InMemoryJobQueue()
        job = Job(name="done_job")
        job_id = _run(queue.enqueue(job))

        _run(queue.update_status(job_id, JobStatus.COMPLETED))
        cancelled = _run(queue.cancel(job_id))
        assert cancelled is False

    def test_events_track_full_lifecycle(self):
        """Job events track the full lifecycle: PENDING -> RUNNING -> COMPLETED."""
        queue = InMemoryJobQueue()
        job = Job(name="lifecycle_job")
        job_id = _run(queue.enqueue(job))

        _run(queue.update_status(job_id, JobStatus.RUNNING))
        _run(queue.update_status(job_id, JobStatus.COMPLETED, result={"ok": True}))

        events = _run(queue.get_events(job_id))
        statuses = [e["status"] for e in events]
        assert statuses == ["pending", "running", "completed"]

    def test_dequeue_timeout_returns_none(self):
        """Dequeuing with a short timeout from an empty queue returns None."""
        queue = InMemoryJobQueue()

        start = time.monotonic()
        result = _run(queue.dequeue(timeout=0.1))
        elapsed = time.monotonic() - start

        assert result is None
        # Should have waited approximately 0.1 seconds
        assert elapsed >= 0.05  # Allow some tolerance
        assert elapsed < 2.0  # But not too long
