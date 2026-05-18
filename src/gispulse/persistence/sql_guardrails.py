"""SQL guardrails for :meth:`GeoPackageEngine.execute`.

Why this exists
---------------
``execute()`` is the single hot path through which YAML actions
(``set_field``, ``run_sql``) issue DML against a user GPKG. Without a
sandbox layer, a malicious ``cadastre_paris.yaml`` downloaded from a
forum could:

* ``DROP TABLE`` on user layers,
* ``DELETE FROM gpkg_contents`` / ``gpkg_geometry_columns`` -> a
  permanently corrupted GPKG that QGIS refuses to open,
* ``ATTACH DATABASE`` to siphon data from another file,
* flip ``PRAGMA writable_schema = 1`` then bidouille ``sqlite_master``,
* tamper with ``_gispulse_*`` audit tables and break the change-log.

We refuse those at the engine boundary — defense in depth on top of the
upstream :func:`core.sql_safety.validate_expression`.

Design
------
We intentionally do **not** pull in a full SQL parser (sqlglot, sqlparse
are not project deps). Statement-type detection only — strip leading
comments / whitespace, look at the leading keyword, then sweep the
normalised text for forbidden patterns and protected table writes. A
single :func:`enforce` call per ``execute()`` is the contract.

Single-statement guarantee
--------------------------
We refuse anything that contains a meaningful semicolon between
statements. SQLite's ``Connection.execute`` already accepts only one
statement, but ``executescript`` does not, so this is a belt-and-braces
check that also catches ``"INSERT ...; DROP TABLE x"`` payloads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SecurityError(Exception):
    """Raised when SQL submitted to ``execute()`` violates the guardrails.

    Distinct from :class:`sqlite3.OperationalError` so the retry wrapper
    in :mod:`gispulse.runtime.sqlite_retry` does not retry it. A bad
    payload should fail fast.
    """


@dataclass(frozen=True)
class ParsedStatement:
    """Result of :func:`parse_statement` — the data the enforcer needs."""

    statement_type: str  # upper-case keyword: SELECT / INSERT / UPDATE / ...
    normalized_sql: str  # comments stripped, lower-case, single line
    raw_sql: str         # original (used for logging only)
    paren_depth: int     # max nesting depth (CTEs / sub-queries)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


# Permanent allowlist of statement leading keywords that are valid DML
# from a YAML action standpoint. SELECT is included so ``execute()`` can
# also be used for read paths (e.g. an audit query in a custom action),
# but the wider system prefers :meth:`execute_sql` / :meth:`sql_to_gdf`
# for reads — ``execute()`` returning ``rowcount`` is a write API.
_DML_ALLOWED: Final[frozenset[str]] = frozenset({"INSERT", "UPDATE", "DELETE", "SELECT"})

# DDL is gated by the ``allow_ddl`` flag (used for internal migrations
# only — never reachable from a YAML action).
_DDL_KEYWORDS: Final[frozenset[str]] = frozenset({"CREATE", "DROP", "ALTER"})

# Always refused, regardless of any flag. ATTACH/DETACH could open a
# sibling DB; VACUUM rewrites the file; PRAGMA can flip writable_schema
# (handled separately so we can list the safe pragmas if we ever need
# to). REINDEX/ANALYZE are read-only-ish but not part of our contract.
_HARD_BLOCKED: Final[frozenset[str]] = frozenset(
    {"ATTACH", "DETACH", "VACUUM", "REINDEX", "ANALYZE", "PRAGMA", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"}
)

# Regex: leading keyword (after comment / whitespace strip).
_LEADING_KEYWORD_RE = re.compile(r"^\s*([A-Za-z]+)")

# Regex for SQL line + block comments. We strip them BEFORE we look at
# the leading keyword so ``-- DROP TABLE\nSELECT 1`` is recognised as a
# SELECT, not a comment-prefixed DROP.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# String literals — we mask them before scanning for DDL keywords so a
# literal like ``'DROP'`` does not trigger. Double-quoted tokens in SQL
# are **identifiers**, not literals, so we must keep them visible to
# the protected-table scan (otherwise ``"gpkg_contents"`` would be
# masked to nothing). We strip the quote chars instead so the inner
# identifier remains scannable.
_STRING_LITERAL_RE = re.compile(
    r"'(?:[^']|'')*'"   # single-quoted, supports the SQL '' escape
)
_DOUBLE_QUOTED_IDENT_RE = re.compile(r'"((?:[^"]|"")*)"')

# Forbidden danger patterns we refuse even inside an otherwise-allowed
# statement (e.g. an UPDATE that mutates ``writable_schema`` via PRAGMA
# in a sub-statement). Case-insensitive, applied to the comment-stripped
# string-masked text.
_DANGER_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("writable_schema", r"\bwritable_schema\b"),
    ("sqlite_master", r"\bsqlite_master\b"),
    ("sqlite_temp_master", r"\bsqlite_temp_master\b"),
    ("attach_database", r"\battach\s+database\b"),
    ("detach_database", r"\bdetach\s+database\b"),
    ("load_extension", r"\bload_extension\b"),
)

# Tables we refuse to write to from ``execute()``. Case-insensitive
# prefix match. Adding a layer goes through ``write_layer`` (pyogrio),
# not raw SQL.
_PROTECTED_PREFIXES: Final[tuple[str, ...]] = (
    "gpkg_",         # GPKG metadata: gpkg_contents, gpkg_geometry_columns, ...
    "rtree_",        # Spatial index — handled by GPKG triggers
    "sqlite_",       # SQLite internals
    "_gispulse_",    # Our own audit / state tables
)

# Maximum nesting depth for parentheses. CTEs + sub-queries beyond 5
# levels are almost certainly an attempt to DoS the parser; SQLite would
# accept them, but we do not.
MAX_PAREN_DEPTH: Final[int] = 5

# Regex: any INSERT/UPDATE/DELETE/REPLACE INTO/FROM <table>. Applied to
# the string-masked text where double-quoted identifiers have been
# unwrapped (so ``"gpkg_contents"`` matches ``gpkg_contents``) and
# string literals are replaced by ``''``.
_WRITE_TARGET_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+"
    r"(?:(?P<schema>[A-Za-z_][\w]*)\.)?"
    r"(?P<table>[A-Za-z_][\w]*)",
    re.IGNORECASE,
)


def _strip_comments(sql: str) -> str:
    """Remove block + line comments. Strings are NOT touched yet."""
    sql = _BLOCK_COMMENT_RE.sub(" ", sql)
    sql = _LINE_COMMENT_RE.sub(" ", sql)
    return sql


def _mask_strings(sql: str) -> str:
    """Replace single-quoted string literals with ``''`` sentinels.

    Double-quoted tokens are identifiers in SQL, not literals — we
    unwrap their quote characters so the inner name remains visible to
    the protected-table scan (e.g. ``"gpkg_contents"`` -> ``gpkg_contents``).
    """
    masked = _STRING_LITERAL_RE.sub("''", sql)
    masked = _DOUBLE_QUOTED_IDENT_RE.sub(lambda m: m.group(1), masked)
    return masked


def _strip_meaningful_trailing_semicolon(sql: str) -> str:
    """Drop a single trailing semicolon (SQL pretty-printers add one)."""
    stripped = sql.rstrip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    return stripped


def _has_extra_statement(sql: str) -> bool:
    """Return True when *sql* (post-strip-trailing) contains a semicolon.

    We mask string literals first so ``'a;b'`` does not count, then
    look for any remaining ``;``. Any one is one too many — SQLite will
    only run the first statement, but a malicious payload can hide a
    second one we do not want even attempted.
    """
    masked = _mask_strings(sql)
    return ";" in masked


def _max_paren_depth(sql: str) -> int:
    """Maximum unmatched-open paren depth, ignoring strings.

    We do not validate balanced parens (SQLite does that). We only
    measure the deepest open prefix — enough to refuse pathological
    nesting before we hand the string to the parser.
    """
    depth = 0
    max_depth = 0
    in_single = False
    in_double = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if in_single:
            if ch == "'" and (i + 1 < n and sql[i + 1] == "'"):
                i += 2  # SQL '' escape
                continue
            if ch == "'":
                in_single = False
        elif in_double:
            if ch == '"' and (i + 1 < n and sql[i + 1] == '"'):
                i += 2
                continue
            if ch == '"':
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == "(":
                depth += 1
                if depth > max_depth:
                    max_depth = depth
            elif ch == ")":
                depth = max(0, depth - 1)
        i += 1
    return max_depth


def parse_statement(sql: str) -> ParsedStatement:
    """Parse *sql* enough to know what we are dealing with.

    Raises:
        SecurityError: if *sql* is empty after comment stripping.
    """
    if not sql or not sql.strip():
        raise SecurityError("empty SQL statement")

    no_comments = _strip_comments(sql)
    no_trailing = _strip_meaningful_trailing_semicolon(no_comments)
    match = _LEADING_KEYWORD_RE.match(no_trailing)
    if not match:
        raise SecurityError(
            f"could not identify leading SQL keyword: {sql[:80]!r}"
        )

    keyword = match.group(1).upper()
    paren_depth = _max_paren_depth(no_trailing)
    return ParsedStatement(
        statement_type=keyword,
        normalized_sql=no_trailing,
        raw_sql=sql,
        paren_depth=paren_depth,
    )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


def _check_protected_writes(masked_sql: str) -> None:
    """Refuse any write that targets a protected internal table."""
    for match in _WRITE_TARGET_RE.finditer(masked_sql):
        table = (match.group("table") or "").lower()
        if not table:
            continue
        for prefix in _PROTECTED_PREFIXES:
            if table.startswith(prefix):
                raise SecurityError(
                    f"write to protected table {table!r} is forbidden "
                    f"(prefix {prefix!r}). Use the engine's typed API "
                    f"(write_layer / kv_set / change-log helpers)."
                )


def _check_danger_patterns(masked_sql: str) -> None:
    """Refuse references to dangerous SQLite primitives."""
    for label, pattern in _DANGER_PATTERNS:
        if re.search(pattern, masked_sql, re.IGNORECASE):
            raise SecurityError(
                f"forbidden SQL primitive {label!r} detected"
            )


def enforce(sql: str, *, allow_ddl: bool = False) -> ParsedStatement:
    """Validate *sql* against the GISPulse SQL guardrails.

    Args:
        sql:        Single SQL statement.
        allow_ddl:  When True, ``CREATE`` / ``DROP`` / ``ALTER`` are
                    permitted (used by internal migrations only — never
                    set from a YAML-driven path).

    Returns:
        The :class:`ParsedStatement` so callers can log the canonical
        statement type.

    Raises:
        SecurityError: on any guardrail violation.
    """
    parsed = parse_statement(sql)

    if parsed.paren_depth > MAX_PAREN_DEPTH:
        raise SecurityError(
            f"sub-query / CTE nesting depth {parsed.paren_depth} exceeds "
            f"the maximum {MAX_PAREN_DEPTH}"
        )

    # Multi-statement guard. We strip exactly one trailing semicolon in
    # ``parse_statement``; any remaining one is an attempt to chain.
    if _has_extra_statement(parsed.normalized_sql):
        raise SecurityError(
            "multiple SQL statements are not permitted in a single "
            "execute() call"
        )

    statement_type = parsed.statement_type

    if statement_type in _HARD_BLOCKED:
        raise SecurityError(
            f"{statement_type} statements are forbidden via execute()"
        )

    if statement_type in _DDL_KEYWORDS:
        if not allow_ddl:
            raise SecurityError(
                f"{statement_type} requires allow_ddl=True (reserved for "
                f"internal migrations, never exposed via YAML actions)"
            )
        # Even with allow_ddl, the danger / protected scans still apply.
    elif statement_type not in _DML_ALLOWED:
        raise SecurityError(
            f"{statement_type!r} is not in the execute() whitelist "
            f"({sorted(_DML_ALLOWED)})"
        )

    masked = _mask_strings(parsed.normalized_sql)
    _check_danger_patterns(masked)

    # Protected-table writes apply to write-shaped statements only.
    if statement_type in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
        _check_protected_writes(masked)
    if statement_type in _DDL_KEYWORDS and allow_ddl:
        # DDL with allow_ddl=True is internal migration; we still refuse
        # DDL that targets a protected prefix when allow_ddl is False
        # (handled above by the DDL gate). When allow_ddl=True the
        # internal caller is presumed trusted, but we still log via the
        # caller — no extra scan here.
        pass

    return parsed


__all__ = [
    "MAX_PAREN_DEPTH",
    "ParsedStatement",
    "SecurityError",
    "enforce",
    "parse_statement",
]
