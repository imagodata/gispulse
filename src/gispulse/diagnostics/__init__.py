"""Runtime diagnostics — system health checks shared by CLI and HTTP API.

The pure functions in :mod:`gispulse.diagnostics.system` are consumed by both
``gispulse doctor`` (CLI surface) and ``POST /system/doctor`` (Portal HTTP
surface). Keeping the logic in a single module is the contract that maintains
the CLI ↔ Portal symmetry axiom for system diagnostics.
"""

from gispulse.diagnostics.system import (
    CheckResult,
    CheckStatus,
    DoctorResult,
    KNOWN_CHECKS,
    run_checks,
)

__all__ = [
    "CheckResult",
    "CheckStatus",
    "DoctorResult",
    "KNOWN_CHECKS",
    "run_checks",
]
