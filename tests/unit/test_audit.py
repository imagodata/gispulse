"""
Unit tests for the audit logging system (Sprint S5).

Tests cover:
- AuditLogger: CRUD, query filters, retention cleanup
- AuditMiddleware: route mapping, shouldLog logic, IP extraction
- Tier gating: audit requires Pro tier
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from gispulse.persistence.audit import AuditEntry, AuditLogger, AuditQuery


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "test_audit.db"


@pytest.fixture
def audit_logger(tmp_db):
    return AuditLogger(db_path=tmp_db)


def _make_entry(**overrides) -> AuditEntry:
    defaults = dict(
        action="dataset.upload",
        resource_type="dataset",
        ip_address="127.0.0.1",
        user_agent="test-agent/1.0",
        user_id="user-1",
        resource_id="ds-123",
        status_code=201,
    )
    defaults.update(overrides)
    return AuditEntry(**defaults)


# ---------------------------------------------------------------------------
# AuditLogger tests
# ---------------------------------------------------------------------------


class TestAuditLogger:
    def test_log_and_query(self, audit_logger):
        entry = _make_entry()
        audit_logger.log(entry)

        results = audit_logger.query(AuditQuery())
        assert len(results) == 1
        assert results[0].action == "dataset.upload"
        assert results[0].user_id == "user-1"
        assert results[0].ip_address == "127.0.0.1"
        assert results[0].status_code == 201

    def test_query_filter_by_user(self, audit_logger):
        audit_logger.log(_make_entry(user_id="alice"))
        audit_logger.log(_make_entry(user_id="bob"))

        results = audit_logger.query(AuditQuery(user_id="alice"))
        assert len(results) == 1
        assert results[0].user_id == "alice"

    def test_query_filter_by_action(self, audit_logger):
        audit_logger.log(_make_entry(action="dataset.upload"))
        audit_logger.log(_make_entry(action="rule.create"))

        results = audit_logger.query(AuditQuery(action="rule.create"))
        assert len(results) == 1
        assert results[0].action == "rule.create"

    def test_query_filter_by_resource_type(self, audit_logger):
        audit_logger.log(_make_entry(resource_type="dataset"))
        audit_logger.log(_make_entry(resource_type="rule"))

        results = audit_logger.query(AuditQuery(resource_type="rule"))
        assert len(results) == 1

    def test_query_filter_by_date_range(self, audit_logger):
        old = _make_entry()
        old.timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        audit_logger.log(old)

        recent = _make_entry()
        recent.timestamp = datetime(2026, 4, 1, tzinfo=timezone.utc)
        audit_logger.log(recent)

        results = audit_logger.query(AuditQuery(
            date_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ))
        assert len(results) == 1

    def test_count(self, audit_logger):
        for i in range(5):
            audit_logger.log(_make_entry(user_id=f"user-{i}"))

        assert audit_logger.count(AuditQuery()) == 5
        assert audit_logger.count(AuditQuery(user_id="user-0")) == 1

    def test_pagination(self, audit_logger):
        for i in range(10):
            audit_logger.log(_make_entry(action=f"action.{i}"))

        page1 = audit_logger.query(AuditQuery(limit=3, offset=0))
        page2 = audit_logger.query(AuditQuery(limit=3, offset=3))
        assert len(page1) == 3
        assert len(page2) == 3
        # No overlap
        ids1 = {e.id for e in page1}
        ids2 = {e.id for e in page2}
        assert ids1.isdisjoint(ids2)

    def test_cleanup_old_entries(self, audit_logger):
        old = _make_entry()
        old.timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc)
        audit_logger.log(old)

        recent = _make_entry()
        audit_logger.log(recent)

        deleted = audit_logger.cleanup(older_than=timedelta(days=365))
        assert deleted == 1
        assert audit_logger.count(AuditQuery()) == 1

    def test_cleanup_no_old_entries(self, audit_logger):
        audit_logger.log(_make_entry())
        deleted = audit_logger.cleanup(older_than=timedelta(days=365))
        assert deleted == 0

    def test_wal_mode(self, tmp_db, audit_logger):
        """Verify the database is opened in WAL mode."""
        conn = sqlite3.connect(str(tmp_db))
        result = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()
        assert result[0] == "wal"

    def test_details_json_roundtrip(self, audit_logger):
        entry = _make_entry(details={"size_mb": 42.5, "format": "gpkg"})
        audit_logger.log(entry)

        results = audit_logger.query(AuditQuery())
        assert results[0].details == {"size_mb": 42.5, "format": "gpkg"}

    def test_null_user_id(self, audit_logger):
        entry = _make_entry(user_id=None)
        audit_logger.log(entry)

        results = audit_logger.query(AuditQuery())
        assert results[0].user_id is None


# ---------------------------------------------------------------------------
# Middleware helper tests
# ---------------------------------------------------------------------------


class TestMiddlewareHelpers:
    def test_normalise_path_uuid(self):
        from gispulse.adapters.http.middleware.audit_middleware import _normalise_path

        path = "/datasets/550e8400-e29b-41d4-a716-446655440000"
        assert _normalise_path(path) == "/datasets/{id}"

    def test_normalise_path_no_uuid(self):
        from gispulse.adapters.http.middleware.audit_middleware import _normalise_path

        assert _normalise_path("/health") == "/health"

    def test_resolve_action_known_route(self):
        from gispulse.adapters.http.middleware.audit_middleware import _resolve_action

        result = _resolve_action("POST", "/datasets/upload")
        assert result == ("dataset.upload", "dataset")

    def test_resolve_action_with_uuid(self):
        from gispulse.adapters.http.middleware.audit_middleware import _resolve_action

        result = _resolve_action(
            "DELETE",
            "/datasets/550e8400-e29b-41d4-a716-446655440000",
        )
        assert result == ("dataset.delete", "dataset")

    def test_resolve_action_unknown_route(self):
        from gispulse.adapters.http.middleware.audit_middleware import _resolve_action

        result = _resolve_action("POST", "/unknown/thing")
        assert result is None

    def test_should_log_post(self):
        from gispulse.adapters.http.middleware.audit_middleware import _should_log

        assert _should_log("POST", "/datasets/upload") is True
        assert _should_log("DELETE", "/rules/123") is True
        assert _should_log("PUT", "/rules/123") is True
        assert _should_log("PATCH", "/admin/users/123") is True

    def test_should_not_log_get(self):
        from gispulse.adapters.http.middleware.audit_middleware import _should_log

        assert _should_log("GET", "/datasets") is False
        assert _should_log("GET", "/health") is False

    def test_should_log_admin_get(self):
        from gispulse.adapters.http.middleware.audit_middleware import _should_log

        assert _should_log("GET", "/admin/users") is True
        assert _should_log("GET", "/admin/audit") is True

    def test_extract_resource_id(self):
        from gispulse.adapters.http.middleware.audit_middleware import _extract_resource_id

        rid = _extract_resource_id(
            "/datasets/550e8400-e29b-41d4-a716-446655440000"
        )
        assert rid == "550e8400-e29b-41d4-a716-446655440000"

    def test_extract_resource_id_none(self):
        from gispulse.adapters.http.middleware.audit_middleware import _extract_resource_id

        assert _extract_resource_id("/health") is None


# ---------------------------------------------------------------------------
# Tier gating test
# ---------------------------------------------------------------------------


class TestTierGating:
    def test_audit_requires_pro(self):
        """check_tier('pro') should raise for community tier."""
        from gispulse.persistence.tier import TierError, check_tier

        with patch.dict("os.environ", {"GISPULSE_TIER": "community"}, clear=False):
            with pytest.raises(TierError, match="Pro"):
                check_tier("pro")

    def test_audit_passes_pro(self):
        from gispulse.persistence.tier import check_tier

        with patch.dict(
            "os.environ",
            {"GISPULSE_TIER": "pro", "GISPULSE_LICENCE_SKIP_VERIFY": "true", "GISPULSE_LICENSE_KEY": "eyJvcmciOiAidGVzdCIsICJ0aWVyIjogInBybyIsICJleHAiOiAiMjAzMC0wMS0wMVQwMDowMDowMFoifQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
            clear=False,
        ):
            assert check_tier("pro") is True


# =====================================================================
# #314: Connection pooling — benchmark
# =====================================================================


class TestAuditConnectionPooling314:
    """Verify pooled connection performance and close()."""

    def test_1000_writes_under_5_seconds(self, tmp_path):
        """1000 audit writes must complete in < 5s with pooled connection."""
        import time
        logger = AuditLogger(db_path=tmp_path / "bench.db")

        start = time.monotonic()
        for i in range(1000):
            logger.log(AuditEntry(
                action=f"bench.write.{i}",
                resource_type="dataset",
                ip_address="127.0.0.1",
                user_agent="benchmark",
            ))
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"1000 writes took {elapsed:.2f}s (> 5s)"
        assert logger.count(AuditQuery()) == 1000
        logger.close()

    def test_close_releases_connection(self, tmp_path):
        """close() must release the SQLite connection."""
        logger = AuditLogger(db_path=tmp_path / "close.db")
        logger.log(AuditEntry(
            action="test", resource_type="t",
            ip_address="0", user_agent="t",
        ))
        logger.close()
        # After close, the connection is released
        # Re-creating should work fine
        logger2 = AuditLogger(db_path=tmp_path / "close.db")
        assert logger2.count(AuditQuery()) == 1
        logger2.close()

    def test_single_connection_reused(self, tmp_path):
        """AuditLogger must use a single persistent connection."""
        logger = AuditLogger(db_path=tmp_path / "pool.db")
        assert hasattr(logger, "_conn")
        conn_id = id(logger._conn)

        # After multiple operations, same connection object
        logger.log(AuditEntry(
            action="a", resource_type="t",
            ip_address="0", user_agent="t",
        ))
        logger.log(AuditEntry(
            action="b", resource_type="t",
            ip_address="0", user_agent="t",
        ))
        assert id(logger._conn) == conn_id
        logger.close()
