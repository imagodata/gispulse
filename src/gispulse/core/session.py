"""Session models for GISPulse ephemeral sessions.

Supports both PostGIS (Phase 3, multi-client) and SpatiaLite
(Phase 2, mono-client) backends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from gispulse.core.enums import SessionBackend, SessionStatus


@dataclass
class EphemeralSession:
    """Session GISPulse éphémère — PostGIS (#74) ou SpatiaLite (#91).

    Créée par SessionProvisioner. Le schéma/rôle PostGIS sont détruits lors du
    teardown (#77) ; une session SpatiaLite ferme simplement son fichier .db.
    """
    id: UUID = field(default_factory=uuid4)
    schema_name: str = ""
    pg_role: str = ""
    pg_password: str = ""
    pg_dsn: str | None = None
    status: SessionStatus = SessionStatus.PROVISIONING
    source_client: str | None = None
    ttl_hours: int = 8
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    torn_down_at: datetime | None = None
    backend: SessionBackend = SessionBackend.POSTGIS
    db_path: str | None = None
