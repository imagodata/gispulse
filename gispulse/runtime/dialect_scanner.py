"""Config-load-time scan for PostGIS-only SQL constructs (issue #146).

ADR 0001 declares DuckDB-spatial the contract dialect for
``triggers.yaml``. That declaration was documentary only: a user could
write a PostGIS-only construct in a ``run_sql`` action or a ``predicate``
and only discover it when the first tick blew up — often long after
deployment.

This module closes that gap with a *regex blocklist* scan (no SQL
grammar parser — ADR 0001 rejects auto-transpilation, and false
positives are silenced by declaring ``engine: postgis``, which is the
correct answer anyway). It walks every ``run_sql`` expression and every
``predicate`` and flags the known PostGIS-only constructs.

The caller (config loader / CLI) emits a structured ``dialect_drift``
warning per finding *unless* the config declares ``engine: postgis``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from gispulse.runtime.config_loader import GISPulseConfig


@dataclass(frozen=True)
class DialectFinding:
    """One PostGIS-only construct found in a config expression."""

    construct: str       # human label of the offending pattern
    location: str        # where it was found (trigger name + field)
    snippet: str         # the verbatim expression it appeared in
    hint: str            # how to make it portable

    def message(self) -> str:
        return (
            f"{self.location}: PostGIS-only construct {self.construct} — "
            f"{self.hint}"
        )


# Each entry: (label, regex, portability hint). The patterns are
# deliberately loose — a blocklist, not a parser. ``engine: postgis``
# silences every one of them at once.
_RAW_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "ST_Transform/2 (2-arg form)",
        r"\bST_Transform\s*\(\s*[^,()]+,\s*\d+\s*\)",
        "DuckDB's ST_Transform needs explicit source+target CRS — use the "
        "3-arg form ST_Transform(geom, 'EPSG:2154', 'EPSG:4326')",
    ),
    (
        "geography() cast",
        r"\bgeography\s*\(",
        "geography is a PostGIS type; cast to a metric CRS and use planar "
        "functions, or pin engine: postgis",
    ),
    (
        "INTERSECTS() shorthand",
        r"(?<!ST_)\bINTERSECTS\s*\(",
        "use the portable ST_Intersects(a, b)",
    ),
    (
        "&& bounding-box operator",
        r"&&",
        "the && GiST hint is PostGIS-only — DuckDB plans bbox joins itself",
    ),
    (
        "::geometry / ::geography cast",
        r"::\s*(?:geometry|geography)\b",
        "the ::type cast is PostgreSQL syntax — drop it or pin engine: postgis",
    ),
)

# Compile once, case-insensitive.
_COMPILED: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (label, re.compile(rx, re.IGNORECASE), hint)
    for label, rx, hint in _RAW_PATTERNS
)


def scan_expression(expr: str, location: str) -> list[DialectFinding]:
    """Return every PostGIS-only construct found in a single expression."""
    if not expr:
        return []
    findings: list[DialectFinding] = []
    for label, pattern, hint in _COMPILED:
        if pattern.search(expr):
            findings.append(
                DialectFinding(
                    construct=label, location=location, snippet=expr, hint=hint
                )
            )
    return findings


def scan_for_dialect_drift(config: "GISPulseConfig") -> list[DialectFinding]:
    """Scan every ``run_sql`` expression and ``predicate`` in ``config``.

    Returns an empty list when the config pins ``engine: postgis`` — the
    constructs are legitimate there — or when nothing PostGIS-only is
    found. The caller decides how to surface the findings.
    """
    if (config.engine or "").strip().lower() == "postgis":
        return []

    findings: list[DialectFinding] = []
    for entry in config.triggers:
        if entry.predicate:
            findings.extend(
                scan_expression(
                    entry.predicate, f"trigger {entry.name!r} predicate"
                )
            )
        for idx, action in enumerate(entry.actions):
            if action.type == "run_sql" and action.expression:
                findings.extend(
                    scan_expression(
                        action.expression,
                        f"trigger {entry.name!r} run_sql action #{idx + 1}",
                    )
                )
    return findings


__all__ = ["DialectFinding", "scan_expression", "scan_for_dialect_drift"]
