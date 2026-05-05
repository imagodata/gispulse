"""Health-check primitives for a tracked GeoPackage.

Issue #93: extracted from ``gispulse.cli_track.cmd_doctor`` so the
HTTP layer (``POST /datasets/{id}/changelog/doctor``) and the CLI
share the same checklist + auto-fix logic.

The module is dependency-light: ``sqlite3`` + the existing
``persistence.gpkg_schema`` install helper. No I/O, no logging, no
human-friendly formatting — that's the caller's job.
"""

from __future__ import annotations

import sqlite3
from typing import Any


# GPKG application_id per OGC spec (0x47504B47 = "GPKG").
GPKG_APP_ID = 1196444487
# Rows older than 24 h that still have ``processed = 0`` indicate the
# watcher is stuck or never ran. Reported as a warning by ``run_doctor``.
STALE_THRESHOLD_S = 24 * 3600
# busy_timeout below this number triggers a warning (PR #57 hardening).
BUSY_TIMEOUT_WARN_MS = 5000

# Hard checks fail the doctor; warnings keep ``status="ok"``.
_FAIL_STATUSES = frozenset({"fail"})

# Status values returned for each check entry.
STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_FIXED = "fixed"


def _check_pragma(conn: sqlite3.Connection, name: str) -> str:
    """Read a PRAGMA value, returning ``""`` on failure or NULL."""
    try:
        row = conn.execute(f"PRAGMA {name}").fetchone()
    except sqlite3.Error:
        return ""
    if row is None:
        return ""
    try:
        return str(row[0])
    except (IndexError, TypeError):
        return ""


def _changelog_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = '_gispulse_change_log'"
    ).fetchone()
    return row is not None


def _stale_unprocessed(
    conn: sqlite3.Connection,
) -> tuple[int, str | None]:
    """Return ``(count, oldest_changed_at)`` for unprocessed rows older
    than :data:`STALE_THRESHOLD_S`. Positional row access keeps the
    helper factory-agnostic.
    """
    if not _changelog_table_exists(conn):
        return (0, None)
    try:
        row = conn.execute(
            "SELECT COUNT(*), MIN(changed_at) "
            "FROM _gispulse_change_log "
            "WHERE processed = 0 "
            "AND (julianday('now') - julianday(changed_at)) * 86400 > ?",
            (STALE_THRESHOLD_S,),
        ).fetchone()
    except sqlite3.Error:
        return (0, None)
    if row is None:
        return (0, None)
    return (int(row[0] or 0), row[1])


def _list_spatial_layers(conn: sqlite3.Connection) -> list[str]:
    """Names of every layer registered in ``gpkg_contents``.

    Uses positional row access so the helper works with both
    ``sqlite3.Row`` factory and the bare-tuple default.
    """
    try:
        rows = conn.execute(
            "SELECT table_name FROM gpkg_contents "
            "WHERE data_type = 'features'"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [str(r[0]) for r in rows if r[0]]


def _installed_triggers(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Map of ``layer_name → [op, ...]`` for layers with at least one
    GISPulse trigger installed.

    Reads ``sqlite_master.tbl_name`` (slug-aware after B-05 #107) so
    layer names with spaces / accents resolve correctly. Positional
    row access keeps the helper factory-agnostic.
    """
    rows = conn.execute(
        "SELECT name, tbl_name FROM sqlite_master "
        "WHERE type = 'trigger' "
        "AND name LIKE '\\_gispulse\\_trg\\_%' ESCAPE '\\'"
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        name = str(r[0])
        layer = str(r[1])
        for op in ("insert", "update", "delete"):
            if name.endswith(f"_{op}"):
                out.setdefault(layer, []).append(op)
                break
    return out


def run_doctor(
    conn: sqlite3.Connection,
    *,
    auto_fix: bool = False,
) -> dict[str, Any]:
    """Run the full health-check sweep against an open GPKG connection.

    Args:
        conn:     Open SQLite connection on the GeoPackage.
        auto_fix: When ``True`` and a layer has at least one GISPulse
                  trigger but is missing one or two of the three
                  expected ones, re-install the layer's full trigger
                  set via :func:`persistence.gpkg_schema.install_change_tracking`.

    Returns:
        A dict matching the ``POST /datasets/{id}/changelog/doctor``
        response contract::

            {
                "ok": bool,
                "status": "ok" | "fail",
                "errors": int,
                "repaired": [layer, ...],
                "checks": [
                    {"check": "...", "status": "ok"|"warn"|"fail"|"fixed",
                     "detail": "...", ...extra},
                    ...
                ],
            }
    """
    from persistence.gpkg_schema import install_change_tracking

    checks: list[dict[str, Any]] = []
    repaired: list[str] = []
    errors = 0

    def _add(name: str, status: str, detail: str = "", **extra: Any) -> None:
        nonlocal errors
        if status in _FAIL_STATUSES:
            errors += 1
        checks.append(
            {"check": name, "status": status, "detail": detail, **extra}
        )

    # ---- 1. application_id (GPKG magic) --------------------------------
    app_id = _check_pragma(conn, "application_id")
    if app_id == str(GPKG_APP_ID):
        _add("application_id", STATUS_OK, f"GPKG ({app_id})")
    else:
        _add(
            "application_id",
            STATUS_FAIL,
            f"expected {GPKG_APP_ID} (GPKG), got {app_id or 'unset'}",
        )

    # ---- 2. _gispulse_change_log table ---------------------------------
    if _changelog_table_exists(conn):
        _add(
            "changelog_table",
            STATUS_OK,
            "_gispulse_change_log present",
        )
    else:
        _add(
            "changelog_table",
            STATUS_FAIL,
            "_gispulse_change_log missing — install change tracking first",
        )

    # ---- 3. WAL mode (warn-only) ---------------------------------------
    journal = _check_pragma(conn, "journal_mode").lower()
    if journal == "wal":
        _add("journal_mode", STATUS_OK, "wal")
    else:
        _add(
            "journal_mode",
            STATUS_WARN,
            f"{journal!r} — concurrent writes from QGIS may block the watcher",
        )

    # ---- 4. busy_timeout (warn-only) -----------------------------------
    try:
        busy = int(_check_pragma(conn, "busy_timeout") or "0")
    except ValueError:
        busy = 0
    if busy >= BUSY_TIMEOUT_WARN_MS:
        _add("busy_timeout", STATUS_OK, f"{busy} ms")
    else:
        _add(
            "busy_timeout",
            STATUS_WARN,
            f"{busy} ms (< {BUSY_TIMEOUT_WARN_MS} ms) — "
            "SQLITE_BUSY likely under contention",
        )

    # ---- 5. Trigger presence per layer --------------------------------
    spatial = set(_list_spatial_layers(conn))
    installed = _installed_triggers(conn)
    expected_ops = {"insert", "update", "delete"}

    for layer in sorted(set(installed) | spatial):
        ops = set(installed.get(layer, []))
        if not ops:
            _add(
                "triggers",
                STATUS_OK,
                f"layer {layer!r} not tracked",
                layer=layer,
                missing=[],
            )
            continue
        missing = sorted(expected_ops - ops)
        if not missing:
            _add(
                "triggers",
                STATUS_OK,
                f"layer {layer!r}: all 3 triggers present",
                layer=layer,
                missing=[],
            )
            continue

        if auto_fix:
            try:
                install_change_tracking(conn, layer)
            except (ValueError, sqlite3.OperationalError) as exc:
                _add(
                    "triggers",
                    STATUS_FAIL,
                    f"layer {layer!r}: reinstall failed — {exc}",
                    layer=layer,
                    missing=missing,
                )
                continue
            repaired.append(layer)
            _add(
                "triggers",
                STATUS_FIXED,
                f"layer {layer!r}: reinstalled (was missing {missing})",
                layer=layer,
                missing=missing,
            )
        else:
            _add(
                "triggers",
                STATUS_FAIL,
                f"layer {layer!r}: missing {missing} — call doctor with auto_fix=true",
                layer=layer,
                missing=missing,
            )

    # ---- 6. Stale unprocessed rows (warn-only) ------------------------
    stale_count, oldest = _stale_unprocessed(conn)
    if stale_count == 0:
        _add(
            "stale_unprocessed",
            STATUS_OK,
            "no rows older than 24 h",
        )
    else:
        _add(
            "stale_unprocessed",
            STATUS_WARN,
            f"{stale_count} unprocessed row(s) older than 24 h "
            f"(oldest: {oldest}) — watcher dead?",
            count=stale_count,
            oldest=oldest,
        )

    return {
        "ok": errors == 0,
        "status": STATUS_OK if errors == 0 else STATUS_FAIL,
        "errors": errors,
        "repaired": repaired,
        "checks": checks,
    }


def health_score(checks: list[dict[str, Any]]) -> int:
    """Return a 0-100 health score from a check list.

    Each ``fail`` deducts 25, each ``warn`` deducts 5, ``fixed`` is
    neutral (was a fail, now repaired). Floors at 0, ceils at 100.
    Used by the portal UI to show a single-glance health number — the
    rich check list is still available for the detailed view.
    """
    score = 100
    for c in checks or []:
        s = c.get("status")
        if s == STATUS_FAIL:
            score -= 25
        elif s == STATUS_WARN:
            score -= 5
    return max(0, min(100, score))
