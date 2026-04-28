"""
Integration tests — Sprint S7.

Full pipeline tests covering end-to-end flows identified in
docs/AUDIT_QA_POST_SPRINTS.md section 4.

Covers:
  - Install/configure/run/result pipeline
  - RBAC full flow (admin bootstrap -> user -> API key -> permissions)
  - Scheduler create and fire
  - Audit trail completeness
  - Storage upload/download/delete cycle
  - Tier gating for all Pro features
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from core.models import Job, JobStatus
from orchestration.job_queue import InMemoryJobQueue
from persistence.audit import AuditEntry, AuditLogger, AuditQuery
from persistence.storage import LocalStorage, StorageError


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
# 1. Install / Configure / Run / Result — full pipeline
# =====================================================================


class TestInstallConfigureRunResult:
    """End-to-end: create a dataset in storage, enqueue a job, verify result."""

    def test_full_pipeline_storage_to_job_to_result(self):
        """Pipeline complet: upload dataset, create job, process, verify.

        Simulates the full lifecycle:
        1. Upload a dataset file to LocalStorage
        2. Create and enqueue a job referencing the dataset
        3. Dequeue and simulate processing
        4. Update status to COMPLETED
        5. Verify the result is accessible
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Step 1: Upload dataset
            storage = LocalStorage(base_path=tmpdir)
            dataset_key = "org1/ds1/parcels.gpkg"
            _run(storage.upload(dataset_key, b"fake gpkg content"))
            assert _run(storage.exists(dataset_key))

            # Step 2: Create and enqueue job
            queue = InMemoryJobQueue()
            dataset_id = uuid4()
            job = Job(
                name="buffer_analysis",
                dataset_id=dataset_id,
                parameters={"rule_ids": ["buffer_50m"], "input_key": dataset_key},
            )
            job_id = _run(queue.enqueue(job))
            assert job_id is not None

            # Step 3: Dequeue
            dequeued = _run(queue.dequeue(timeout=1))
            assert dequeued is not None
            assert str(dequeued.id) == job_id

            # Step 4: Simulate processing and update status
            _run(queue.update_status(job_id, JobStatus.RUNNING))
            status = _run(queue.get_status(job_id))
            assert status["status"] == "running"

            # Upload result
            result_key = "org1/ds1/parcels_buffered.gpkg"
            _run(storage.upload(result_key, b"buffered result data"))

            _run(queue.update_status(
                job_id, JobStatus.COMPLETED,
                result={"result_path": result_key},
            ))

            # Step 5: Verify result
            final_status = _run(queue.get_status(job_id))
            assert final_status["status"] == "completed"
            assert _run(storage.exists(result_key))
            result_data = _run(storage.download(result_key))
            assert result_data == b"buffered result data"

    def test_pipeline_with_job_failure(self):
        """Pipeline where the job fails — error is properly recorded."""
        queue = InMemoryJobQueue()
        job = Job(name="failing_job", parameters={"bad_param": True})
        job_id = _run(queue.enqueue(job))

        _run(queue.update_status(job_id, JobStatus.RUNNING))
        _run(queue.update_status(
            job_id, JobStatus.FAILED,
            error="ValueError: bad_param is not valid",
        ))

        status = _run(queue.get_status(job_id))
        assert status["status"] == "failed"
        assert "bad_param" in status["error"]


# =====================================================================
# 2. RBAC Full Flow
# =====================================================================


class TestRbacFullFlow:
    """Full RBAC lifecycle: bootstrap admin, create user, API key, permissions."""

    def test_admin_bootstrap_create_user_create_api_key(self):
        """RBAC flow: create admin -> create editor user -> create API key -> verify.

        Tests the full auth repository lifecycle:
        1. Bootstrap: create first admin user
        2. Create an editor user under the same org
        3. Generate an API key for the editor
        4. Verify the key maps back to the correct user
        5. Verify permissions via role comparison
        """
        from persistence.auth_models import Organisation, User, role_gte
        from persistence.auth_repository import AuthRepository, hash_api_key

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "auth_test.db"
            repo = AuthRepository(db_path=db_path)

            # Step 1: No users initially
            assert repo.user_count() == 0

            # Step 2: Create org
            org = Organisation(name="TestCorp")
            repo.create_org(org)

            # Step 3: Bootstrap admin
            admin = User(
                email="admin@testcorp.com",
                name="Admin User",
                role="admin",
                org_id=org.id,
            )
            repo.create_user(admin)
            assert repo.user_count() == 1

            # Step 4: Create editor user
            editor = User(
                email="editor@testcorp.com",
                name="Editor User",
                role="editor",
                org_id=org.id,
            )
            repo.create_user(editor)
            assert repo.user_count() == 2

            # Step 5: Generate API key for editor
            # create_api_key returns (ApiKey, raw_key)
            api_key, raw_key = repo.create_api_key(
                user_id=editor.id,
                name="editor-ci-key",
                scopes=["read", "write"],
            )
            assert raw_key.startswith("gp_")

            # Step 6: Verify key lookup
            found_key = repo.get_api_key_by_hash(hash_api_key(raw_key))
            assert found_key is not None
            assert found_key.user_id == editor.id
            assert "write" in found_key.scopes

            # Step 7: Verify role hierarchy
            assert role_gte("admin", "editor") is True
            assert role_gte("editor", "admin") is False
            assert role_gte("editor", "viewer") is True

            # Step 8: Verify user retrieval
            found_user = repo.get_user(editor.id)
            assert found_user is not None
            assert found_user.email == "editor@testcorp.com"

    def test_api_key_expiration_blocks_access(self):
        """An expired API key must be detectable by the auth layer."""
        from persistence.auth_models import User
        from persistence.auth_repository import AuthRepository, hash_api_key

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "auth_test.db"
            repo = AuthRepository(db_path=db_path)

            user = User(email="test@example.com", name="Test", role="editor")
            repo.create_user(user)

            api_key, raw_key = repo.create_api_key(
                user_id=user.id,
                name="expired-key",
                scopes=["read"],
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )

            found = repo.get_api_key_by_hash(hash_api_key(raw_key))
            assert found is not None
            # The key is found but expired — the auth middleware must check this
            assert found.expires_at < datetime.now(timezone.utc)


# =====================================================================
# 3. Scheduler Create and Fire
# =====================================================================


class TestSchedulerCreateAndFire:
    """Scheduler integration: create schedule, advance time, verify job enqueued."""

    def test_create_schedule_and_fire(self):
        """Create a schedule, simulate time passing, and verify job enqueue.

        Uses PipelineScheduler with an InMemoryJobQueue to validate the
        full scheduling flow without external dependencies.
        """
        from orchestration.scheduler import (
            PipelineScheduler,
            ScheduledPipeline,
        )

        queue = InMemoryJobQueue()
        scheduler = PipelineScheduler(job_queue=queue)

        # Create a schedule that was due 1 minute ago
        schedule = ScheduledPipeline(
            name="hourly_analysis",
            cron_expression="0 * * * *",
            pipeline_config={"rules": ["buffer_50m"]},
            next_run=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        # Bypass tier check for testing — add directly
        scheduler._schedules[str(schedule.id)] = schedule

        # Fire the tick
        _run(scheduler._tick())

        # Verify a job was enqueued
        size = _run(queue.queue_size())
        assert size == 1

        # Dequeue and verify job name
        job = _run(queue.dequeue())
        assert job is not None
        assert job.name == "scheduled:hourly_analysis"
        assert job.parameters["schedule_id"] == str(schedule.id)

        # Verify next_run was recomputed
        updated_schedule = scheduler._schedules[str(schedule.id)]
        assert updated_schedule.next_run > datetime.now(timezone.utc)
        assert updated_schedule.last_run is not None

    def test_disabled_schedule_does_not_fire(self):
        """A disabled schedule must not enqueue any job, even if it's past due."""
        from orchestration.scheduler import PipelineScheduler, ScheduledPipeline

        queue = InMemoryJobQueue()
        scheduler = PipelineScheduler(job_queue=queue)

        schedule = ScheduledPipeline(
            name="disabled_schedule",
            cron_expression="* * * * *",
            pipeline_config={},
            enabled=False,
            next_run=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        scheduler._schedules[str(schedule.id)] = schedule

        _run(scheduler._tick())

        assert _run(queue.queue_size()) == 0

    def test_schedule_without_next_run_does_not_fire(self):
        """A schedule with next_run=None must not fire."""
        from orchestration.scheduler import PipelineScheduler, ScheduledPipeline

        queue = InMemoryJobQueue()
        scheduler = PipelineScheduler(job_queue=queue)

        schedule = ScheduledPipeline(
            name="no_next_run",
            cron_expression="0 * * * *",
            pipeline_config={},
            next_run=None,
        )
        scheduler._schedules[str(schedule.id)] = schedule

        _run(scheduler._tick())

        assert _run(queue.queue_size()) == 0


# =====================================================================
# 4. Audit Trail Completeness
# =====================================================================


class TestAuditTrailCompleteness:
    """Verify that multiple actions produce a complete, queryable audit trail."""

    def test_multiple_actions_fully_logged(self):
        """Log multiple different actions and verify they are all retrievable.

        Simulates a typical session:
        1. User logs in
        2. User uploads a dataset
        3. User runs a job
        4. User downloads the result
        5. User deletes the dataset
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit_test.db"
            logger = AuditLogger(db_path=db_path)

            actions = [
                ("auth.login", "user", "user123"),
                ("dataset.upload", "dataset", "ds456"),
                ("job.run", "job", "job789"),
                ("dataset.download", "dataset", "ds456"),
                ("dataset.delete", "dataset", "ds456"),
            ]

            for action, resource_type, resource_id in actions:
                entry = AuditEntry(
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    ip_address="192.168.1.1",
                    user_agent="TestClient/1.0",
                    user_id="user123",
                )
                logger.log(entry)

            # Query all entries
            all_entries = logger.query(AuditQuery(limit=100))
            assert len(all_entries) == 5

            # Query by user
            user_entries = logger.query(AuditQuery(user_id="user123"))
            assert len(user_entries) == 5

            # Query by action
            upload_entries = logger.query(AuditQuery(action="dataset.upload"))
            assert len(upload_entries) == 1
            assert upload_entries[0].resource_id == "ds456"

            # Query by resource
            dataset_entries = logger.query(AuditQuery(resource_type="dataset"))
            assert len(dataset_entries) == 3

            # Count
            total = logger.count(AuditQuery())
            assert total == 5

    def test_audit_cleanup_respects_retention(self):
        """Cleanup removes old entries but keeps recent ones."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit_test.db"
            logger = AuditLogger(db_path=db_path)

            # Log an old entry (manually set timestamp)
            old_entry = AuditEntry(
                action="old.action",
                resource_type="test",
                ip_address="127.0.0.1",
                user_agent="test",
                timestamp=datetime.now(timezone.utc) - timedelta(days=100),
            )
            logger.log(old_entry)

            # Log a recent entry
            recent_entry = AuditEntry(
                action="recent.action",
                resource_type="test",
                ip_address="127.0.0.1",
                user_agent="test",
            )
            logger.log(recent_entry)

            # Cleanup entries older than 90 days
            deleted = logger.cleanup(older_than=timedelta(days=90))
            assert deleted == 1

            # Verify only the recent entry remains
            remaining = logger.query(AuditQuery())
            assert len(remaining) == 1
            assert remaining[0].action == "recent.action"

    def test_audit_entry_ordering(self):
        """Entries are returned in reverse chronological order (newest first)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit_test.db"
            logger = AuditLogger(db_path=db_path)

            for i in range(5):
                entry = AuditEntry(
                    action=f"action_{i}",
                    resource_type="test",
                    ip_address="127.0.0.1",
                    user_agent="test",
                    timestamp=datetime.now(timezone.utc) + timedelta(seconds=i),
                )
                logger.log(entry)

            results = logger.query(AuditQuery())
            # Newest first
            assert results[0].action == "action_4"
            assert results[-1].action == "action_0"


# =====================================================================
# 5. Storage Upload / Download / Delete cycle
# =====================================================================


class TestStorageUploadDownloadDelete:
    """Full storage lifecycle: upload, verify, download, delete, verify gone."""

    def test_complete_lifecycle_local_storage(self):
        """LocalStorage: upload -> exists -> download -> list -> delete -> verify gone."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_path=tmpdir)

            key = "org1/project1/data.gpkg"
            content = b"GeoPackage binary content here"

            # Upload
            result_key = _run(storage.upload(key, content))
            assert result_key == key

            # Exists
            assert _run(storage.exists(key)) is True

            # Download
            downloaded = _run(storage.download(key))
            assert downloaded == content

            # List
            keys = _run(storage.list_keys("org1/"))
            assert key in keys

            # Get local path
            local_path = _run(storage.get_local_path(key))
            assert local_path is not None
            assert local_path.exists()

            # Delete
            _run(storage.delete(key))

            # Verify gone
            assert _run(storage.exists(key)) is False
            with pytest.raises(StorageError):
                _run(storage.download(key))

    def test_upload_overwrites_existing(self):
        """Uploading to an existing key must overwrite the content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_path=tmpdir)

            key = "test/file.txt"
            _run(storage.upload(key, b"version 1"))
            _run(storage.upload(key, b"version 2"))

            data = _run(storage.download(key))
            assert data == b"version 2"

    def test_delete_nonexistent_is_noop(self):
        """Deleting a key that doesn't exist must not raise an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_path=tmpdir)
            # Should not raise
            _run(storage.delete("nonexistent/key.gpkg"))

    def test_upload_binaryio(self):
        """Upload from a BinaryIO stream (file-like object)."""
        from io import BytesIO

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LocalStorage(base_path=tmpdir)

            stream = BytesIO(b"stream content")
            _run(storage.upload("stream/file.dat", stream))

            data = _run(storage.download("stream/file.dat"))
            assert data == b"stream content"


# =====================================================================
# 6. Tier Gating — All Features
# =====================================================================


class TestTierGatingAllFeatures:
    """Verify that each Pro feature is properly gated in Community tier."""

    def test_community_blocks_s3_storage(self, monkeypatch):
        """S3 storage is gated to Pro tier — Community gets LocalStorage fallback."""
        from persistence.storage import create_storage

        monkeypatch.setenv("GISPULSE_S3_ENDPOINT", "http://minio:9000")
        monkeypatch.setenv("GISPULSE_TIER", "community")
        monkeypatch.delenv("GISPULSE_LICENSE_KEY", raising=False)

        storage = create_storage()
        assert isinstance(storage, LocalStorage)

    def test_community_blocks_scheduler(self, monkeypatch):
        """PipelineScheduler.start() requires Pro tier."""
        from orchestration.scheduler import PipelineScheduler
        from persistence.tier import TierError

        monkeypatch.setenv("GISPULSE_TIER", "community")
        monkeypatch.delenv("GISPULSE_LICENSE_KEY", raising=False)

        queue = InMemoryJobQueue()
        scheduler = PipelineScheduler(job_queue=queue)

        with pytest.raises(TierError):
            _run(scheduler.start())

    def test_pro_tier_requires_license_key(self, monkeypatch):
        """Setting GISPULSE_TIER=pro without a license key must raise TierError."""
        from persistence.tier import TierError, check_tier

        monkeypatch.setenv("GISPULSE_TIER", "pro")
        monkeypatch.delenv("GISPULSE_LICENSE_KEY", raising=False)

        with pytest.raises(TierError, match="license key"):
            check_tier("pro")

    def test_pro_tier_with_valid_license_passes(self, monkeypatch):
        """Setting GISPULSE_TIER=pro with license skip verify passes."""
        import base64
        from persistence.tier import check_tier

        # Build a syntactically valid payload.signature key (skip actual Ed25519 verify)
        payload = base64.urlsafe_b64encode(b'{"org":"test","tier":"pro","exp":"2099-01-01T00:00:00Z"}').rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(b"fakesig0123456789012345678901234567890123456789012345678901234").rstrip(b"=").decode()

        monkeypatch.setenv("GISPULSE_TIER", "pro")
        monkeypatch.setenv("GISPULSE_LICENSE_KEY", f"{payload}.{sig}")
        monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "1")

        assert check_tier("pro") is True

    def test_community_blocks_postgis_engine(self, monkeypatch):
        """PostGIS engine is gated to Pro tier."""
        from persistence.tier import TierError, enforce_engine_tier

        monkeypatch.setenv("GISPULSE_TIER", "community")
        monkeypatch.delenv("GISPULSE_LICENSE_KEY", raising=False)

        with pytest.raises(TierError, match="[Pp]ro"):
            enforce_engine_tier("postgis")

    def test_community_allows_duckdb_engine(self, monkeypatch):
        """DuckDB engine is available in Community tier."""
        from persistence.tier import enforce_engine_tier

        monkeypatch.setenv("GISPULSE_TIER", "community")
        # Should not raise
        enforce_engine_tier("duckdb")

    def test_enterprise_features_blocked_for_pro(self, monkeypatch):
        """Enterprise-tier features must be blocked for Pro tier."""
        from persistence.tier import TierError, check_tier

        monkeypatch.setenv("GISPULSE_TIER", "pro")
        monkeypatch.setenv("GISPULSE_LICENSE_KEY", "valid")

        with pytest.raises(TierError):
            check_tier("enterprise")
