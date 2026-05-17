"""Centralised SQL safety utilities for GISPulse.

Provides identifier validation, expression sanitisation, and SQL keyword
blocklist checking.  All modules that build dynamic SQL must use these
helpers instead of rolling their own regexes.
"""

from __future__ import annotations

import hashlib
import re

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Strict SQL identifier: letter/underscore start, alphanumeric/underscore/dot body.
# Max 127 chars (PostgreSQL limit).
SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,126}$")

# B-05: forbidden in any QGIS-friendly layer/column name. These are the
# characters that would let the caller break out of a double-quoted
# identifier (``"..."``), a single-quoted literal (``'...'``), or
# terminate the statement boundary. Anything else (spaces, accents,
# dashes, dots, leading digits, Unicode) is allowed because the GPKG
# trigger DDL always wraps the value in quotes.
_LAYER_NAME_FORBIDDEN: frozenset[str] = frozenset({'"', "'", ";", "\\"})
_LAYER_NAME_MAX_LEN = 128

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


def validate_layer_name(name: str, label: str = "layer") -> str:
    """Permissive identifier validator for QGIS-friendly layer/column names.

    Accepts spaces, accents, dashes, dots, leading digits — any character
    that is safe inside a *quoted* SQL identifier (``"..."``) **and** a
    *quoted* literal (``'...'``). Use this for layer/column names that
    originate from a local GPKG / SpatiaLite file edited by a desktop
    tool (QGIS, Field Calculator) where the user controls the names.

    DO NOT use for identifiers received over public HTTP — those go
    through :func:`validate_identifier` (strict ASCII).

    Forbidden: ``"``, ``'``, ``;``, ``\\``, plus all ASCII control
    characters (NUL, ``\\n``, ``\\r``, ``\\t``, ...).

    Args:
        name:  Candidate name.
        label: Human-readable label for the error message.

    Returns:
        The validated name unchanged.

    Raises:
        ValueError: If *name* is empty, longer than 128 chars, or
            contains a forbidden character.
    """
    if not isinstance(name, str):
        raise ValueError(f"Invalid {label}: {name!r} — must be str")
    if not name:
        raise ValueError(f"Invalid {label}: empty string")
    if len(name) > _LAYER_NAME_MAX_LEN:
        raise ValueError(
            f"Invalid {label}: {name!r} longer than {_LAYER_NAME_MAX_LEN} chars"
        )
    for ch in name:
        if ch in _LAYER_NAME_FORBIDDEN or ord(ch) < 0x20:
            raise ValueError(
                f"Invalid {label}: {name!r} contains forbidden character "
                f"{ch!r} — accept any character except \", ', ;, \\, "
                f"NUL or control chars"
            )
    return name


def slug_identifier(name: str, *, max_safe_len: int = 32) -> str:
    """Stable ASCII slug for use as an internal SQL object name.

    When *name* is already a strict SQL identifier
    (``[A-Za-z_][A-Za-z0-9_]*``), it is returned unchanged so legacy
    GPKGs created with v1.5.x trigger names keep matching after the
    B-05 relaxation. Otherwise a hash-based slug is returned of the
    form ``<safe-prefix>_<hash8>`` where ``hash8`` is the first 8 hex
    chars of SHA-1(name) and ``safe-prefix`` is the input lowercased
    with non-``[a-z0-9_]`` chars replaced by ``_`` (truncated to
    *max_safe_len* chars). The result is collision-resistant for
    realistic dataset sizes and stable across processes (no randomness).

    Args:
        name:         Layer / column name (must be non-empty).
        max_safe_len: Cap on the human-readable prefix length.

    Returns:
        A pure-ASCII identifier safe to embed in a trigger / index name
        without quoting.

    Raises:
        ValueError: If *name* is empty or not a string.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"slug_identifier: empty or non-str input {name!r}")
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_").lower()[:max_safe_len]
    if not safe:
        safe = "x"
    return f"{safe}_{digest}"


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
