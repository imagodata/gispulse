"""Centralised SQL safety utilities for GISPulse.

Provides identifier validation, expression sanitisation, and SQL keyword
blocklist checking.  All modules that build dynamic SQL must use these
helpers instead of rolling their own regexes.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Strict SQL identifier: letter/underscore start, alphanumeric/underscore/dot body.
# Max 127 chars (PostgreSQL limit).
SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,126}$")

# DDL / DCL keywords that must never appear in user-supplied expressions.
SQL_BLOCKLIST = re.compile(
    r"\b(DROP|ALTER|CREATE|TRUNCATE|DELETE|INSERT|UPDATE|GRANT|REVOKE|COPY|"
    r"pg_read_file|pg_write_file|lo_import|lo_export)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_identifier(name: str, label: str = "identifier") -> str:
    """Validate that *name* is a safe SQL identifier.

    Args:
        name:  The identifier string to check.
        label: Human-readable label for error messages (e.g. "table", "field").

    Returns:
        The validated identifier unchanged.

    Raises:
        ValueError: If *name* is empty or contains unsafe characters.
    """
    if not name or not SAFE_IDENT_RE.match(name):
        raise ValueError(
            f"Invalid SQL {label}: {name!r}. "
            "Must match ^[A-Za-z_][A-Za-z0-9_.]{0,126}$"
        )
    return name


def validate_expression(expr: str) -> str:
    """Validate a user-supplied SQL expression against dangerous patterns.

    Rejects expressions containing DDL/DCL keywords, dollar-quoting, or
    statement terminators.

    Args:
        expr: The SQL expression to validate.

    Returns:
        The expression unchanged.

    Raises:
        ValueError: If the expression contains forbidden patterns.
    """
    if SQL_BLOCKLIST.search(expr):
        raise ValueError(
            f"Expression contains forbidden SQL keyword: {expr[:100]!r}"
        )
    if "$$" in expr or ";" in expr:
        raise ValueError(
            f"Expression contains forbidden characters (;, $$): {expr[:100]!r}"
        )
    return expr


def validate_ref_filter(ref_filter: str) -> str:
    """Validate a reference filter clause (WHERE predicate fragment).

    Stricter than :func:`validate_expression` — also rejects quotes and
    comment markers.

    Args:
        ref_filter: The filter string (e.g. ``"categorie='N2000'"``).

    Returns:
        The filter unchanged.

    Raises:
        ValueError: If the filter contains suspicious patterns.
    """
    if re.search(
        r"[;'\"]|--|/\*|\*/|\$\$|"
        r"DROP|ALTER|INSERT|UPDATE|DELETE|TRUNCATE|EXEC|"
        r"GRANT|REVOKE|COPY|CALL|"
        r"pg_read_file|pg_write_file|lo_import|lo_export",
        ref_filter,
        re.IGNORECASE,
    ):
        raise ValueError(f"Unsafe ref_filter rejected: {ref_filter!r}")
    return ref_filter
