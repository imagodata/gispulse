"""``gispulse template ...`` CLI subapp."""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import BaseModel

template_app = typer.Typer(
    name="template",
    help="Manage and use GISPulse pipeline templates.",
    add_completion=False,
)

# Directory where built-in templates are stored relative to the repository root.
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
_TABLE_RULE = "\u2500"
_EMPTY_VALUE = "\u2014"


class TemplateEntry(BaseModel):
    """Metadata displayed by ``gispulse template list``."""

    name: str
    path: Path
    steps: int
    capabilities: list[str]


def _load_template_index() -> list[TemplateEntry]:
    """Return metadata for every .json template in the templates directory."""
    import json

    if not _TEMPLATES_DIR.exists():
        return []

    entries: list[TemplateEntry] = []
    for tpl_path in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(tpl_path.read_text(encoding="utf-8"))
            steps = len(data) if isinstance(data, list) else 1
            entries.append(
                TemplateEntry(
                    name=tpl_path.stem,
                    path=tpl_path,
                    steps=steps,
                    capabilities=(
                        sorted({r.get("capability", "?") for r in data})
                        if isinstance(data, list)
                        else []
                    ),
                )
            )
        except Exception:
            entries.append(
                TemplateEntry(name=tpl_path.stem, path=tpl_path, steps=0, capabilities=[])
            )

    return entries


@template_app.command("list")
def template_list() -> None:
    """List available built-in pipeline templates."""
    entries = _load_template_index()

    if not entries:
        typer.echo(
            f"No templates found in {_TEMPLATES_DIR}. "
            "Re-install GISPulse or run from the project root."
        )
        return

    typer.echo(f"Available templates ({len(entries)}):\n")
    typer.echo(f"  {'Name':<35} {'Steps':>5}  Capabilities")
    typer.echo(f"  {_TABLE_RULE * 35} {_TABLE_RULE * 5}  {_TABLE_RULE * 40}")
    for entry in entries:
        caps = ", ".join(entry.capabilities) or _EMPTY_VALUE
        typer.echo(f"  {entry.name:<35} {entry.steps:>5}  {caps}")

    typer.echo("\nUse: gispulse template use <name> [-o <dest>]")


@template_app.command("use")
def template_use(
    name: str = typer.Argument(..., help="Template name (without .json extension)."),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination path (default: <name>.json in the current directory).",
    ),
) -> None:
    """Copy a built-in pipeline template to the current directory (or a given path).

    Example::

        gispulse template use validation_plu_cnig
        gispulse template use ftth_network_analysis -o rules/ftth.json
    """
    import shutil

    tpl_path = _TEMPLATES_DIR / f"{name}.json"
    if not tpl_path.exists():
        tpl_path = _TEMPLATES_DIR / name
    if not tpl_path.exists():
        available = [entry.name for entry in _load_template_index()]
        typer.echo(
            f"Error: template '{name}' not found.\n"
            f"Available: {', '.join(available) or 'none'}",
            err=True,
        )
        raise typer.Exit(1)

    dest = output or Path.cwd() / tpl_path.name
    if dest.exists():
        typer.echo(f"Warning: {dest} already exists \u2014 overwriting.", err=True)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tpl_path, dest)
    typer.echo(f"Template '{name}' copied to {dest}")
    typer.echo(f"Edit the file then run: gispulse run <input> --rules {dest} -o output.gpkg")


@template_app.command("workflow")
def workflow_execute(
    name: str = typer.Argument(..., help="Workflow name (ex: ftth_network_analysis)"),
    input_path: Path = typer.Argument(..., help="Chemin vers le GPKG d'entr\u00e9e"),
    output_path: Path | None = typer.Option(None, "--output", "-o", help="Chemin de sortie (GPKG)"),
) -> None:
    """Execute a built-in workflow such as ftth_network_analysis.

    Example::

        gispulse template workflow ftth_network_analysis input.gpkg -o output.gpkg
    """
    from gispulse.persistence.gpkg import read_gpkg
    from gispulse.workflows.ftth_network_analysis import FTTHNetworkAnalysisWorkflow

    dataset = read_gpkg(input_path)
    template_path = _TEMPLATES_DIR / f"{name}.json"

    if not template_path.exists():
        typer.echo(f"Error: workflow template '{name}' not found.", err=True)
        raise typer.Exit(1)

    workflow = FTTHNetworkAnalysisWorkflow(template_path, dataset)
    result = workflow.run(output_path)
    typer.echo(f"Workflow '{name}' termin\u00e9. R\u00e9sultat: {len(result.layers)} couches.")


__all__ = ["template_app"]
