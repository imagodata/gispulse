"""
Capabilities router for the GISPulse HTTP API.

Endpoints:
    GET  /capabilities              — list all registered capabilities with schemas
    GET  /capabilities/{name}       — detail for a single capability
    POST /capabilities/sql-preview  — preview a SQL query (LIMIT enforced)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from gispulse import get_app
from gispulse.adapters.http.routers.portal_sql_router import (
    _check_admin_key,
    _validate_sql_readonly,
)
from gispulse.adapters.http.schemas import CapabilityInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("", response_model=list[CapabilityInfo])
def list_capabilities() -> list[CapabilityInfo]:
    """Return metadata for every registered capability."""
    return [
        CapabilityInfo(
            name=item["name"],
            description=item["description"],
            json_schema=item["schema"],
        )
        for item in get_app().list_capabilities()
    ]


@router.get("/{name}", response_model=CapabilityInfo)
def get_capability(name: str) -> CapabilityInfo:
    """Return metadata for a single capability by name.

    Raises:
        404: If no capability with the given name is registered.
    """
    for item in get_app().list_capabilities():
        if item["name"] == name:
            return CapabilityInfo(
                name=item["name"],
                description=item["description"],
                json_schema=item["schema"],
            )
    raise HTTPException(
        status_code=404,
        detail=f"Capability '{name}' not found.",
    )


# ---------------------------------------------------------------------------
# SQL Preview (issue #40)
# ---------------------------------------------------------------------------


class SQLPreviewRequest(BaseModel):
    """Request body for SQL preview."""

    sql: str = Field(..., description="SQL query to preview.")
    params: dict[str, str] = Field(default_factory=dict, description="Placeholder values.")
    limit: int = Field(10, ge=1, le=100, description="Max rows to return.")


class SQLPreviewResponse(BaseModel):
    """Response for SQL preview."""

    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


@router.post("/sql-preview", response_model=SQLPreviewResponse)
def sql_preview(
    body: SQLPreviewRequest,
    request: Request,
    x_admin_key: str | None = Header(default=None),
) -> SQLPreviewResponse:
    """Execute a SQL query for preview purposes.

    The query is wrapped with a LIMIT to prevent large result sets.
    Requires the same X-Admin-Key + DDL/DCL blocklist gating as
    /portal/sql/execute — both endpoints sit on the same SQL engine and
    must share the security perimeter.
    """
    _check_admin_key(x_admin_key)

    from gispulse.core.config import settings as _cfg

    dsn = _cfg.database.postgis_dsn
    if not dsn:
        return SQLPreviewResponse(
            error="No PostGIS DSN configured. Set GISPULSE_POSTGIS_DSN.",
        )

    if not body.sql.strip():
        return SQLPreviewResponse(error="SQL query is empty.")

    _validate_sql_readonly(body.sql)

    try:
        from gispulse.adapters.http.dependencies import get_postgis_sqlalchemy_engine
        from sqlalchemy import text

        engine = get_postgis_sqlalchemy_engine(request)
        if engine is None:
            return SQLPreviewResponse(error="No PostGIS DSN configured. Set GISPULSE_POSTGIS_DSN.")

        # Render placeholders using the same safe logic as PostGISSQLCapability
        from gispulse.capabilities.postgis_sql import _safe_render

        rendered_sql = _safe_render(body.sql, body.params)

        # Wrap in a subquery with LIMIT to enforce preview safety
        preview_sql = f"SELECT * FROM ({rendered_sql}) AS _preview LIMIT {body.limit}"

        with engine.connect() as conn:
            result = conn.execute(text(preview_sql))
            columns = list(result.keys())
            rows = [dict(row._mapping) for row in result.fetchall()]

        # Serialise non-JSON-native types (geometry WKB, dates, etc.)
        clean_rows: list[dict[str, Any]] = []
        for row in rows:
            clean: dict[str, Any] = {}
            for k, v in row.items():
                if v is None:
                    clean[k] = None
                elif isinstance(v, (int, float, bool)):
                    clean[k] = v
                elif isinstance(v, bytes):
                    clean[k] = f"<binary {len(v)} bytes>"
                else:
                    clean[k] = str(v)
            clean_rows.append(clean)

        return SQLPreviewResponse(columns=columns, rows=clean_rows)

    except ImportError as exc:
        return SQLPreviewResponse(error=f"Missing dependency: {exc}")
    except ValueError as exc:
        return SQLPreviewResponse(error=f"Parameter error: {exc}")
    except Exception as exc:
        logger.warning("SQL preview failed: %s", exc, exc_info=True)
        return SQLPreviewResponse(error=str(exc))
