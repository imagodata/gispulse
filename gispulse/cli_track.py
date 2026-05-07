"""``gispulse track ...`` CLI subapp — SQL change-tracking on a GeoPackage.

Five sub-commands (v1.3.0 #4):

    gispulse track install   <gpkg> --layer <name> [--pk fid]
    gispulse track install   <gpkg> --all-layers
    gispulse track uninstall <gpkg> --layer <name>
    gispulse track list      <gpkg> [--json]
    gispulse track tail      <gpkg> [--limit 50] [--json]

The actual SQLi-safe DDL lives in
:mod:`persistence.gpkg_schema` (``install_change_tracking`` /
``uninstall_change_tracking``); this module is the user-facing shell that
wraps validation, output formatting, and the ``--all-layers`` reconciliation.

Conventions match :mod:`gispulse.cli_triggers`:
    * Human-friendly Rich output → stdout
    * Structured JSON events     → stderr
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import typer

from gispulse.cli_triggers import _human, _log_event, _maybe_warn_network_fs

track_app = typer.Typer(
    name="track",
    help="Manage SQL change-tracking triggers on a GeoPackage.",
    add_completion=False,
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _open_gpkg(path: Path) -> sqlite3.Connection:
    """Open a GPKG with the same pragmas the engine uses (WAL + busy timeout).

    Delegates to :func:`persistence.gpkg_connection.connect_gpkg` so every
    code path that touches a GeoPackage applies identical concurrency
    pragmas. The CLI wrapper only adds the user-friendly "file not found"
    exit before opening.
    """
    if not path.exists():
        _human(f"GPKG not found: {path}", err=True, style="red")
        raise typer.Exit(code=2)
    from persistence.gpkg_connection import connect_gpkg

    return connect_gpkg(path, row_factory=sqlite3.Row)


def _list_spatial_layers(conn: sqlite3.Connection) -> list[str]:
    """Return non-internal spatial layers from gpkg_contents.

    Falls back to an empty list if ``gpkg_contents`` does not exist (fresh
    SQLite file masquerading as GPKG); callers should handle that case.
    """
    try:
        rows = conn.execute(
            "SELECT table_name FROM gpkg_contents "
            "WHERE data_type = 'features' AND table_name NOT LIKE '\\_gispulse\\_%' ESCAPE '\\'"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r["table_name"] for r in rows]


def _installed_triggers(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Map each tracked layer → list of installed trigger names.

    Reads ``sqlite_master`` for triggers matching the
    ``_gispulse_trg_<layer>_<op>`` convention defined in
    :func:`persistence.gpkg_schema._build_change_triggers`.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' "
        "AND name LIKE '\\_gispulse\\_trg\\_%' ESCAPE '\\'"
    ).fetchall()
    by_layer: dict[str, list[str]] = {}
    for row in rows:
        name: str = row["name"]
        # _gispulse_trg_<layer>_<op>
        rest = name[len("_gispulse_trg_"):]
        try:
            layer, op = rest.rsplit("_", 1)
        except ValueError:
            continue
        if op not in ("insert", "update", "delete"):
            continue
        by_layer.setdefault(layer, []).append(op)
    return by_layer


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    """Render a result either as JSON (stdout) or a Rich line (stdout)."""
    if as_json:
        print(json.dumps(payload, default=str, separators=(",", ":")))
    else:
        # Caller composes a human line; payload is for the JSON branch.
        # We still log the structured event on stderr for consistency.
        _log_event("track_result", **payload)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@track_app.command("install")
def cmd_install(
    gpkg: Path = typer.Argument(
        ...,
        help="Path to the GeoPackage file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    layer: str | None = typer.Option(
        None,
        "--layer",
        "-l",
        help="Spatial layer to track. Mutually exclusive with --all-layers.",
    ),
    all_layers: bool = typer.Option(
        False,
        "--all-layers",
        help="Install change tracking on every spatial layer in gpkg_contents.",
    ),
    pk: str = typer.Option(
        "fid",
        "--pk",
        help="Primary key column name (default 'fid' per GPKG spec).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Install AFTER INSERT/UPDATE/DELETE triggers on one or all layers."""
    if (layer is None) == (not all_layers):
        _human(
            "Exactly one of --layer or --all-layers is required.",
            err=True,
            style="red",
        )
        raise typer.Exit(code=2)

    _maybe_warn_network_fs(gpkg)

    # Imported lazily so `gispulse --help` does not pull persistence.
    from persistence.gpkg_schema import bootstrap_gpkg_project, install_change_tracking

    conn = _open_gpkg(gpkg)
    try:
        bootstrap_gpkg_project(conn)
        existing = set(_list_spatial_layers(conn))

        if all_layers:
            targets = sorted(existing)
            if not targets:
                _human(
                    f"No spatial layers found in {gpkg}. "
                    "Add a layer first (e.g. via QGIS or ogr2ogr).",
                    err=True,
                    style="yellow",
                )
                raise typer.Exit(code=1)
        else:
            assert layer is not None  # Typer mutual-exclusion guard above
            if layer not in existing:
                _human(
                    f"Layer [bold]{layer}[/bold] not found in {gpkg}. "
                    f"Available: {', '.join(sorted(existing)) or '(none)'}",
                    err=True,
                    style="red",
                )
                raise typer.Exit(code=1)
            targets = [layer]

        installed: list[str] = []
        skipped: list[dict[str, str]] = []
        for tgt in targets:
            try:
                install_change_tracking(conn, tgt, pk_col=pk)
                installed.append(tgt)
                _log_event("track_installed", gpkg=str(gpkg), layer=tgt, pk=pk)
            except (ValueError, sqlite3.OperationalError) as exc:
                skipped.append({"layer": tgt, "reason": str(exc)})
                _log_event(
                    "track_install_skipped",
                    gpkg=str(gpkg),
                    layer=tgt,
                    reason=str(exc),
                )

        result = {
            "ok": True,
            "gpkg": str(gpkg),
            "installed": installed,
            "skipped": skipped,
            "pk": pk,
        }
        if as_json:
            print(json.dumps(result, separators=(",", ":")))
        else:
            _human(
                f"[green]✓[/green] Installed change tracking on {len(installed)} layer(s): "
                f"{', '.join(installed) or '—'}"
            )
            if skipped:
                _human(
                    f"[yellow]⚠[/yellow] Skipped {len(skipped)}: "
                    f"{', '.join(s['layer'] for s in skipped)}",
                    err=True,
                )
        if skipped and not installed:
            raise typer.Exit(code=1)
    finally:
        conn.close()


@track_app.command("uninstall")
def cmd_uninstall(
    gpkg: Path = typer.Argument(
        ...,
        help="Path to the GeoPackage file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    layer: str = typer.Option(
        ...,
        "--layer",
        "-l",
        help="Spatial layer to stop tracking.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Drop AFTER INSERT/UPDATE/DELETE triggers for a layer.

    The ``_gispulse_change_log`` table is left intact (audit trail). To wipe
    it as well, delete the rows manually or drop the table.
    """
    from persistence.gpkg_schema import uninstall_change_tracking

    conn = _open_gpkg(gpkg)
    try:
        uninstall_change_tracking(conn, layer)
        result = {"ok": True, "gpkg": str(gpkg), "uninstalled": layer}
        if as_json:
            print(json.dumps(result, separators=(",", ":")))
        else:
            _human(
                f"[green]✓[/green] Removed change-tracking triggers for layer "
                f"[bold]{layer}[/bold]."
            )
        _log_event("track_uninstalled", gpkg=str(gpkg), layer=layer)
    except ValueError as exc:
        _human(f"Invalid layer name: {exc}", err=True, style="red")
        raise typer.Exit(code=2) from exc
    finally:
        conn.close()


@track_app.command("list")
def cmd_list(
    gpkg: Path = typer.Argument(
        ...,
        help="Path to the GeoPackage file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show which layers have change-tracking installed (and pending row counts)."""
    conn = _open_gpkg(gpkg)
    try:
        installed = _installed_triggers(conn)
        spatial = set(_list_spatial_layers(conn))

        # Pending-change counts per layer (cheap aggregate; safe if table absent).
        pending: dict[str, int] = {}
        try:
            rows = conn.execute(
                "SELECT table_name, COUNT(*) AS n "
                "FROM _gispulse_change_log WHERE processed = 0 GROUP BY table_name"
            ).fetchall()
            pending = {r["table_name"]: r["n"] for r in rows}
        except sqlite3.OperationalError:
            pass

        layers_payload = []
        for name in sorted(set(installed) | spatial):
            ops = sorted(installed.get(name, []))
            layers_payload.append(
                {
                    "layer": name,
                    "tracked": bool(ops),
                    "ops": ops,
                    "complete": ops == ["delete", "insert", "update"],
                    "pending": pending.get(name, 0),
                    "spatial": name in spatial,
                }
            )

        result = {"ok": True, "gpkg": str(gpkg), "layers": layers_payload}
        if as_json:
            print(json.dumps(result, separators=(",", ":")))
            return

        # Human table
        try:
            from rich.console import Console
            from rich.table import Table

            table = Table(title=f"Change tracking — {gpkg.name}")
            table.add_column("Layer")
            table.add_column("Tracked", justify="center")
            table.add_column("Ops")
            table.add_column("Pending", justify="right")
            for row in layers_payload:
                mark = (
                    "[green]✓[/green]"
                    if row["complete"]
                    else "[yellow]partial[/yellow]"
                    if row["tracked"]
                    else "[dim]—[/dim]"
                )
                table.add_row(
                    row["layer"],
                    mark,
                    ",".join(row["ops"]) or "—",
                    str(row["pending"]),
                )
            Console().print(table)
        except ImportError:  # pragma: no cover
            for row in layers_payload:
                print(f"{row['layer']}\ttracked={row['tracked']}\tops={','.join(row['ops'])}\tpending={row['pending']}")
    finally:
        conn.close()


@track_app.command("tail")
def cmd_tail(
    gpkg: Path = typer.Argument(
        ...,
        help="Path to the GeoPackage file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        "-n",
        help="Maximum unprocessed rows to display.",
        min=1,
        max=10_000,
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Print the last N unprocessed rows of ``_gispulse_change_log`` (debug)."""
    conn = _open_gpkg(gpkg)
    try:
        try:
            rows = conn.execute(
                "SELECT id, table_name, operation, row_pk, changed_at "
                "FROM _gispulse_change_log WHERE processed = 0 "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            _human(
                f"_gispulse_change_log is missing — run "
                f"`gispulse track install {gpkg}` first.",
                err=True,
                style="red",
            )
            raise typer.Exit(code=1) from exc

        records = [dict(r) for r in rows]
        if as_json:
            print(json.dumps({"ok": True, "gpkg": str(gpkg), "rows": records}, default=str, separators=(",", ":")))
            return

        if not records:
            _human("[dim]no pending changes[/dim]")
            return

        try:
            from rich.console import Console
            from rich.table import Table

            table = Table(title=f"Pending changes — {gpkg.name} (latest {len(records)})")
            for col in ("id", "table_name", "operation", "row_pk", "changed_at"):
                table.add_column(col)
            for r in records:
                table.add_row(*(str(r[c]) for c in ("id", "table_name", "operation", "row_pk", "changed_at")))
            Console().print(table)
        except ImportError:  # pragma: no cover
            for r in records:
                print(f"{r['id']}\t{r['table_name']}\t{r['operation']}\t{r['row_pk']}\t{r['changed_at']}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# doctor (v1.3.0 #6) — verify trigger health, optionally auto-reinstall
# ---------------------------------------------------------------------------

# GPKG application_id per OGC spec (0x47504B47 = 'GPKG').
_GPKG_APP_ID = 1196444487
_STALE_PROCESSED_THRESHOLD_S = 24 * 3600


def _check_pragma(conn: sqlite3.Connection, name: str) -> str:
    """Return the value of a SQLite PRAGMA as a string ('' on error)."""
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
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_gispulse_change_log'"
    ).fetchone()
    return row is not None


def _stale_unprocessed(conn: sqlite3.Connection) -> tuple[int, str | None]:
    """Return (count, oldest_changed_at) for unprocessed rows older than 24 h."""
    if not _changelog_table_exists(conn):
        return (0, None)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MIN(changed_at) AS oldest "
            "FROM _gispulse_change_log "
            "WHERE processed = 0 "
            "AND (julianday('now') - julianday(changed_at)) * 86400 > ?",
            (_STALE_PROCESSED_THRESHOLD_S,),
        ).fetchone()
    except sqlite3.Error:
        return (0, None)
    if row is None:
        return (0, None)
    return (int(row["n"] or 0), row["oldest"])


@track_app.command("doctor")
def cmd_doctor(
    gpkg: Path = typer.Argument(
        ...,
        help="Path to the GeoPackage file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    auto_fix: bool = typer.Option(
        False,
        "--auto-fix",
        help="Reinstall missing triggers on every layer that has at least one trigger present.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Verify trigger health: app_id, WAL mode, busy timeout, trigger presence, stale rows.

    Exits 0 when all hard checks pass (warnings allowed). Exits 1 on any
    hard failure unless ``--auto-fix`` repaired it.
    """
    from persistence.gpkg_schema import install_change_tracking

    conn = _open_gpkg(gpkg)
    checks: list[dict[str, Any]] = []
    repaired: list[str] = []
    errors = 0

    def _add(name: str, status: str, detail: str = "", **extra: Any) -> None:
        # status ∈ {"ok", "warn", "fail", "fixed"}
        nonlocal errors
        if status == "fail":
            errors += 1
        checks.append({"check": name, "status": status, "detail": detail, **extra})

    try:
        # ---- 1. application_id ---------------------------------------
        app_id = _check_pragma(conn, "application_id")
        if app_id == str(_GPKG_APP_ID):
            _add("application_id", "ok", f"GPKG ({app_id})")
        else:
            _add(
                "application_id",
                "fail",
                f"expected {_GPKG_APP_ID} (GPKG), got {app_id or 'unset'}",
            )

        # ---- 2. _gispulse_change_log table ---------------------------
        if _changelog_table_exists(conn):
            _add("changelog_table", "ok", "_gispulse_change_log present")
        else:
            _add(
                "changelog_table",
                "fail",
                f"_gispulse_change_log missing — run `gispulse track install {gpkg}`",
            )

        # ---- 3. WAL mode (warn-only) ---------------------------------
        journal = _check_pragma(conn, "journal_mode").lower()
        if journal == "wal":
            _add("journal_mode", "ok", "wal")
        else:
            _add(
                "journal_mode",
                "warn",
                f"{journal!r} — concurrent writes from QGIS may block the watcher",
            )

        # ---- 4. busy_timeout (warn-only) -----------------------------
        try:
            busy = int(_check_pragma(conn, "busy_timeout") or "0")
        except ValueError:
            busy = 0
        if busy >= 5000:
            _add("busy_timeout", "ok", f"{busy} ms")
        else:
            _add(
                "busy_timeout",
                "warn",
                f"{busy} ms (< 5000 ms) — SQLITE_BUSY likely under contention",
            )

        # ---- 5. Trigger presence per layer ---------------------------
        spatial = set(_list_spatial_layers(conn))
        installed = _installed_triggers(conn)
        expected_ops = {"insert", "update", "delete"}

        # We treat "tracked" as: at least one trigger is present (so a
        # partially-dropped layer surfaces). Untracked layers are not
        # an error — the user may not want every layer tracked.
        for layer in sorted(set(installed) | spatial):
            ops = set(installed.get(layer, []))
            if not ops:
                _add(
                    "triggers",
                    "ok",
                    f"layer {layer!r} not tracked",
                    layer=layer,
                    missing=[],
                )
                continue
            missing = sorted(expected_ops - ops)
            if not missing:
                _add(
                    "triggers",
                    "ok",
                    f"layer {layer!r}: all 3 triggers present",
                    layer=layer,
                    missing=[],
                )
                continue

            if auto_fix:
                try:
                    install_change_tracking(conn, layer)
                    repaired.append(layer)
                    _add(
                        "triggers",
                        "fixed",
                        f"layer {layer!r}: reinstalled (was missing {missing})",
                        layer=layer,
                        missing=missing,
                    )
                    _log_event(
                        "doctor_reinstalled",
                        gpkg=str(gpkg),
                        layer=layer,
                        missing=missing,
                    )
                except (ValueError, sqlite3.OperationalError) as exc:
                    _add(
                        "triggers",
                        "fail",
                        f"layer {layer!r}: reinstall failed — {exc}",
                        layer=layer,
                        missing=missing,
                    )
            else:
                _add(
                    "triggers",
                    "fail",
                    f"layer {layer!r}: missing {missing} (run with --auto-fix)",
                    layer=layer,
                    missing=missing,
                )

        # ---- 6. Stale unprocessed rows (warn-only) -------------------
        stale_count, oldest = _stale_unprocessed(conn)
        if stale_count == 0:
            _add("stale_unprocessed", "ok", "no rows older than 24 h")
        else:
            _add(
                "stale_unprocessed",
                "warn",
                f"{stale_count} unprocessed row(s) older than 24 h "
                f"(oldest: {oldest}) — watcher dead?",
                count=stale_count,
                oldest=oldest,
            )

        result_status = "ok" if errors == 0 else "fail"
        result = {
            "ok": errors == 0,
            "gpkg": str(gpkg),
            "status": result_status,
            "errors": errors,
            "repaired": repaired,
            "checks": checks,
        }

        if as_json:
            print(json.dumps(result, default=str, separators=(",", ":")))
        else:
            try:
                from rich.console import Console
                from rich.table import Table

                table = Table(title=f"track doctor — {gpkg.name}")
                table.add_column("Check")
                table.add_column("Status", justify="center")
                table.add_column("Detail")
                for c in checks:
                    style = {
                        "ok": "[green]OK[/green]",
                        "warn": "[yellow]WARN[/yellow]",
                        "fail": "[red]FAIL[/red]",
                        "fixed": "[cyan]FIXED[/cyan]",
                    }[c["status"]]
                    table.add_row(c["check"], style, c["detail"])
                Console().print(table)
                if errors:
                    _human(
                        f"[red bold]{errors} hard failure(s).[/red bold] "
                        "Re-run with --auto-fix to repair trigger drift, "
                        "or `gispulse track install` for missing setup.",
                        err=True,
                    )
                elif repaired:
                    _human(
                        f"[cyan]Repaired {len(repaired)} layer(s):[/cyan] "
                        f"{', '.join(repaired)}"
                    )
                else:
                    _human("[green]All checks passed.[/green]")
            except ImportError:  # pragma: no cover
                for c in checks:
                    print(f"{c['check']}\t{c['status']}\t{c['detail']}")

        if errors:
            raise typer.Exit(1)
    finally:
        conn.close()
