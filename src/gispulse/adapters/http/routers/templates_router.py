"""Templates router for the GISPulse HTTP API.

Endpoints:
    GET /templates          — list the built-in pipeline templates
    GET /templates/{name}   — the raw JSON of one template

Closes the "templates have no HTTP endpoint" gap (Chantier C of the
v1.8.0 "Foundations" refonte): the 23 bundled pipeline templates were
reachable from the CLI (``gispulse template``) and the MCP server but not
over HTTP. Like every v1.8.0 surface, this router is a thin adapter — it
delegates to :class:`gispulse.app.GISPulseApp`, holding no logic of its
own.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from gispulse import get_app

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("")
def list_templates() -> list[dict[str, Any]]:
    """Return the built-in pipeline templates.

    Each entry carries ``name`` (the stem used to fetch it), ``title``
    and ``description``.
    """
    return get_app().list_templates()


@router.get("/{name}")
def get_template(name: str) -> dict[str, Any]:
    """Return the raw JSON of a single built-in template.

    Raises:
        404: If no template matches ``name``.
    """
    try:
        return get_app().get_template(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
