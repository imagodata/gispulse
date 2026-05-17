"""``gispulse watch`` — foreground daemon for live DML → rule dispatch.

This is the long-running counterpart of ``gispulse triggers run --once``:
it starts the :class:`ChangeLogWatcher` polling thread, evaluates rules on
every batch, dispatches actions (webhook / SET_FIELD / RUN_SQL / …), and
runs until SIGINT / SIGTERM is received. On signal the watcher is stopped
gracefully (in-flight rows are acked before exit).

Suitable for ``systemd`` (Type=simple), Docker, or interactive shells.

Out of scope for v1.3.0 #5 (handled in sibling issues):
    * ``--bulk-threshold`` / ``--debounce-ms``  → #8
    * ``--watch-new-layers``                    → #6 (doctor / auto-reinstall)
    * One-shot ``gispulse run --once``          → #11
"""

from __future__ import annotations

import signal
import threading
import time
from pathlib import Path

import typer

from gispulse.cli_triggers import _human, _log_event, _maybe_warn_network_fs

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEARTBEAT_INTERVAL_S = 60.0


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def cmd_watch(
    gpkg: Path = typer.Argument(
        ...,
        help="Path to the GeoPackage file to watch.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    rules: Path = typer.Option(
        ...,
        "--rules",
        "-r",
        help="Path to the YAML triggers config file (same format as `triggers run --config`).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    webhook: list[str] = typer.Option(
        [],
        "--webhook",
        "-w",
        help="Allowlisted webhook host (repeatable). Adds to security.webhook_allowlist from YAML.",
    ),
    poll_ms: int | None = typer.Option(
        None,
        "--poll-ms",
        help="Override runtime.poll_interval_ms from YAML.",
        min=50,
        max=60_000,
    ),
    batch_limit: int | None = typer.Option(
        None,
        "--batch-limit",
        help="Override runtime.max_batch from YAML.",
        min=1,
        max=10_000,
    ),
    dataset_id: str = typer.Option(
        "__cli__",
        "--dataset-id",
        help="Stable identifier stamped on every event payload (multi-tenant disambiguation).",
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="Drain once and exit instead of looping. Suitable for cron / Lambda / CI hooks.",
    ),
    exit_zero_if_empty: bool = typer.Option(
        False,
        "--exit-zero-if-empty",
        help="(--once only) Silent exit 0 when the changelog is empty.",
    ),
    bulk_threshold: int = typer.Option(
        0,
        "--bulk-threshold",
        help="Collapse ticks with N+ rows into a single bulk.changed event. "
        "Avoids webhook flooding on ogr2ogr appends or QGIS bulk paste. "
        "0 (default) keeps per-row events.",
        min=0,
        max=1_000_000,
    ),
) -> None:
    """Watch a GeoPackage and dispatch rule actions on every DML change.

    Default: foreground daemon until SIGINT/SIGTERM. With ``--once``, drains
    one tick and exits 0 — suitable for cron jobs, AWS Lambda, or CI hooks.

    Trigger drift (a layer has its triggers dropped by ``ogr2ogr -overwrite``
    or ``VACUUM``) is reported but not auto-fixed in this version. Run
    ``gispulse track doctor <gpkg> --auto-fix`` first if you suspect drift.
    """
    # Lazy imports — keeps `gispulse --help` snappy.
    from gispulse.runtime.config_loader import (
        ConfigError,
        load_config,
        to_triggers,
        validate_against_gpkg,
    )
    from gispulse.runtime.headless_runtime import build_runtime

    # ---- Config ------------------------------------------------------
    try:
        cfg = load_config(rules, gpkg_override=gpkg)
    except ConfigError as exc:
        _log_event("config_error", error=str(exc))
        _human(f"[red]Config error:[/red] {exc}", err=True)
        raise typer.Exit(1) from exc

    schema_errors = validate_against_gpkg(cfg)
    if schema_errors:
        for err in schema_errors:
            _log_event("schema_error", message=err)
            _human(f"[red]Schema error:[/red] {err}", err=True)
        raise typer.Exit(1)

    gpkg_path = Path(cfg.gpkg)
    _maybe_warn_network_fs(gpkg_path)

    triggers_obj = to_triggers(cfg)

    # ---- Resolve runtime params (CLI overrides YAML) -----------------
    poll_interval_s = (
        (poll_ms or cfg.runtime.poll_interval_ms) / 1000.0
    )
    effective_batch = batch_limit or cfg.runtime.max_batch
    yaml_allowlist = list(cfg.security.webhook_allowlist or [])
    effective_allowlist = yaml_allowlist + [
        host.strip().lower() for host in webhook if host.strip()
    ]

    if exit_zero_if_empty and not once:
        _human(
            "--exit-zero-if-empty has no effect without --once; ignoring.",
            err=True,
            style="yellow",
        )

    mode = "once" if once else "daemon"
    _log_event(
        "watch_starting",
        gpkg=str(gpkg_path),
        rules=str(rules),
        triggers=len(triggers_obj),
        poll_interval_ms=int(poll_interval_s * 1000),
        batch_limit=effective_batch,
        bulk_threshold=bulk_threshold,
        webhook_allowlist=effective_allowlist or None,
        dataset_id=dataset_id,
        mode=mode,
    )
    if once:
        _human(
            f"[green]gispulse watch --once[/green] [cyan]{gpkg_path.name}[/cyan] — "
            f"{len(triggers_obj)} trigger(s), drain≤{effective_batch} row(s)."
        )
    else:
        _human(
            f"[green]gispulse watch[/green] [cyan]{gpkg_path.name}[/cyan] — "
            f"{len(triggers_obj)} trigger(s), poll={int(poll_interval_s*1000)}ms, "
            f"PID={_os_getpid()}. [dim]Ctrl+C to stop.[/dim]"
        )

    # ---- Build runtime ----------------------------------------------
    try:
        runtime = build_runtime(
            gpkg_path=gpkg_path,
            triggers=triggers_obj,
            webhook_allowlist=effective_allowlist or None,
            poll_interval=poll_interval_s,
            batch_limit=effective_batch,
            dataset_id=dataset_id,
            bulk_threshold=bulk_threshold,
        )
    except Exception as exc:
        _log_event("runtime_build_failed", error=str(exc))
        _human(f"[red]Runtime build failed:[/red] {exc}", err=True)
        raise typer.Exit(1) from exc

    # ---- One-shot drain mode ----------------------------------------
    if once:
        exit_code = _run_once(
            runtime=runtime,
            gpkg_path=gpkg_path,
            exit_zero_if_empty=exit_zero_if_empty,
        )
        raise typer.Exit(exit_code)

    # ---- Daemon mode (foreground until SIGINT/SIGTERM) ---------------
    stop_event = threading.Event()

    def _on_signal(signum: int, _frame: object) -> None:
        name = signal.Signals(signum).name if signum in [s.value for s in signal.Signals] else str(signum)
        _log_event("watch_signal_received", signal=name)
        stop_event.set()

    # Install handlers; SIGTERM is the one systemd sends, SIGINT is Ctrl+C.
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    exit_code = 0
    try:
        runtime.start()
        _log_event("watch_started", gpkg=str(gpkg_path))

        last_heartbeat = time.monotonic()
        last_change_id_seen = 0
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                pending, latest = _snapshot_changelog(runtime)
                _log_event(
                    "watch_heartbeat",
                    gpkg=str(gpkg_path),
                    pending=pending,
                    latest_change_id=latest,
                    delta_since_last=max(0, latest - last_change_id_seen),
                    running=runtime.watcher.is_running(),
                )
                last_heartbeat = now
                last_change_id_seen = latest
    except Exception as exc:  # pragma: no cover — defensive
        _log_event("watch_loop_error", error=str(exc))
        _human(f"[red]Watch loop error:[/red] {exc}", err=True)
        exit_code = 1
    finally:
        _human("[yellow]Stopping…[/yellow]", err=True)
        try:
            runtime.stop()
        except Exception as exc:  # pragma: no cover
            _log_event("watch_stop_error", error=str(exc))
        try:
            runtime.close()
        except Exception as exc:  # pragma: no cover
            _log_event("watch_close_error", error=str(exc))
        _log_event("watch_stopped", gpkg=str(gpkg_path), exit_code=exit_code)

    raise typer.Exit(exit_code)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _os_getpid() -> int:
    """Return the current process id (extracted to keep the command body tidy)."""
    import os
    return os.getpid()


def _run_once(
    *,
    runtime: object,
    gpkg_path: Path,
    exit_zero_if_empty: bool,
) -> int:
    """Single-tick drain — calls ``runtime.run_once()`` once and exits.

    Lifecycle:
        - No daemon thread, no signal handler, no heartbeat.
        - ``runtime.run_once()`` swallows per-row dispatch errors so a
          single bad webhook does not abort the batch (per
          ChangeLogWatcher contract).
        - Webhook 5xx leaves rows ``processed=0`` for the next invocation
          (idempotent retry on next cron tick).
        - Returns 0 on success, 1 on a build/tick exception, 0 (silent) on
          an empty changelog when ``exit_zero_if_empty`` is set.
    """
    try:
        pending_before, _ = _snapshot_changelog(runtime)
        if pending_before == 0 and exit_zero_if_empty:
            _log_event(
                "run_once_empty_silent_exit",
                gpkg=str(gpkg_path),
            )
            return 0

        processed = runtime.run_once()  # type: ignore[attr-defined]
        _log_event(
            "run_once_done",
            gpkg=str(gpkg_path),
            processed=processed,
            pending_before=pending_before,
        )
        if processed == 0:
            _human(
                f"[dim]Nothing to drain on {gpkg_path.name}.[/dim]"
            )
        else:
            _human(
                f"[green]✓[/green] Processed [bold]{processed}[/bold] "
                f"change-log row(s) on [cyan]{gpkg_path.name}[/cyan]."
            )
        return 0
    except Exception as exc:
        _log_event("run_once_failed", error=str(exc))
        _human(f"[red]run --once failed:[/red] {exc}", err=True)
        return 1
    finally:
        try:
            runtime.close()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover — defensive
            _log_event("run_once_close_error", error=str(exc))


def _snapshot_changelog(runtime: object) -> tuple[int, int]:
    """Return (pending_unprocessed_count, max_change_id) for the heartbeat log.

    Cheap aggregate via the engine's underlying SQLite connection. Failures
    are swallowed (heartbeat is best-effort observability, never blocks the
    watcher).
    """
    try:
        engine = runtime.engine  # type: ignore[attr-defined]
        conn = engine._get_conn()  # noqa: SLF001 — documented internal accessor
        row = conn.execute(
            "SELECT COUNT(*) FILTER (WHERE processed = 0) AS pending, "
            "COALESCE(MAX(id), 0) AS latest "
            "FROM _gispulse_change_log"
        ).fetchone()
        if row is None:
            return (0, 0)
        # sqlite3.Row supports both index and key access.
        try:
            return (int(row["pending"]), int(row["latest"]))
        except (KeyError, IndexError, TypeError):
            return (int(row[0]), int(row[1]))
    except Exception:
        return (0, 0)
