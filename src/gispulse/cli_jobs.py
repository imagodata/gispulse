"""``gispulse jobs ...`` CLI subapp."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
import typer

jobs_app = typer.Typer(
    name="jobs",
    help="Manage GISPulse jobs (list, status, cancel).",
    add_completion=False,
)


class JobsHttpClient(Protocol):
    """Minimal client surface used by the jobs CLI commands."""

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        ...

    def post(self, path: str) -> Any:
        ...


class JobRecord(BaseModel):
    """Normalized job payload returned by the HTTP API."""

    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    name: str = ""
    dataset_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    result_path: str | None = None
    attempts: int = 0


class JobListPayload(BaseModel):
    """Collection wrapper for both list and object-style API responses."""

    model_config = ConfigDict(extra="allow")

    items: list[JobRecord] = Field(default_factory=list)


def _jobs_from_payload(data: object) -> list[JobRecord]:
    if isinstance(data, list):
        return [JobRecord.model_validate(item) for item in data]
    return JobListPayload.model_validate(data).items


def _jobs_http(host: str, api_key: str | None) -> AbstractContextManager[JobsHttpClient]:
    """Return an httpx.Client configured for the given host."""
    import httpx

    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    return httpx.Client(base_url=host.rstrip("/"), headers=headers, timeout=10.0)


@jobs_app.command("list")
def jobs_list(
    host: str = typer.Option(
        "http://localhost:8001",
        "--host",
        "-H",
        help="GISPulse API base URL.",
        envvar="GISPULSE_HOST",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="API key for authentication.",
        envvar="GISPULSE_API_KEY",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum number of jobs to show."),
) -> None:
    """List recent jobs."""
    import httpx

    with _jobs_http(host, api_key) as http:
        try:
            resp = http.get("/jobs", params={"limit": limit})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            typer.echo(f"Error {exc.response.status_code}: {exc.response.text}", err=True)
            raise typer.Exit(1)
        except httpx.RequestError as exc:
            typer.echo(f"Connection error: {exc}", err=True)
            raise typer.Exit(1)

    items = _jobs_from_payload(resp.json())

    if not items:
        typer.echo("No jobs found.")
        return

    header = f"{'ID':<38}  {'STATUS':<10}  {'NAME':<24}  {'ATTEMPTS':>8}  {'DURATION':>10}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for job in items:
        duration = job.duration_seconds
        dur_str = f"{duration:.1f}s" if duration is not None else "\u2014"
        typer.echo(
            f"{job.id:<38}  {job.status:<10}  {job.name:<24}"
            f"  {job.attempts:>8}  {dur_str:>10}"
        )


@jobs_app.command("status")
def jobs_status(
    job_id: str = typer.Argument(..., help="Job UUID."),
    host: str = typer.Option(
        "http://localhost:8001",
        "--host",
        "-H",
        envvar="GISPULSE_HOST",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="GISPULSE_API_KEY",
    ),
) -> None:
    """Show detailed status of a job."""
    import httpx

    with _jobs_http(host, api_key) as http:
        try:
            resp = http.get(f"/jobs/{job_id}")
            if resp.status_code == 404:
                typer.echo(f"Job '{job_id}' not found.", err=True)
                raise typer.Exit(1)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            typer.echo(f"Error {exc.response.status_code}: {exc.response.text}", err=True)
            raise typer.Exit(1)
        except httpx.RequestError as exc:
            typer.echo(f"Connection error: {exc}", err=True)
            raise typer.Exit(1)

    job = JobRecord.model_validate(resp.json())
    typer.echo(f"ID:        {job.id}")
    typer.echo(f"Name:      {job.name}")
    typer.echo(f"Status:    {job.status}")
    typer.echo(f"Attempts:  {job.attempts}")
    if job.dataset_id:
        typer.echo(f"Dataset:   {job.dataset_id}")
    if job.started_at:
        typer.echo(f"Started:   {job.started_at}")
    if job.completed_at:
        typer.echo(f"Completed: {job.completed_at}")
    if job.duration_seconds is not None:
        typer.echo(f"Duration:  {job.duration_seconds:.1f}s")
    if job.error_message:
        typer.echo(f"Error:     {job.error_message}")
    if job.result_path:
        typer.echo(f"Result:    {job.result_path}")


@jobs_app.command("cancel")
def jobs_cancel(
    job_id: str = typer.Argument(..., help="Job UUID."),
    host: str = typer.Option(
        "http://localhost:8001",
        "--host",
        "-H",
        envvar="GISPULSE_HOST",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="GISPULSE_API_KEY",
    ),
) -> None:
    """Cancel a pending or running job."""
    import httpx

    with _jobs_http(host, api_key) as http:
        try:
            resp = http.post(f"/jobs/{job_id}/cancel")
            if resp.status_code == 404:
                typer.echo(f"Job '{job_id}' not found.", err=True)
                raise typer.Exit(1)
            if resp.status_code == 409:
                detail = resp.json().get("detail", "")
                typer.echo(f"Cannot cancel: {detail}", err=True)
                raise typer.Exit(1)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            typer.echo(f"Error {exc.response.status_code}: {exc.response.text}", err=True)
            raise typer.Exit(1)
        except httpx.RequestError as exc:
            typer.echo(f"Connection error: {exc}", err=True)
            raise typer.Exit(1)

    job = JobRecord.model_validate(resp.json())
    typer.echo(f"Job '{job_id}' cancelled (status: {job.status}).")


__all__ = ["jobs_app"]
