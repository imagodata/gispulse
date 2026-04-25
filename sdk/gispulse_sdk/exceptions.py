"""SDK-specific exceptions mapped to HTTP status codes."""

from __future__ import annotations


class GISPulseError(Exception):
    """Base exception for all SDK errors."""

    def __init__(self, message: str, status_code: int | None = None, detail: object = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class AuthError(GISPulseError):
    """401 — Invalid or missing API key."""


class NotFoundError(GISPulseError):
    """404 — Resource not found."""


class ConflictError(GISPulseError):
    """409 — Duplicate or conflicting resource."""


class ValidationError(GISPulseError):
    """422 — Request validation failed."""


class ServerError(GISPulseError):
    """5xx — Server-side error."""


_STATUS_MAP: dict[int, type[GISPulseError]] = {
    401: AuthError,
    403: AuthError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
}


def raise_for_status(status_code: int, body: object) -> None:
    """Raise the appropriate SDK exception for a non-2xx response."""
    if 200 <= status_code < 300:
        return

    detail = body if isinstance(body, dict) else {"raw": str(body)}
    message = ""
    if isinstance(body, dict):
        message = body.get("detail", body.get("message", str(body)))
    else:
        message = str(body)

    exc_cls = _STATUS_MAP.get(status_code, ServerError if status_code >= 500 else GISPulseError)
    raise exc_cls(message, status_code=status_code, detail=detail)
