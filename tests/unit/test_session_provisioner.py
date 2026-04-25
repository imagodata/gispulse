"""Tests for SessionProvisioner (P-6 #74, #77, #89)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock
from datetime import datetime, timezone

from core.models import SessionBackend, SessionStatus
from persistence.session_provisioner import SessionProvisioner


class TestSessionCreation:
    def test_create_session_returns_ephemeral_session(self):
        # Default backend (no DSN) = SpatiaLite → starts ACTIVE, needs no DB provisioning.
        p = SessionProvisioner()
        s = p.create_session()
        assert s.schema_name.startswith("sess_")
        assert len(s.schema_name) == len("sess_") + 32  # uuid hex
        assert s.pg_role == s.schema_name
        assert len(s.pg_password) > 16
        assert s.status == SessionStatus.ACTIVE
        assert s.expires_at is not None

    def test_create_postgis_session_stays_provisioning(self):
        # PostGIS backend requires an external provision(conn) call to reach ACTIVE.
        p = SessionProvisioner(base_dsn="postgresql://user:pass@host:5432/db")
        s = p.create_session(backend="postgis")
        assert s.status == SessionStatus.PROVISIONING
        assert s.pg_dsn is not None and "user=" in s.pg_dsn

    def test_create_session_with_client(self):
        p = SessionProvisioner()
        s = p.create_session(source_client="qgis", ttl_hours=4)
        assert s.source_client == "qgis"
        assert s.ttl_hours == 4

    def test_sessions_have_unique_ids(self):
        p = SessionProvisioner()
        s1 = p.create_session()
        s2 = p.create_session()
        assert s1.id != s2.id
        assert s1.schema_name != s2.schema_name

    def test_get_session(self):
        p = SessionProvisioner()
        s = p.create_session()
        found = p.get(str(s.id))
        assert found is not None
        assert found.id == s.id

    def test_get_unknown_session_returns_none(self):
        p = SessionProvisioner()
        assert p.get("nonexistent") is None

    def test_list_active_only_returns_active(self):
        from core.models import SessionStatus
        p = SessionProvisioner()
        s1 = p.create_session()
        s1.status = SessionStatus.ACTIVE
        s2 = p.create_session()
        s2.status = SessionStatus.TORN_DOWN
        active = p.list_active()
        assert len(active) == 1
        assert active[0].id == s1.id


class TestProvisionSQL:
    def test_build_provision_sql_count(self):
        p = SessionProvisioner()
        s = p.create_session()
        sql = p.build_provision_sql(s)
        assert len(sql) == 3

    def test_build_provision_sql_schema(self):
        p = SessionProvisioner()
        s = p.create_session()
        sql = p.build_provision_sql(s)
        assert s.schema_name in sql[0]  # CREATE SCHEMA
        assert s.pg_role in sql[1]      # CREATE ROLE
        assert s.pg_password in sql[1]
        assert s.schema_name in sql[2]  # GRANT

    def test_build_teardown_sql_count(self):
        p = SessionProvisioner()
        s = p.create_session()
        sql = p.build_teardown_sql(s)
        assert len(sql) == 2

    def test_build_teardown_sql_contains_schema(self):
        p = SessionProvisioner()
        s = p.create_session()
        sql = p.build_teardown_sql(s)
        assert s.schema_name in sql[0]  # DROP SCHEMA
        assert s.pg_role in sql[1]       # DROP ROLE


class TestProvisionAsync:
    @pytest.mark.asyncio
    async def test_provision_marks_active(self):
        p = SessionProvisioner()
        s = p.create_session()
        mock_conn = AsyncMock()
        result = await p.provision(s, mock_conn)
        assert result.status == SessionStatus.ACTIVE
        assert mock_conn.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_teardown_marks_torn_down(self):
        p = SessionProvisioner()
        s = p.create_session()
        s.status = SessionStatus.ACTIVE
        mock_conn = AsyncMock()
        await p.teardown(str(s.id), mock_conn)
        assert s.status == SessionStatus.TORN_DOWN
        assert s.torn_down_at is not None
        assert mock_conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_teardown_unknown_raises_key_error(self):
        p = SessionProvisioner()
        mock_conn = AsyncMock()
        with pytest.raises(KeyError, match="not found"):
            await p.teardown("nonexistent", mock_conn)


class TestExpireStale:
    def test_expire_stale_marks_expired_sessions(self):
        from datetime import timedelta
        p = SessionProvisioner()
        s = p.create_session()
        s.status = SessionStatus.ACTIVE
        # Forcer l'expiration
        s.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        expired_count = p.expire_stale()
        assert expired_count == 1
        assert s.status == SessionStatus.EXPIRED

    def test_expire_stale_leaves_fresh_sessions(self):
        p = SessionProvisioner()
        s = p.create_session()
        s.status = SessionStatus.ACTIVE
        expired_count = p.expire_stale()
        assert expired_count == 0
        assert s.status == SessionStatus.ACTIVE


# ---------------------------------------------------------------------------
# provision_background — async PostGIS session finalisation
# ---------------------------------------------------------------------------


class FakeConn:
    """asyncpg connection stub for provision_background tests."""

    def __init__(self, *, fail_on_execute: bool = False):
        self.executed: list[str] = []
        self.closed = False
        self._fail = fail_on_execute

    async def execute(self, sql: str) -> None:
        if self._fail:
            raise RuntimeError("simulated DB error")
        self.executed.append(sql)

    async def close(self) -> None:
        self.closed = True


class TestProvisionBackgroundPostGIS:
    @pytest.mark.asyncio
    async def test_success_transitions_to_active(self):
        p = SessionProvisioner(base_dsn="postgresql://u:p@h:5432/db")
        s = p.create_session(backend="postgis")
        assert s.status == SessionStatus.PROVISIONING

        conn = FakeConn()

        async def fake_connect(dsn: str) -> FakeConn:
            assert dsn == "postgresql://u:p@h:5432/db"
            return conn

        await p.provision_background(str(s.id), connect=fake_connect)

        assert s.status == SessionStatus.ACTIVE
        # All three provisioning SQL statements were executed
        assert len(conn.executed) == 3
        assert any("CREATE SCHEMA" in sql for sql in conn.executed)
        assert any("CREATE ROLE" in sql for sql in conn.executed)
        assert any("GRANT" in sql for sql in conn.executed)
        assert conn.closed is True

    @pytest.mark.asyncio
    async def test_connection_failure_marks_session_failed(self):
        p = SessionProvisioner(base_dsn="postgresql://u:p@h/db")
        s = p.create_session(backend="postgis")

        async def failing_connect(dsn: str):
            raise ConnectionError("host unreachable")

        await p.provision_background(str(s.id), connect=failing_connect)
        assert s.status == SessionStatus.FAILED

    @pytest.mark.asyncio
    async def test_execute_failure_marks_session_failed(self):
        p = SessionProvisioner(base_dsn="postgresql://u:p@h/db")
        s = p.create_session(backend="postgis")

        conn = FakeConn(fail_on_execute=True)

        async def fake_connect(dsn: str):
            return conn

        await p.provision_background(str(s.id), connect=fake_connect)
        assert s.status == SessionStatus.FAILED
        assert conn.closed is True  # still closed on failure


class TestProvisionBackgroundNoOps:
    @pytest.mark.asyncio
    async def test_unknown_session_is_noop(self):
        p = SessionProvisioner(base_dsn="postgresql://u:p@h/db")
        called = []

        async def fake_connect(dsn: str):
            called.append(dsn)
            return FakeConn()

        await p.provision_background("does-not-exist", connect=fake_connect)
        assert called == []

    @pytest.mark.asyncio
    async def test_spatialite_backend_is_noop(self):
        p = SessionProvisioner()  # no DSN → SpatiaLite default
        s = p.create_session()
        assert s.backend == SessionBackend.SPATIALITE
        assert s.status == SessionStatus.ACTIVE

        called = []

        async def fake_connect(dsn: str):
            called.append(dsn)
            return FakeConn()

        await p.provision_background(str(s.id), connect=fake_connect)
        # No connection attempted, session untouched
        assert called == []
        assert s.status == SessionStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_already_active_postgis_session_is_noop(self):
        p = SessionProvisioner(base_dsn="postgresql://u:p@h/db")
        s = p.create_session(backend="postgis")
        s.status = SessionStatus.ACTIVE  # already provisioned

        called = []

        async def fake_connect(dsn: str):
            called.append(dsn)
            return FakeConn()

        await p.provision_background(str(s.id), connect=fake_connect)
        assert called == []
        assert s.status == SessionStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_empty_dsn_marks_failed(self):
        """PostGIS session created without a DSN can never provision — mark
        it FAILED so the client sees the error rather than polling forever."""
        p = SessionProvisioner(base_dsn="")
        # Force PostGIS backend despite missing DSN
        s = p.create_session(backend="postgis")
        s.backend = SessionBackend.POSTGIS  # in case resolution fell back
        s.status = SessionStatus.PROVISIONING

        await p.provision_background(str(s.id))
        assert s.status == SessionStatus.FAILED
