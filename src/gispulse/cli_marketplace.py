"""``gispulse marketplace ...`` CLI subapp."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import typer
from pydantic import BaseModel, ConfigDict, Field

marketplace_app = typer.Typer(
    name="marketplace",
    help="Manage GISPulse capability plugins.",
    add_completion=False,
)

_PLUGIN_PREFIX = "gispulse-cap-"
_REGISTRY_URL = "https://raw.githubusercontent.com/gispulse/marketplace/main/registry.json"


class CommandResult(Protocol):
    """Minimal subprocess result surface consumed by this module."""

    returncode: int
    stderr: str


class CommandRunner(Protocol):
    """Callable subprocess runner contract, kept local to marketplace commands."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
    ) -> CommandResult:
        ...


class SimpleProject(BaseModel):
    """PyPI simple-index project entry."""

    model_config = ConfigDict(extra="ignore")

    name: str


class SimpleIndexPayload(BaseModel):
    """Subset of PyPI's simple-index JSON payload."""

    model_config = ConfigDict(extra="ignore")

    projects: list[SimpleProject] = Field(default_factory=list)


class RegistryPlugin(BaseModel):
    """Subset of the curated marketplace registry."""

    model_config = ConfigDict(extra="ignore")

    package: str
    name: str = ""
    description: str = ""


def _package_name(name: str) -> str:
    return name if name.startswith(_PLUGIN_PREFIX) else f"{_PLUGIN_PREFIX}{name}"


def _run_command(args: Sequence[str], runner: CommandRunner | None = None) -> CommandResult:
    import subprocess

    return (runner or subprocess.run)(args, capture_output=True, text=True)


@marketplace_app.command("list")
def marketplace_list() -> None:
    """List installed GISPulse plugins (entry-point based)."""
    from gispulse.capabilities.registry import list_plugins

    plugins = list_plugins()
    if not plugins:
        typer.echo("No plugins installed.")
        typer.echo("\nInstall one with:  gispulse marketplace install <name>")
        return

    typer.echo(f"{len(plugins)} plugin(s) installed:\n")
    for p in plugins:
        typer.echo(f"  - {p['name']}  ({p['module']})")


@marketplace_app.command("search")
def marketplace_search(
    query: str = typer.Argument(..., help="Search term (e.g. 'ftth', 'raster')."),
) -> None:
    """Search PyPI for GISPulse capability packages."""
    import json
    import urllib.request

    typer.echo(f"Searching PyPI for '{_PLUGIN_PREFIX}*' matching '{query}'...")

    try:
        req = urllib.request.Request(
            "https://pypi.org/simple/",
            headers={"Accept": "application/vnd.pypi.simple.v1+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = SimpleIndexPayload.model_validate(json.loads(resp.read()))
        matches = [
            project.name
            for project in data.projects
            if project.name.startswith(_PLUGIN_PREFIX) and query.lower() in project.name.lower()
        ]
    except Exception:
        matches = []
        try:
            req = urllib.request.Request(_REGISTRY_URL)
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw_registry = json.loads(resp.read())
            registry = [
                RegistryPlugin.model_validate(item)
                for item in raw_registry.get("plugins", [])
            ]
            matches = [
                plugin.package
                for plugin in registry
                if query.lower() in plugin.name.lower()
                or query.lower() in plugin.description.lower()
            ]
        except Exception:
            typer.echo("Error: could not reach PyPI or plugin registry.", err=True)
            raise typer.Exit(1)

    if not matches:
        typer.echo("No matching plugins found.")
        return

    typer.echo(f"\n{len(matches)} result(s):\n")
    for name in matches:
        typer.echo(f"  - {name}")
    typer.echo("\nInstall with:  gispulse marketplace install <name>")


@marketplace_app.command("install")
def marketplace_install(
    name: str = typer.Argument(
        ..., help="Plugin name (e.g. 'ftth'). Will install gispulse-cap-<name>."
    ),
    upgrade: bool = typer.Option(False, "--upgrade", "-U", help="Upgrade if already installed."),
) -> None:
    """Install a GISPulse capability plugin from PyPI."""
    import sys

    package = _package_name(name)
    cmd = [sys.executable, "-m", "pip", "install", package]
    if upgrade:
        cmd.append("--upgrade")

    typer.echo(f"Installing {package}...")
    result = _run_command(cmd)

    if result.returncode != 0:
        typer.echo(f"Error installing {package}:\n{result.stderr}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Installed {package} successfully.")
    typer.echo("Restart GISPulse to use the new capability.")


@marketplace_app.command("uninstall")
def marketplace_uninstall(
    name: str = typer.Argument(
        ..., help="Plugin name (e.g. 'ftth'). Will uninstall gispulse-cap-<name>."
    ),
) -> None:
    """Uninstall a GISPulse capability plugin."""
    import sys

    package = _package_name(name)

    typer.echo(f"Uninstalling {package}...")
    result = _run_command([sys.executable, "-m", "pip", "uninstall", "-y", package])

    if result.returncode != 0:
        typer.echo(f"Error uninstalling {package}:\n{result.stderr}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Uninstalled {package}.")


@marketplace_app.command("info")
def marketplace_info(
    name: str = typer.Argument(..., help="Plugin name (e.g. 'ftth')."),
) -> None:
    """Show details about an installed plugin."""
    from importlib.metadata import PackageNotFoundError

    package = _package_name(name)

    try:
        from importlib.metadata import metadata as pkg_metadata

        meta = pkg_metadata(package)
        typer.echo(f"Package:     {meta['Name']}")
        typer.echo(f"Version:     {meta['Version']}")
        typer.echo(f"Summary:     {meta.get('Summary', 'N/A')}")
        typer.echo(f"Author:      {meta.get('Author', meta.get('Author-email', 'N/A'))}")
        typer.echo(f"License:     {meta.get('License', 'N/A')}")
        typer.echo(f"Home-page:   {meta.get('Home-page', 'N/A')}")
    except PackageNotFoundError:
        typer.echo(f"Package '{package}' is not installed.", err=True)
        raise typer.Exit(1)


__all__ = ["marketplace_app"]
