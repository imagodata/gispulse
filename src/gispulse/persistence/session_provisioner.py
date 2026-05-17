"""
SessionProvisioner — crée et détruit des sessions GISPulse éphémères.

P-6 #74: create_session() + provision(conn)         → backend PostGIS
P-6 #77: teardown(session_id, conn)                 → DROP ROLE + DROP SCHEMA
P-8 #91: backend="spatialite" + sélection auto      → SpatiaLiteSession

Le provisioner gère un registre en-mémoire des sessions actives (#89).
SQL généré mais pas exécuté directement — conn asyncpg passé en paramètre.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from typing import Any

from gispulse.core.logging import get_logger
from gispulse.core.models import EphemeralSession, SessionBackend, SessionStatus

log = get_logger(__name__)


# SQL templates — valeurs injectées via Python format (jamais user input)
_SQL_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS {schema}"
_SQL_CREATE_ROLE = (
    "CREATE ROLE {role} WITH LOGIN PASSWORD '{password}' "
    "VALID UNTIL '{expires}'"
)
_SQL_GRANT_SCHEMA = "GRANT USAGE, CREATE ON SCHEMA {schema} TO {role}"
_SQL_DROP_SCHEMA = "DROP SCHEMA IF EXISTS {schema} CASCADE"
_SQL_DROP_ROLE = "DROP ROLE IF EXISTS {role}"


class SessionProvisioner:
    """Crée et détruit des sessions PostGIS éphémères."""

    def __init__(self, base_dsn: str = "") -> None:
        self.base_dsn = base_dsn
        self._sessions: dict[str, EphemeralSession] = {}

    @staticmethod
    def _schema_from_uuid(uid: str) -> str:
        return f"sess_{uid.replace('-', '')}"

    def build_provision_sql(self, session: EphemeralSession) -> list[str]:
        """Retourne les instructions SQL pour provisionner une session."""
        expires = (
            session.expires_at.strftime("%Y-%m-%d %H:%M:%S")
            if session.expires_at
            else "infinity"
        )
        return [
            _SQL_CREATE_SCHEMA.format(schema=session.schema_name),
            _SQL_CREATE_ROLE.format(
                role=session.pg_role,
                password=session.pg_password,
                expires=expires,
            ),
            _SQL_GRANT_SCHEMA.format(schema=session.schema_name, role=session.pg_role),
        ]

    def build_teardown_sql(self, session: EphemeralSession) -> list[str]:
        """Retourne les instructions SQL pour détruire une session (#77)."""
        return [
            _SQL_DROP_SCHEMA.format(schema=session.schema_name),
            _SQL_DROP_ROLE.format(role=session.pg_role),
        ]

    def create_session(
        self,
        source_client: str | None = None,
        ttl_hours: int = 8,
        backend: str | SessionBackend = "auto",
        db_path: str | None = None,
    ) -> EphemeralSession:
        """Crée un objet EphemeralSession (sans I/O DB).

        Args:
            source_client: Identifiant du client ("qgis", "portal", "cli", …).
            ttl_hours:     Durée de vie de la session en heures.
            backend:       "postgis" | "spatialite" | "auto".
                           "auto" → PostGIS si base_dsn configuré, sinon SpatiaLite.
            db_path:       Chemin du fichier SpatiaLite (None → ":memory:").
                           Ignoré pour le backend PostGIS.
        """
        # Résolution automatique du backend
        if backend == "auto":
            resolved = SessionBackend.POSTGIS if self.base_dsn else SessionBackend.SPATIALITE
        else:
            resolved = SessionBackend(backend)

        uid = uuid4()
        schema_name = self._schema_from_uuid(str(uid))
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        # SpatiaLite sessions need no DB-side provisioning and are ACTIVE on
        # creation; PostGIS sessions remain PROVISIONING until provision(conn)
        # executes the CREATE SCHEMA / CREATE ROLE statements.
        initial_status = (
            SessionStatus.ACTIVE
            if resolved == SessionBackend.SPATIALITE
            else SessionStatus.PROVISIONING
        )
        session = EphemeralSession(
            id=uid,
            schema_name=schema_name,
            pg_role=schema_name,
            pg_password=secrets.token_urlsafe(24),
            status=initial_status,
            source_client=source_client,
            ttl_hours=ttl_hours,
            expires_at=expires_at,
            backend=resolved,
            db_path=db_path,
        )
        if resolved == SessionBackend.POSTGIS and self.base_dsn:
            session.pg_dsn = f"{self.base_dsn}?user={schema_name}"
        self._sessions[str(uid)] = session
        return session

    async def provision(self, session: EphemeralSession, conn: Any) -> EphemeralSession:
        """Exécute les SQL de provisioning sur la connexion asyncpg."""
        for sql in self.build_provision_sql(session):
            await conn.execute(sql)
        session.status = SessionStatus.ACTIVE
        return session

    async def provision_background(
        self,
        session_id: str,
        *,
        connect: Any | None = None,
    ) -> None:
        """Provision a PostGIS session in the background.

        Opens a short-lived asyncpg connection using ``self.base_dsn``, runs
        the provisioning SQL, and updates ``session.status``. Called as a
        FastAPI background task after ``POST /sessions``.

        No-op when:
        - session is missing or not PROVISIONING
        - backend is SpatiaLite (no DB-side provisioning needed)
        - base_dsn is empty (no DSN configured)

        On error, ``session.status`` is set to ``SessionStatus.FAILED`` so
        the client can detect a provisioning failure.

        Args:
            session_id: Target session UUID string.
            connect:    Optional async connection factory for tests. Defaults
                        to ``asyncpg.connect``.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        if session.backend != SessionBackend.POSTGIS:
            return
        if session.status != SessionStatus.PROVISIONING:
            return
        if not self.base_dsn:
            log.warning(
                "session_provision_skipped_no_dsn", session_id=session_id
            )
            session.status = SessionStatus.FAILED
            return

        if connect is None:
            try:
                import asyncpg

                connect = asyncpg.connect
            except ImportError:
                log.error(
                    "asyncpg_not_installed",
                    msg="asyncpg is required to provision PostGIS sessions. "
                    "Install with pip install gispulse[postgis].",
                )
                session.status = SessionStatus.FAILED
                return

        conn = None
        try:
            conn = await connect(self.base_dsn)
            await self.provision(session, conn)
            log.info(
                "session_provisioned",
                session_id=session_id,
                schema=session.schema_name,
            )
        except Exception as exc:
            log.error(
                "session_provision_failed",
                session_id=session_id,
                error=str(exc),
            )
            session.status = SessionStatus.FAILED
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass

    async def teardown(self, session_id: str, conn: Any) -> None:
        """Détruit le schéma et le rôle PostgreSQL (#77)."""
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session '{session_id}' not found")
        for sql in self.build_teardown_sql(session):
            await conn.execute(sql)
        session.status = SessionStatus.TORN_DOWN
        session.torn_down_at = datetime.now(timezone.utc)

    def get(self, session_id: str) -> EphemeralSession | None:
        return self._sessions.get(session_id)

    def list_active(self) -> list[EphemeralSession]:
        now = datetime.now(timezone.utc)
        return [
            s for s in self._sessions.values()
            if s.status == SessionStatus.ACTIVE
            and (s.expires_at is None or s.expires_at > now)
        ]

    def expire_stale(self) -> int:
        """Marque les sessions expirées. Retourne le nombre de sessions expirées."""
        now = datetime.now(timezone.utc)
        count = 0
        for s in self._sessions.values():
            if s.status == SessionStatus.ACTIVE and s.expires_at and s.expires_at <= now:
                s.status = SessionStatus.EXPIRED
                count += 1
        return count
