"""Read-only middleware for the public demo deployment.

When ``GISPULSE_READ_ONLY=true`` is set, the FastAPI app is wrapped with this
middleware which rejects any state-mutating HTTP method (``POST``, ``PUT``,
``PATCH``, ``DELETE``) with ``403 Forbidden``.

A small allowlist of *compute-only* ``POST`` endpoints (validate, preview,
sql-preview, evaluate, run, run-node) stays open so the demo can keep its
interactive features (pipeline preview, rule validation, trigger evaluation)
without ever persisting data.

Health/metrics/options/preflight always pass through.
"""

from __future__ import annotations

import re
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# POST endpoints that perform pure computation (no DB writes, no file uploads,
# no shell-outs creating persistent state). They stay allowed in read-only mode
# so the public demo keeps its interactive preview/validation features.
_COMPUTE_ALLOWLIST: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"^/capabilities/sql-preview$",
        r"^/pipelines/validate$",
        r"^/pipelines/execute-steps$",
        r"^/rules/[^/]+/validate$",
        r"^/rules/from-node$",
        r"^/filter/(apply|preview|validate|chain)$",
        r"^/triggers/[^/]+/evaluate$",
        r"^/scenarios/[^/]+/run-node$",
        r"^/projects/[^/]+/detect-relations$",
        # Mode 2 "Try it" mini-backend (v1.5.x) — pure in-memory trigger
        # evaluation. The endpoint never touches the bundled GPKGs and
        # the dispatcher is stubbed out to capture, not execute. Safe to
        # leave open even on the public read-only deployment.
        r"^/examples/[^/]+/triggers/dryrun$",
    )
)


def _is_compute_only(path: str) -> bool:
    return any(p.match(path) for p in _COMPUTE_ALLOWLIST)


class ReadOnlyMiddleware(BaseHTTPMiddleware):
    """Block state-mutating requests when the deployment is in read-only mode.

    The middleware is intentionally permissive on:

    * ``GET``, ``HEAD``, ``OPTIONS`` — never blocked.
    * Compute-only ``POST`` endpoints (see ``_COMPUTE_ALLOWLIST``).
    * Any caller that proves admin scope (valid ``X-API-Key`` matching the
      configured admin key, set via ``GISPULSE_SQL_ADMIN_KEY``). This lets the
      demo seed worker still write through.

    Every other write attempt returns ``403`` with a clear message.
    """

    def __init__(self, app, *, admin_keys: Iterable[str] | None = None):
        super().__init__(app)
        self._admin_keys = {k.strip() for k in (admin_keys or ()) if k and k.strip()}

    def _is_admin(self, request: Request) -> bool:
        if not self._admin_keys:
            return False
        provided = request.headers.get("X-API-Key", "").strip()
        if not provided:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                provided = auth[7:].strip()
        return bool(provided) and provided in self._admin_keys

    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        if method in _SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if method == "POST" and _is_compute_only(path):
            return await call_next(request)

        if self._is_admin(request):
            return await call_next(request)

        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "READ_ONLY_DEMO",
                    "message": "This deployment is in read-only mode. "
                    "Mutations are disabled.",
                    "detail": None,
                }
            },
        )
