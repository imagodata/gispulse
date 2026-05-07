"""Read-only access helpers for ``_gispulse_change_log``.

Issue #93: the CLI's ``gispulse track tail`` and ``gispulse track list``
commands read pending DML rows + per-layer aggregates straight from the
GPKG. The HTTP layer needs the same surface so a portal-only user can
debug "why didn't my trigger fire?" from the browser. This module
extracts the read primitives (no I/O, no pretty-printing) so both
surfaces consume the identical SQL contract.

All public functions accept an open ``sqlite3.Connection`` — caller
owns the lifecycle (open / close / commit). Errors surface as
``ChangelogReaderError`` so HTTP routers can translate to a clean 400/
404 instead of leaking ``sqlite3.OperationalError``.
"""

from __future__ import annotations

import sqlite3
from typing import Any


class ChangelogReaderError(RuntimeError):
    """The change-log table is missing or inaccessible.

    Distinguished from a generic SQLite error so the HTTP layer can
    translate to a 404 (tracking not enabled) instead of a 500.
    """


# ---------------------------------------------------------------------------
# Pending-rows tail
# ---------------------------------------------------------------------------


# Column whitelist that mirrors the v2 (#7) + v3 (#103 B-02) schema —
# anything else is dropped at the SQL level so a future column rename
# never leaks raw rows over HTTP. v1.6.0 (#120 B-08) adds ``old_values``
# so the watcher can hydrate ``ChangeRecord.old_values`` for DELETE
# events, unblocking ``predicate:`` filters on rows that no longer
# exist in the underlying table. The column has been populated by the
# AFTER DELETE trigger since v1; only the read path was missing.
_TAIL_COLUMNS = (
    "id",
    "table_name",
    "operation",
    "row_pk",
    "changed_at",
    "geom_changed",
    "old_values",
)


def list_pending_changes(
    conn: sqlite3.Connection,
    *,
    layer: str | None = None,
    op: str | None = None,
    since_id: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return up to *limit* pending change-log rows ordered by id ASC.

    Args:
        conn:      Open SQLite connection on the GPKG.
        layer:     Optional ``table_name`` filter. ``None`` returns all.
        op:        Optional operation filter — one of ``"INSERT"``,
                   ``"UPDATE"``, ``"DELETE"`` (case-insensitive). ``None``
                   returns all.
        since_id:  Cursor — return rows with ``id > since_id``. Use the
                   ``next_since_id`` from the previous response to page
                   forward.
        limit:     Hard cap on returned rows. ``1 ≤ limit ≤ 500``.

    Returns:
        A list of dicts — one per row — with the columns from
        :data:`_TAIL_COLUMNS`. Empty when no pending rows match.

    Raises:
        ChangelogReaderError: If ``_gispulse_change_log`` is missing
            (caller should respond 404 — tracking not enabled).
        ValueError: If *limit* is out of bounds or *op* is unknown.
    """
    if not (1 <= limit <= 500):
        raise ValueError(f"limit must be in [1, 500], got {limit}")
    op_upper: str | None = None
    if op is not None:
        op_upper = op.upper()
        if op_upper not in ("INSERT", "UPDATE", "DELETE"):
            raise ValueError(
                f"op must be INSERT/UPDATE/DELETE (case-insensitive), got {op!r}"
            )

    where = ["processed = 0", "id > ?"]
    params: list[Any] = [int(since_id or 0)]
    if layer:
        where.append("table_name = ?")
        params.append(layer)
    if op_upper is not None:
        where.append("operation = ?")
        params.append(op_upper)
    cols = ", ".join(_TAIL_COLUMNS)
    sql = (
        f"SELECT {cols} FROM _gispulse_change_log "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY id ASC LIMIT ?"
    )
    params.append(int(limit))

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise ChangelogReaderError(
                "_gispulse_change_log missing — change tracking not enabled "
                "on this GPKG"
            ) from exc
        raise

    return [dict(r) for r in rows]


def next_since_id(rows: list[dict[str, Any]], fallback: int = 0) -> int:
    """Compute the cursor to pass on the next call.

    The caller stores ``next_since_id`` from the response and re-uses
    it as ``since_id`` to page forward without skipping or duplicating
    rows. Returns *fallback* when *rows* is empty.
    """
    if not rows:
        return int(fallback or 0)
    return max(int(r["id"]) for r in rows)


# ---------------------------------------------------------------------------
# Per-layer aggregates
# ---------------------------------------------------------------------------


def changelog_stats(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Return aggregate counts per layer + grand totals.

    Used by ``GET /datasets/{id}/changelog/stats`` (issue #93) and
    ``gispulse track list``. Output shape::

        {
            "total_pending": 17,
            "total_processed": 4_213,
            "by_layer": [
                {
                    "layer": "parcels",
                    "pending": 12,
                    "processed": 3000,
                    "by_op": {"INSERT": 5, "UPDATE": 6, "DELETE": 1},
                },
                ...
            ],
        }

    Raises:
        ChangelogReaderError: If ``_gispulse_change_log`` is missing.
    """
    try:
        totals = conn.execute(
            "SELECT "
            "  SUM(CASE WHEN processed = 0 THEN 1 ELSE 0 END) AS pending, "
            "  SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END) AS processed "
            "FROM _gispulse_change_log"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise ChangelogReaderError(
                "_gispulse_change_log missing — change tracking not enabled "
                "on this GPKG"
            ) from exc
        raise

    total_pending = int(totals["pending"] or 0) if totals else 0
    total_processed = int(totals["processed"] or 0) if totals else 0

    layer_rows = conn.execute(
        "SELECT table_name, operation, "
        "  SUM(CASE WHEN processed = 0 THEN 1 ELSE 0 END) AS pending, "
        "  SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END) AS processed "
        "FROM _gispulse_change_log "
        "GROUP BY table_name, operation"
    ).fetchall()

    by_layer: dict[str, dict[str, Any]] = {}
    for r in layer_rows:
        layer = r["table_name"] or ""
        op = (r["operation"] or "").upper()
        bucket = by_layer.setdefault(
            layer,
            {
                "layer": layer,
                "pending": 0,
                "processed": 0,
                "by_op": {"INSERT": 0, "UPDATE": 0, "DELETE": 0},
            },
        )
        pending = int(r["pending"] or 0)
        processed = int(r["processed"] or 0)
        bucket["pending"] += pending
        bucket["processed"] += processed
        if op in bucket["by_op"]:
            bucket["by_op"][op] += pending + processed

    return {
        "total_pending": total_pending,
        "total_processed": total_processed,
        "by_layer": sorted(by_layer.values(), key=lambda b: b["layer"]),
    }
