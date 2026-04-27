"""``gispulse triggers ...`` CLI subapp.

Three sub-commands (Mode 1 scope):

    gispulse triggers run --config FILE [--gpkg PATH] [--once | --watch]
    gispulse triggers validate --config FILE [--gpkg PATH]
    gispulse triggers list --gpkg PATH

Logging convention
------------------
- Human-friendly Rich output → stdout (UI surface).
- Structured JSON events     → stderr (machine-readable for log shippers).

Both ``--once`` (single tick then exit) and ``--watch`` (daemon loop with
SIGINT/SIGTERM handling, reload-on-config-change and tick-error backoff)
are supported. See ``cli_triggers_watch.py`` for the daemon implementation.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


triggers_app = typer.Typer(
    name="triggers",
    help="Run YAML-configured triggers against a GeoPackage (Mode 1, headless).",
    add_completion=False,
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _log_event(event: str, **fields: Any) -> None:
    """Emit a single-line JSON log record on stderr.

    Stays decoupled from the project's structlog config so the CLI works
    even when the user has not bootstrapped the structured logger.
    """
    record = {"event": event, **fields}
    try:
        line = json.dumps(record, default=str, separators=(",", ":"))
    except Exception:  # pragma: no cover - extreme defensive
        line = json.dumps({"event": event, "format_error": True})
    print(line, file=sys.stderr, flush=True)


def _human(message: str, *, err: bool = False, style: str | None = None) -> None:
    """Pretty-print to stdout (or stderr) using Rich if available."""
    try:
        from rich.console import Console

        console = Console(stderr=err)
        if style:
            console.print(f"[{style}]{message}[/{style}]")
        else:
            console.print(message)
    except ImportError:  # pragma: no cover - Rich is a hard dep, but be safe
        stream = sys.stderr if err else sys.stdout
        print(message, file=stream)


# ---------------------------------------------------------------------------
# Network FS detection (warn, do not refuse — see brief)
# ---------------------------------------------------------------------------


def _maybe_warn_network_fs(path: Path) -> None:
    """Emit a warning when ``path`` lives on a probable network filesystem.

    Heuristic only: GISPulse change-tracking relies on SQLite WAL +
    triggers, which are unsafe on NFS/SMB shares. Beta requested a hard
    refusal but we ship this as a warning first to gather telemetry on
    real-world false positives. A P0 follow-up will tighten this gate.
    """
    p = str(path).lower()
    network_signals = ("/mnt/", "/net/", "//", "/cifs", "/smb")
    if any(sig in p for sig in network_signals):
        msg = (
            f"GPKG path {path} looks like a network filesystem. SQLite WAL "
            "+ triggers are not safe on NFS / SMB shares. Copy the GPKG to "
            "a local disk before running triggers."
        )
        _log_event("network_fs_warning", path=str(path))
        warnings.warn(msg, RuntimeWarning, stacklevel=2)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@triggers_app.command("run")
def cmd_run(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="Path to the YAML triggers config file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    gpkg: Path | None = typer.Option(
        None,
        "--gpkg",
        help="Override the gpkg path declared in the config.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="Run a single tick then exit. Mutually exclusive with --watch.",
    ),
    watch: bool = typer.Option(
        False,
        "--watch",
        help="Run as a daemon: poll the change-log every "
        "``--poll-interval-ms`` until SIGINT/SIGTERM.",
    ),
    poll_interval_ms: int | None = typer.Option(
        None,
        "--poll-interval-ms",
        help="Override the YAML ``runtime.poll_interval_ms`` for daemon "
        "mode. CLI flag wins over YAML when both are set. Ignored under "
        "--once.",
        min=10,
        max=60_000,
    ),
) -> None:
    """Execute the trigger pipeline against a GPKG.

    Two modes:

    - ``--once``  — a single tick, exit 0. The watcher's daemon thread
                    is *not* started; the change-log is drained
                    synchronously then the runtime closes.
    - ``--watch`` — block on a tick → wait → tick loop until
                    SIGINT/SIGTERM. The YAML config is checked for
                    mtime changes every tick so an operator can edit
                    triggers without restarting the daemon. Broken
                    YAML is logged and ignored (the previous valid
                    config stays active).

    The flags are mutually exclusive; passing neither (or both) exits 2.
    """
    if once and watch:
        _human(
            "[!] --once and --watch are mutually exclusive.",
            err=True,
            style="yellow",
        )
        raise typer.Exit(2)
    if not once and not watch:
        _human(
            "[!] Pass either --once (single tick) or --watch (daemon).",
            err=True,
            style="yellow",
        )
        raise typer.Exit(2)

    # Lazy import — keeps `gispulse --help` fast even with the heavy
    # geopandas + duckdb stack underneath.
    from gispulse.runtime.config_loader import (
        ConfigError,
        load_config,
        to_triggers,
        validate_against_gpkg,
    )
    from gispulse.runtime.headless_runtime import build_runtime

    try:
        cfg = load_config(config, gpkg_override=gpkg)
    except ConfigError as exc:
        _log_event("config_error", error=str(exc))
        _human(f"[red]Config error:[/red] {exc}", err=True)
        raise typer.Exit(1)

    schema_errors = validate_against_gpkg(cfg)
    if schema_errors:
        for err in schema_errors:
            _log_event("schema_error", message=err)
            _human(f"[red]Schema error:[/red] {err}", err=True)
        raise typer.Exit(1)

    gpkg_path = Path(cfg.gpkg)
    _maybe_warn_network_fs(gpkg_path)

    triggers_obj = to_triggers(cfg)
    # Brief: "défaut 1000ms, surchargeable YAML > flag" — the flag, when
    # set, wins over the YAML value. The YAML value is itself optional
    # (defaults to 1000 ms in :class:`RuntimeConfigModel`).
    effective_poll_ms = (
        poll_interval_ms if poll_interval_ms is not None else cfg.runtime.poll_interval_ms
    )

    _log_event(
        "runtime_starting",
        gpkg=str(gpkg_path),
        triggers=len(triggers_obj),
        poll_interval_ms=effective_poll_ms,
        max_batch=cfg.runtime.max_batch,
        mode="watch" if watch else "once",
    )

    try:
        runtime = build_runtime(
            gpkg_path=gpkg_path,
            triggers=triggers_obj,
            webhook_allowlist=cfg.security.webhook_allowlist or None,
            poll_interval=effective_poll_ms / 1000.0,
            batch_limit=cfg.runtime.max_batch,
            dataset_id="__cli__",
        )
    except Exception as exc:
        _log_event("runtime_build_failed", error=str(exc))
        _human(f"[red]Runtime build failed:[/red] {exc}", err=True)
        raise typer.Exit(1)

    if watch:
        # Hand the runtime over to the daemon loop. Signal handlers
        # set ``stop_event``; the loop closes the runtime on its own
        # ``finally`` block.
        from gispulse.cli_triggers_watch import (
            install_signal_handlers,
            run_watch_loop,
        )
        import threading as _threading

        stop_event = _threading.Event()
        restore_signals = install_signal_handlers(stop_event)

        _human(
            f"[bold cyan]gispulse watch[/bold cyan] [green]ON[/green]  "
            f"gpkg=[bold]{gpkg_path.name}[/bold]  triggers="
            f"[bold]{len(triggers_obj)}[/bold]  "
            f"poll={effective_poll_ms}ms — Ctrl-C to stop."
        )

        try:
            exit_code = run_watch_loop(
                initial_runtime=runtime,
                initial_cfg=cfg,
                config_path=Path(config).resolve(),
                gpkg_override=gpkg,
                poll_interval=effective_poll_ms / 1000.0,
                stop_event=stop_event,
            )
        finally:
            restore_signals()

        if exit_code != 0:
            _human(
                "[red]Daemon aborted after consecutive tick failures.[/red]",
                err=True,
            )
            raise typer.Exit(exit_code)
        _human("[green]Daemon stopped cleanly.[/green]")
        return

    # ``--once`` path
    try:
        with runtime as rt:
            processed = rt.run_once()
    except Exception as exc:
        _log_event("tick_failed", error=str(exc))
        _human(f"[red]Tick failed:[/red] {exc}", err=True)
        raise typer.Exit(1)

    _log_event("tick_done", processed=processed)
    _human(
        f"[green]OK[/green] one tick processed [bold]{processed}[/bold] "
        f"change-log row(s) on [cyan]{gpkg_path.name}[/cyan]."
    )


@triggers_app.command("validate")
def cmd_validate(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="Path to the YAML triggers config file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    gpkg: Path | None = typer.Option(
        None,
        "--gpkg",
        help="Override the gpkg path declared in the config.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Validate the YAML config (syntax + schema + GPKG layer references).

    Exits 0 on success, 1 on any error.
    """
    from gispulse.runtime.config_loader import (
        ConfigError,
        load_config,
        validate_against_gpkg,
    )

    try:
        cfg = load_config(config, gpkg_override=gpkg)
    except ConfigError as exc:
        _log_event("config_error", error=str(exc))
        _human(f"[red]Config error:[/red] {exc}", err=True)
        raise typer.Exit(1)

    errors = validate_against_gpkg(cfg)
    if errors:
        for err in errors:
            _log_event("schema_error", message=err)
            _human(f"  [red]FAIL[/red]  {err}", err=True)
        _human("\nValidation failed.", err=True, style="red bold")
        raise typer.Exit(1)

    _log_event("validate_ok", triggers=len(cfg.triggers), gpkg=cfg.gpkg)
    _human(
        f"[green]OK[/green] {len(cfg.triggers)} trigger(s) valid against "
        f"[cyan]{Path(cfg.gpkg).name}[/cyan]."
    )


@triggers_app.command("list")
def cmd_list(
    gpkg: Path = typer.Option(
        ...,
        "--gpkg",
        help="GeoPackage to inspect.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """List the native SQLite triggers GISPulse has installed in the GPKG.

    These are the ``_gispulse_trg_<table>_<op>`` triggers wired by the
    engine when ``enable_change_tracking()`` runs. Empty list = the GPKG
    is not yet tracked.
    """
    try:
        conn = sqlite3.connect(str(gpkg))
        try:
            cur = conn.execute(
                """
                SELECT name, tbl_name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name LIKE '\\_gispulse\\_trg\\_%' ESCAPE '\\'
                ORDER BY tbl_name, name
                """,
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        _log_event("gpkg_open_failed", error=str(exc))
        _human(f"[red]Cannot open GPKG:[/red] {exc}", err=True)
        raise typer.Exit(1)

    if not rows:
        _human(
            "[yellow]No GISPulse triggers installed in this GPKG.[/yellow]\n"
            "Use the upload / enable-tracking endpoint or run a config that "
            "calls `engine.enable_change_tracking(...)`."
        )
        _log_event("list_empty", gpkg=str(gpkg))
        return

    # Group: one (table, [ops]) per row.
    by_table: dict[str, list[str]] = {}
    for name, tbl in rows:
        # name is `_gispulse_trg_<table>_<op>` — pull the op suffix.
        op = name.rsplit("_", 1)[-1].upper()
        by_table.setdefault(tbl, []).append(op)

    _human(f"[bold]{len(by_table)} tracked table(s) in {gpkg.name}:[/bold]\n")
    for tbl, ops in sorted(by_table.items()):
        ops_str = ", ".join(sorted(set(ops)))
        _human(f"  - [cyan]{tbl}[/cyan]: {ops_str}")

    _log_event("list_ok", gpkg=str(gpkg), tables=len(by_table))


__all__ = ["triggers_app"]
