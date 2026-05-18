"""System diagnostics router — POST /system/doctor.

HTTP surface of :func:`gispulse.diagnostics.system.run_checks`. Mirrors
``gispulse doctor --json`` exactly: same selectors, same response schema.

Closes P0-4 of the CLI ↔ Portal parity audit (issue #91 / EPIC #90).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from gispulse.adapters.http.auth import require_role
from gispulse.diagnostics import KNOWN_CHECKS, run_checks

router = APIRouter(prefix="/system", tags=["system"])


class DoctorRequest(BaseModel):
    checks: list[str] | None = Field(
        default=None,
        description="Subset of checks to run. Omit / null to run all known checks.",
    )


class DoctorCheck(BaseModel):
    name: str
    status: str  # "ok" | "warning" | "error" | "skipped"
    detail: str


class DoctorResponse(BaseModel):
    summary: dict[str, int]
    checks: list[DoctorCheck]
    ran_at: str
    has_critical: bool


@router.post("/doctor", response_model=DoctorResponse)
async def doctor(
    body: DoctorRequest | None = None,
    _user=Depends(require_role("admin")),
) -> DoctorResponse:
    """Run system diagnostics and return per-check results.

    Admin-only because the response leaks runtime versions (light recon).
    In portal-mode local (``gispulse portal``) where auth is disabled, the
    ``require_role`` dependency is a no-op and the endpoint is reachable.
    """
    requested = body.checks if body and body.checks is not None else None

    if requested is not None:
        unknown = [n for n in requested if n not in KNOWN_CHECKS]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown checks: {sorted(unknown)}. "
                       f"Known: {sorted(KNOWN_CHECKS.keys())}",
            )

    result = run_checks(requested)
    return DoctorResponse(**result.to_dict())
