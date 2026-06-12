"""Storage diagnostics CLI."""

from __future__ import annotations

import json

import typer

from gispulse.persistence.storage import describe_storage_backend


storage_app = typer.Typer(
    help="Inspect explicit local vs S3/Garage storage backend selection.",
    add_completion=False,
)


@storage_app.command("status")
def storage_status(
    mode: str = typer.Option(
        "auto",
        "--mode",
        help="Storage mode to explain: auto, local, or s3.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Report which storage backend gispulse would use, without uploading data."""
    report = describe_storage_backend(mode)

    if json_output:
        typer.echo(json.dumps(report, sort_keys=True))
    else:
        typer.echo(f"requested_mode={report['requested_mode']}")
        typer.echo(f"backend={report['backend']}")
        typer.echo(f"storage_class={report['storage_class']}")
        typer.echo(f"endpoint_configured={report['endpoint_configured']}")
        typer.echo(f"bucket={report['bucket']}")
        typer.echo(f"fallback={report['fallback']}")
        typer.echo(f"selection_reason={report['selection_reason']}")
        if "error" in report:
            error = report["error"]
            if isinstance(error, dict):
                typer.echo(f"error_code={error.get('code')}", err=True)
                typer.echo(f"recovery={error.get('recovery')}", err=True)

    if report["backend"] == "unconfigured":
        raise typer.Exit(1)
