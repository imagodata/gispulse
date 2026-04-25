"""
Sessions router pour l'API HTTP GISPulse.

Endpoints:
    POST   /sessions              — crée une session éphémère PostGIS
    GET    /sessions              — liste les sessions actives
    GET    /sessions/{id}         — détail d'une session
    DELETE /sessions/{id}         — teardown (DROP ROLE + DROP SCHEMA)
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from gispulse.adapters.esb.pg_notify import session_channel
from gispulse.adapters.http.dependencies import get_session_provisioner
from gispulse.adapters.http.rate_limit import limiter
from gispulse.adapters.http.schemas import SessionCreate, SessionResponse
from core.models import SessionBackend
from persistence.session_provisioner import SessionProvisioner

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _to_response(session) -> SessionResponse:
    return SessionResponse(
        id=session.id,
        schema_name=session.schema_name,
        pg_role=session.pg_role,
        pg_password=session.pg_password,
        pg_dsn=session.pg_dsn,
        pg_notify_channel=session_channel(session.schema_name),
        status=session.status,
        source_client=session.source_client,
        ttl_hours=session.ttl_hours,
        expires_at=session.expires_at,
        created_at=session.created_at,
    )


@router.post("", response_model=SessionResponse, status_code=201)
@limiter.limit("10/minute")
def create_session(
    request: Request,
    payload: SessionCreate,
    background_tasks: BackgroundTasks,
    provisioner: SessionProvisioner = Depends(get_session_provisioner),
) -> SessionResponse:
    """Crée une session éphémère et retourne les credentials.

    For PostGIS sessions, schema+role provisioning runs in the background
    after the HTTP response is sent. Clients poll ``GET /sessions/{id}``
    and wait for ``status == 'active'`` before connecting.
    """
    session = provisioner.create_session(
        source_client=payload.source_client,
        ttl_hours=payload.ttl_hours,
    )
    if session.backend == SessionBackend.POSTGIS:
        background_tasks.add_task(
            provisioner.provision_background, str(session.id)
        )
    return _to_response(session)


@router.get("", response_model=list[SessionResponse])
def list_sessions(
    provisioner: SessionProvisioner = Depends(get_session_provisioner),
) -> list[SessionResponse]:
    """Liste les sessions actives."""
    return [_to_response(s) for s in provisioner.list_active()]


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: UUID,
    provisioner: SessionProvisioner = Depends(get_session_provisioner),
) -> SessionResponse:
    """Retourne le détail d'une session."""
    session = provisioner.get(str(session_id))
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return _to_response(session)


@router.delete("/{session_id}", status_code=204)
@limiter.limit("20/minute")
def delete_session(
    session_id: UUID,
    request: Request,
    provisioner: SessionProvisioner = Depends(get_session_provisioner),
) -> None:
    """Supprime une session (marque comme torn_down, sans connexion DB disponible).

    Note: this only updates the in-memory registry.  The actual PostgreSQL
    schema/role teardown requires an asyncpg connection and should be done
    via ``provisioner.teardown(session_id, conn)`` when a DB connection is
    available.
    """
    from datetime import datetime, timezone
    from core.models import SessionStatus

    session = provisioner.get(str(session_id))
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    session.status = SessionStatus.TORN_DOWN
    session.torn_down_at = datetime.now(timezone.utc)
    return None
