"""
GISPulse — Unified error response middleware.

Registers global exception handlers on a FastAPI application so that all
HTTP errors and unhandled exceptions are returned as a consistent JSON
envelope:

    {
        "error": {
            "code":    "<machine-readable string>",
            "message": "<human-readable description>",
            "detail":  <any | null>
        }
    }

Usage::

    from gispulse.adapters.http.error_handlers import register_error_handlers

    app = FastAPI(...)
    register_error_handlers(app)
"""

from __future__ import annotations

import traceback
import uuid
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.logging import get_logger

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_body(code: str, message: str, detail: Any = None) -> dict:
    """Build the standard error envelope."""
    return {"error": {"code": code, "message": message, "detail": detail}}


def _apply_cors_headers(request: Request, response: JSONResponse) -> None:
    # Starlette installs the generic-Exception handler on ServerErrorMiddleware,
    # which runs outside the user middleware stack — so CORSMiddleware never
    # sees the 500 response. Mirror its simple-response logic here so browsers
    # see the HTTP status instead of a misleading CORS error.
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = getattr(request.app.state, "cors_origins", None) or []
    response.headers["Vary"] = "Origin"
    if "*" in allowed:
        response.headers["Access-Control-Allow-Origin"] = "*"
        return
    if origin in allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------

async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Handle Starlette / FastAPI HTTP exceptions (400, 401, 403, 404, …)."""
    status_to_code: dict[int, str] = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "UNPROCESSABLE_ENTITY",
        429: "TOO_MANY_REQUESTS",
        500: "INTERNAL_SERVER_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }
    code = status_to_code.get(exc.status_code, f"HTTP_{exc.status_code}")
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(
            code=code,
            message=str(exc.detail) if exc.detail else code.replace("_", " ").title(),
            detail=None,
        ),
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic / FastAPI request-validation errors (422)."""
    # Convert pydantic error list into a compact list of {field, msg} dicts
    detail = [
        {"field": " -> ".join(str(loc) for loc in err["loc"]), "msg": err["msg"]}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            detail=detail,
        ),
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all handler for unexpected server errors (500)."""
    trace_id = uuid.uuid4().hex[:8]
    # Log the full traceback server-side with trace_id for correlation
    tb = traceback.format_exc()
    _log.error(
        "unhandled_exception",
        trace_id=trace_id,
        path=str(request.url.path),
        method=request.method,
        exc_type=type(exc).__name__,
        exc_info=True,
    )
    # Include traceback only when running in debug/test mode to avoid
    # leaking internals in production.
    from starlette.config import Config  # local import to avoid circular deps
    config = Config()
    debug = config("DEBUG", cast=bool, default=False)
    detail: Any = tb if debug else f"trace_id={trace_id}"

    response = JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected error occurred.",
            detail=detail,
        ),
    )
    _apply_cors_headers(request, response)
    return response


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_error_handlers(app: FastAPI) -> None:
    """Register all GISPulse error handlers on *app*.

    Call this once during app creation, after ``FastAPI(...)`` but before
    mounting routers.

    Args:
        app: The FastAPI application instance to configure.
    """
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_exception_handler)  # type: ignore[arg-type]
