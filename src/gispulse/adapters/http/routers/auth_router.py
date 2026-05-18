"""OSS stub auth router.

Provides minimal compatibility endpoints so the portal UI can render in
deployments without OIDC SSO (community/demo tiers). The enterprise plugin
ships a full implementation that supersedes this one when installed.

Endpoints:
    GET /auth/providers — always returns [] so the UI hides the SSO button
    GET /auth/me        — returns 200 with null body for anonymous sessions.
                          Returning 401 would work too (the portal handles it),
                          but browsers log 4xx network responses to the
                          DevTools console regardless of how the JS client
                          handles them. 200 + null is silent and equally
                          unambiguous (portal authStore treats null as 'not
                          logged in').
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/providers", summary="List configured SSO providers (OSS stub)")
async def list_providers() -> list[dict[str, str]]:
    return []


@router.get("/me", summary="Current user info (OSS stub — anonymous)")
async def get_me() -> None:
    return None
