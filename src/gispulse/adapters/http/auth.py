"""
API key authentication and RBAC for the GISPulse HTTP adapter.

Three authentication modes (checked in priority order):

1. **Session cookie** (OIDC SSO — Enterprise tier): A ``gispulse_session``
   httponly cookie containing a locally-signed JWT.  Set after OIDC login via
   ``/auth/callback``.
2. **API key header** (``X-API-Key`` or ``Bearer``): Looked up in the
   ``api_keys`` table when RBAC is enabled, or validated against
   ``GISPULSE_API_KEYS`` env var in legacy mode.
3. **No auth** (dev mode): When no API keys and no users exist, all requests
   pass through.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from gispulse.persistence.auth_models import VALID_SCOPES, User, role_gte
from gispulse.persistence.auth_repository import AuthRepository, hash_api_key

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_api_key_validator(api_keys: set[str] | None = None):
    """Factory that returns an async dependency validating the ``X-API-Key`` header.

    Args:
        api_keys: Set of valid API keys.  When ``None`` or empty the validator
            accepts every request (development mode).

    Returns:
        An async callable suitable for use with ``fastapi.Depends`` or
        ``fastapi.Security``.
    """

    async def validate(key: str | None = Security(API_KEY_HEADER)) -> str | None:
        if not api_keys:  # Auth disabled -- dev mode
            return None
        if not key:
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key",
            )
        # Timing-safe comparison: iterate all keys to prevent timing leaks
        if not any(hmac.compare_digest(key, valid_key) for valid_key in api_keys):
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key",
            )
        return key

    return validate


# ----------------------------------------------------------------------
# RBAC dependencies
# ----------------------------------------------------------------------


def _get_auth_repo(request: Request) -> AuthRepository | None:
    """Return the AuthRepository from app state, or None if not configured."""
    return getattr(request.app.state, "auth_repo", None)


async def _resolve_user_from_session(request: Request) -> User | None:
    """Attempt to resolve a user from a session cookie via plugin AuthProviders.

    Iterates ``ExtensionHub.auth_providers`` (e.g. the ``oidc`` provider shipped
    by ``gispulse-enterprise``); the first provider that returns claims wins.
    Returns ``None`` when no provider is registered, none accept the request,
    no auth repository is configured, or the resolved user is inactive.

    A provider raising during ``authenticate`` is treated as a soft failure
    so API key auth can still take over.
    """
    from gispulse.core.plugin_hub import ExtensionHub

    providers = ExtensionHub.get().auth_providers
    if not providers:
        return None

    claims: dict | None = None
    for provider in providers.values():
        try:
            claims = await provider.authenticate(request)
        except Exception:
            claims = None
        if claims is not None:
            break

    if claims is None:
        return None

    auth_repo = _get_auth_repo(request)
    if auth_repo is None:
        return None

    user = auth_repo.get_user(claims["sub"])
    if user is None or not user.is_active:
        return None

    request.state.user = user
    # Session users get full scopes based on their role.
    # Admin role implies ALL scopes — not just "admin".
    role_scopes = {
        "viewer": ["read"],
        "editor": ["read", "write"],
        "admin": sorted(VALID_SCOPES),
    }
    request.state.api_key_scopes = role_scopes.get(user.role, ["read"])
    return user


async def get_current_user(
    request: Request,
    key: str | None = Security(API_KEY_HEADER),
) -> User | None:
    """Resolve the current user via session cookie or API key.

    Authentication priority:
    1. ``gispulse_session`` cookie (OIDC SSO)
    2. ``X-API-Key`` / ``Bearer`` header
    3. No auth (dev/legacy mode)

    When RBAC is not configured (no AuthRepository), returns ``None``
    (dev/legacy mode — no auth enforced).

    When RBAC is enabled (AuthRepository attached), auth is **always**
    enforced — even if no users exist yet.  The only unauthenticated
    endpoint is ``POST /admin/bootstrap``.
    """
    # Priority 1: OIDC session cookie
    session_user = await _resolve_user_from_session(request)
    if session_user is not None:
        return session_user

    # Priority 2: API key
    auth_repo = _get_auth_repo(request)

    if auth_repo is None:
        # No RBAC configured — dev/legacy mode, no auth enforced
        return None

    # RBAC is explicitly enabled (auth_repo exists).
    # Even if no users exist yet, we enforce auth.  The only
    # unauthenticated endpoint is POST /admin/bootstrap which does not
    # depend on get_current_user.
    if not key:
        raise HTTPException(status_code=401, detail="API key required")

    if key.startswith("Bearer "):
        key = key[7:]

    key_hash = hash_api_key(key)
    api_key = auth_repo.get_api_key_by_hash(key_hash)

    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="API key expired")

    user = auth_repo.get_user(api_key.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User account disabled")

    request.state.user = user
    request.state.api_key_scopes = api_key.scopes
    return user


def require_scope(scope: str):
    """FastAPI dependency that verifies the current API key has *scope*.

    In legacy mode (no RBAC / no AuthRepository), the check is a no-op.
    When RBAC is active, a missing user always results in 401.
    """

    async def _check(
        request: Request,
        user: User | None = Depends(get_current_user),
    ) -> User | None:
        if user is None:
            # Defense-in-depth: if RBAC is active, never allow None through
            if _get_auth_repo(request) is not None:
                raise HTTPException(status_code=401, detail="Authentication required")
            return None
        scopes: list[str] = getattr(request.state, "api_key_scopes", [])
        if "admin" in scopes:
            return user
        if scope not in scopes:
            raise HTTPException(
                status_code=403,
                detail=f"Missing required scope: {scope}",
            )
        return user

    return _check


def require_role(min_role: str):
    """FastAPI dependency that verifies the user has at least *min_role*.

    In legacy mode (no RBAC / no AuthRepository), the check is a no-op.
    When RBAC is active, a missing user always results in 401.
    """

    async def _check(
        request: Request,
        user: User | None = Depends(get_current_user),
    ) -> User | None:
        if user is None:
            # Defense-in-depth: if RBAC is active, never allow None through
            if _get_auth_repo(request) is not None:
                raise HTTPException(status_code=401, detail="Authentication required")
            return None
        if not role_gte(user.role, min_role):
            raise HTTPException(
                status_code=403,
                detail=f"Requires role: {min_role} (you have: {user.role})",
            )
        return user

    return _check
