"""``gispulse postgis ...`` CLI subapp — PostGIS lifecycle utilities.

Commands
--------
    gispulse postgis shed-columns [OPTIONS]

All outputs are machine-readable JSON (``{"error": code, "context": {...}}``
on stderr + exit 1 for gate failures; full ``ShedReport`` dict on stdout for
success).

Dry-run is the default safe mode; pass ``--apply`` to execute.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import typer

from gispulse.persistence.postgis_lifecycle import (
    ColumnShedProof,
    PostgisLifecycleError,
    shed_columns,
)

postgis_app = typer.Typer(
    name="postgis",
    help="PostGIS lifecycle utilities (column shed, etc.).",
    add_completion=False,
    no_args_is_help=True,
)


def _get_dsn(dsn_option: Optional[str]) -> str:
    if dsn_option:
        return dsn_option
    # Fallback: try the settings object (may not always be configured)
    try:
        from gispulse.config import settings  # type: ignore[attr-defined]
        dsn_from_settings = getattr(getattr(settings, "database", None), "dsn", None)
        if dsn_from_settings:
            return dsn_from_settings
    except Exception:
        pass
    typer.echo(
        json.dumps({"error": "missing_dsn", "context": {"help": "provide --dsn or set settings.database.dsn"}}),
        err=True,
    )
    raise typer.Exit(1)


@postgis_app.command("shed-columns")
def cmd_shed_columns(
    dsn: Optional[str] = typer.Option(
        None,
        "--dsn",
        help="PostGIS connection string (postgresql://...).  Falls back to settings.database.dsn.",
        envvar="GISPULSE_POSTGIS_DSN",
    ),
    schema: str = typer.Option(..., "--schema", help="Target table schema."),
    table: str = typer.Option(..., "--table", help="Target table name."),
    columns: List[str] = typer.Option(..., "--column", help="Column(s) to shed (repeat for multiple)."),
    partition_column: str = typer.Option(..., "--partition-column", help="Partition column name (e.g. dept)."),
    partition_values: List[str] = typer.Option(
        ..., "--partition-value", help="Partition value(s) to cover (repeat for multiple)."
    ),
    proof_path: Path = typer.Option(
        ...,
        "--proof",
        help=(
            'Path to proof JSON: {"partition_values": [...], "row_count": N, "checks": {...}}.'
        ),
        exists=True,
        readable=True,
    ),
    apply: bool = typer.Option(
        False, "--apply", is_flag=True,
        help="Execute the shed (default is dry-run — safe mode).",
    ),
    no_vacuum_full: bool = typer.Option(
        False, "--no-vacuum-full", is_flag=True,
        help="Skip VACUUM (FULL, ANALYZE) after drop.",
    ),
) -> None:
    """Shed heavy PostGIS columns after a verified export (dry-run by default).

    Output: JSON ShedReport on stdout.  Gate failures: JSON error on stderr, exit 1.
    """
    # Parse proof
    try:
        raw_proof = json.loads(proof_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        typer.echo(
            json.dumps({"error": "invalid_proof_file", "context": {"detail": str(exc)}}),
            err=True,
        )
        raise typer.Exit(1)

    try:
        proof = ColumnShedProof(
            partition_values=tuple(raw_proof["partition_values"]),
            row_count=int(raw_proof["row_count"]),
            checks=dict(raw_proof.get("checks", {})),
        )
    except (KeyError, TypeError, ValueError) as exc:
        typer.echo(
            json.dumps({"error": "invalid_proof_schema", "context": {"detail": str(exc)}}),
            err=True,
        )
        raise typer.Exit(1)

    resolved_dsn = _get_dsn(dsn)

    from gispulse.persistence.postgis_lifecycle import _connect_psycopg2

    try:
        conn = _connect_psycopg2(resolved_dsn)
    except Exception as exc:
        typer.echo(
            json.dumps({"error": "connection_failed", "context": {"detail": str(exc)}}),
            err=True,
        )
        raise typer.Exit(1)

    try:
        report = shed_columns(
            conn,
            schema=schema,
            table=table,
            columns=columns,
            partition_column=partition_column,
            partition_values=partition_values,
            proof=proof,
            dry_run=not apply,
            vacuum_full=not no_vacuum_full,
        )
    except PostgisLifecycleError as exc:
        typer.echo(
            json.dumps({"error": exc.code, "context": exc.context}),
            err=True,
        )
        raise typer.Exit(1)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    typer.echo(json.dumps(report.to_dict()))
