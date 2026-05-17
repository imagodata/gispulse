"""
Audit middleware for GISPulse FastAPI application.

Intercepts mutating requests (POST, PUT, PATCH, DELETE) and logs them
to the audit trail after the response is sent.  GET requests are only
logged for ``/admin/*`` paths.

The middleware extracts user identity from ``request.state.user`` (set by
the RBAC auth layer) when available.

Sensitive data (request bodies, auth headers) is never logged.
"""

from __future__ import annotations

import re
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from gispulse.persistence.audit import AuditEntry, AuditLogger

# ---------------------------------------------------------------------------
# Route -> action mapping
# ---------------------------------------------------------------------------

# Static route mappings (method, path_pattern) -> action
# Path parameters are normalised to {param} before lookup.
ROUTE_ACTIONS: dict[tuple[str, str], tuple[str, str]] = {
    # Datasets
    ("POST", "/datasets/upload"): ("dataset.upload", "dataset"),
    ("DELETE", "/datasets/{id}"): ("dataset.delete", "dataset"),
    # Jobs
    ("POST", "/jobs"): ("job.run", "job"),
    ("POST", "/jobs/{id}/cancel"): ("job.cancel", "job"),
    # Rules
    ("POST", "/rules"): ("rule.create", "rule"),
    ("PUT", "/rules/{id}"): ("rule.update", "rule"),
    ("DELETE", "/rules/{id}"): ("rule.delete", "rule"),
    # Triggers
    ("POST", "/triggers"): ("trigger.create", "trigger"),
    ("PUT", "/triggers/{id}"): ("trigger.update", "trigger"),
    ("DELETE", "/triggers/{id}"): ("trigger.delete", "trigger"),
    # Scenarios
    ("POST", "/scenarios"): ("scenario.create", "scenario"),
    ("PUT", "/scenarios/{id}"): ("scenario.update", "scenario"),
    ("DELETE", "/scenarios/{id}"): ("scenario.delete", "scenario"),
    # Projects
    ("POST", "/projects"): ("project.create", "project"),
    ("PUT", "/projects/{id}"): ("project.update", "project"),
    ("DELETE", "/projects/{id}"): ("project.delete", "project"),
    # Schedules
    ("POST", "/schedules"): ("schedule.create", "schedule"),
    ("PUT", "/schedules/{id}"): ("schedule.update", "schedule"),
    ("DELETE", "/schedules/{id}"): ("schedule.delete", "schedule"),
    # Admin - Users
    ("POST", "/admin/users"): ("user.create", "user"),
    ("PATCH", "/admin/users/{id}"): ("user.update", "user"),
    ("DELETE", "/admin/users/{id}"): ("user.delete", "user"),
    ("POST", "/admin/bootstrap"): ("admin.bootstrap", "user"),
    # Admin - API keys
    ("POST", "/admin/api-keys"): ("api_key.create", "api_key"),
    ("DELETE", "/admin/api-keys/{id}"): ("api_key.revoke", "api_key"),
    # Admin - Orgs
    ("POST", "/admin/orgs"): ("org.create", "org"),
    # Sessions
    ("POST", "/sessions"): ("session.create", "session"),
    ("DELETE", "/sessions/{id}"): ("session.delete", "session"),
    # Filter
    ("POST", "/filter/apply"): ("filter.apply", "filter"),
}

# Regex to normalise path segments that look like UUIDs or IDs
_ID_SEGMENT_RE = re.compile(
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
# Also catch simple numeric or short string IDs in known positions
_GENERIC_ID_RE = re.compile(r"/([^/]{8,})")


def _normalise_path(path: str) -> str:
    """Replace UUID-like segments with ``{id}`` for route matching."""
    return _ID_SEGMENT_RE.sub("/{id}", path)


def _resolve_action(method: str, path: str) -> tuple[str, str] | None:
    """Map (method, path) to (action_name, resource_type) or None."""
    normalised = _normalise_path(path)
    key = (method.upper(), normalised)
    if key in ROUTE_ACTIONS:
        return ROUTE_ACTIONS[key]
    return None


def _extract_resource_id(path: str) -> str | None:
    """Extract the first UUID-like segment from *path*."""
    match = _ID_SEGMENT_RE.search(path)
    if match:
        return match.group(0).lstrip("/")
    return None


def _should_log(method: str, path: str) -> bool:
    """Determine if a request should be audited.

    - All mutating methods (POST, PUT, PATCH, DELETE)
    - GET only for /admin/* paths
    """
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return True
    if method == "GET" and path.startswith("/admin/"):
        return True
    return False


def _get_client_ip(request: Request) -> str:
    """Extract client IP from the direct connection.

    X-Forwarded-For is NOT trusted because we cannot verify the
    upstream proxy chain. Always prefer the direct socket peer address
    to prevent audit trail spoofing via forged headers.
    """
    if request.client:
        return request.client.host
    return "unknown"


# ---------------------------------------------------------------------------
# Middleware class
# ---------------------------------------------------------------------------


class AuditMiddleware(BaseHTTPMiddleware):
    """Log mutating API requests to the audit trail.

    Logs are written AFTER the response is produced, so the status code
    and any resource ID from the response are available.
    """

    def __init__(self, app, audit_logger: AuditLogger) -> None:
        super().__init__(app)
        self._audit_logger = audit_logger

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        method = request.method
        path = request.url.path

        if not _should_log(method, path):
            return await call_next(request)

        # Resolve action
        resolved = _resolve_action(method, path)

        # If we can't map the route, generate a generic action
        if resolved is None:
            # Still log unknown mutating requests with a generic action
            normalised = _normalise_path(path)
            parts = normalised.strip("/").split("/")
            resource_type = parts[0] if parts else "unknown"
            action = f"{resource_type}.{method.lower()}"
        else:
            action, resource_type = resolved

        # Execute the actual request
        response = await call_next(request)

        # Extract user_id from request state (set by RBAC auth)
        user_id = None
        user = getattr(request.state, "user", None)
        if user is not None:
            user_id = getattr(user, "id", None)

        # Extract resource ID from path
        resource_id = _extract_resource_id(path)

        entry = AuditEntry(
            action=action,
            resource_type=resource_type,
            ip_address=_get_client_ip(request),
            user_agent=request.headers.get("User-Agent", ""),
            user_id=user_id,
            resource_id=resource_id,
            status_code=response.status_code,
        )

        # Write audit log (synchronous — fast enough for WAL SQLite)
        try:
            self._audit_logger.log(entry)
        except Exception:
            # Never let audit logging break the request
            pass

        return response
