"""OSS stub auth router.

Provides minimal compatibility endpoints so the portal UI can render in
deployments without OIDC SSO (community/demo tiers). The enterprise plugin
ships a full implementation that supersedes this one when installed.

Endpoints:
    GET /auth/providers — always returns ``[]`` so the UI hides the SSO button
    GET /auth/me        — always returns 401 (no session in OSS mode)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/providers", summary="List configured SSO providers (OSS stub)")
async def list_providers() -> list[dict[str, str]]:
    return []


@router.get("/me", summary="Current user info (OSS stub — always 401)")
async def get_me() -> None:
    raise HTTPException(status_code=401, detail="Not authenticated")
