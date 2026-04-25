"""
GISPulse CLI — headless geospatial engine with rules.

Usage::

    gispulse init                                   # scaffold a project
    gispulse run input.gpkg -r rules.json -o out.gpkg  # run a pipeline
    gispulse validate rules.json                    # dry-run validation
    gispulse info data.gpkg                         # inspect file metadata
    gispulse layers data.gpkg                       # list layers
    gispulse serve data.gpkg                        # launch viewer
    gispulse portal                                 # launch web portal
    gispulse formats                                # list supported formats
    gispulse capabilities                           # list capabilities
    gispulse doctor                                 # check environment health
    gispulse update [--check] [--force]             # check for updates / self-update
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional, List, Union, Dict

import typer

if TYPE_CHECKING:
    from packaging.version import Version

app = typer.Typer(
    name="gispulse",
    help="GISPulse — moteur geospatial modulaire avec regles metier.",
    add_completion=False,
)


@app.command()
def init(
    directory: Path = typer.Argument(
        ".", help="Directory to initialize (default: current directory)."
    ),
    name: str = typer.Option(None, "--name", "-n", help="Project name (default: directory name)."),
) -> None:
    """Scaffold a new GISPulse project with template rules and config."""
    import json

    project_dir = directory.resolve()
    project_name = name or project_dir.name

    # Create directory structure
    rules_dir = project_dir / "rules"
    data_dir = project_dir / "data"
    output_dir = project_dir / "output"

    for d in (rules_dir, data_dir, output_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Create template rules file
    template_rules = [
        {
            "name": "filter_example",
            "description": "Filter features by attribute (edit the expression)",
            "capability": "filter",
            "config": {"expression": "True", "order": 0},
            "enabled": True,
        },
        {
            "name": "buffer_100m",
            "description": "Apply a 100m buffer around all features",
            "capability": "buffer",
            "config": {"distance": 100, "order": 1},
            "enabled": False,
        },
    ]

    rules_file = rules_dir / "rules.json"
    if not rules_file.exists():
        rules_file.write_text(json.dumps(template_rules, indent=2, ensure_ascii=False))

    # Create Makefile
    makefile = project_dir / "Makefile"
    if not makefile.exists():
        makefile.write_text(
            f"# {project_name} — GISPulse project\n"
            f"\n"
            f"INPUT  ?= data/input.gpkg\n"
            f"RULES  ?= rules/rules.json\n"
            f"OUTPUT ?= output/result.gpkg\n"
            f"\n"
            f"run:\n"
            f"\tgispulse run $(INPUT) --rules $(RULES) -o $(OUTPUT)\n"
            f"\n"
            f"validate:\n"
            f"\tgispulse validate $(RULES)\n"
            f"\n"
            f"view:\n"
            f"\tgispulse serve $(OUTPUT)\n"
            f"\n"
            f"clean:\n"
            f"\trm -f output/*\n"
        )

    typer.echo(f"Initialized GISPulse project: {project_name}")
    typer.echo(f"  {rules_dir.relative_to(project_dir)}/rules.json  — rule template")
    typer.echo(f"  {data_dir.relative_to(project_dir)}/             — put your data here")
    typer.echo(f"  {output_dir.relative_to(project_dir)}/           — results go here")
    typer.echo("\nNext steps:")
    typer.echo("  1. Copy your spatial file to data/")
    typer.echo("  2. Edit rules/rules.json")
    typer.echo("  3. gispulse run data/myfile.gpkg --rules rules/rules.json -o output/result.gpkg")


@app.command()
def run(
    input_file: Path = typer.Argument(..., help="Input spatial file (GPKG, GeoJSON, Shapefile, FlatGeobuf, CSV, Parquet, ...).",),
    rules: Path = typer.Option(..., "--rules", "-r", help="JSON rules file path."),
    output: Path = typer.Option(..., "--output", "-o", help="Output spatial file path (format detected from extension)."),
    layer: str | None = typer.Option(None, "--layer", "-l", help="Layer name to process (for multi-layer formats, default: first layer)."),
    output_layer: str | None = typer.Option(None, "--output-layer", help="Layer name in output (for multi-layer formats like GPKG)."),
    all_layers: bool = typer.Option(False, "--all-layers", "-A", help="Process ALL layers (multi-layer formats). Copies styles if present."),
    crs: str | None = typer.Option(None, "--crs", help="Force input CRS (e.g. EPSG:4326) when file has none."),
    ref_source: list[str] | None = typer.Option(None, "--ref-source", help="External ref layer as NAME:PATH (repeatable)."),
    engine: str = typer.Option("python", "--engine", "-e", help="Execution engine: 'python' (default) or 'duckdb' (accelerated)."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging."),
) -> None:
    """Run a rules pipeline on a spatial file."""
    import structlog

    if verbose:
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(10),  # DEBUG
        )

    # Lazy imports to keep CLI startup fast
    from persistence.io import detect_format, WRITABLE_FORMATS
    from rules.loader import load_rules

    # Validate engine choice
    if engine not in ("python", "duckdb"):
        typer.echo(f"Error: unknown engine '{engine}'. Use 'python' or 'duckdb'.", err=True)
        raise typer.Exit(1)

    # Validate inputs
    if not input_file.exists():
        typer.echo(f"Error: input file not found: {input_file}", err=True)
        raise typer.Exit(1)
    if not rules.exists():
        typer.echo(f"Error: rules file not found: {rules}", err=True)
        raise typer.Exit(1)

    # Validate formats
    in_driver = detect_format(str(input_file))
    if in_driver is None:
        typer.echo(f"Error: unsupported input format '{input_file.suffix}'.", err=True)
        raise typer.Exit(1)

    out_ext = output.suffix.lower()
    if out_ext not in WRITABLE_FORMATS:
        typer.echo(
            f"Error: cannot write to '{out_ext}'. Writable: {sorted(WRITABLE_FORMATS)}",
            err=True,
        )
        raise typer.Exit(1)

    # Parse --ref-source NAME:PATH entries
    ref_sources: dict[str, Path] = {}
    if ref_source:
        for entry in ref_source:
            if ":" not in entry:
                typer.echo(
                    f"Error: --ref-source must be NAME:PATH, got '{entry}'.",
                    err=True,
                )
                raise typer.Exit(1)
            name, path_str = entry.split(":", 1)
            ref_path = Path(path_str)
            if not ref_path.exists():
                typer.echo(f"Error: ref-source file not found: {ref_path}", err=True)
                raise typer.Exit(1)
            ref_sources[name] = ref_path

    # Load pipeline (v2) or rules (v1)
    from core.pipeline import load_pipeline

    try:
        pipeline_spec = load_pipeline(rules)
    except Exception as e:
        typer.echo(f"Error loading pipeline/rules: {e}", err=True)
        raise typer.Exit(1)

    if not pipeline_spec.steps:
        typer.echo("Warning: no steps/rules found in pipeline file.", err=True)

    # Inject --ref-source entries into pipeline ref_layers
    for name, rpath in ref_sources.items():
        pipeline_spec.ref_layers[name] = str(rpath)

    is_v2 = pipeline_spec.version == 2

    label = f"pipeline '{pipeline_spec.name}'" if pipeline_spec.name else "rules"
    typer.echo(f"Loading {input_file} ({in_driver}) [engine: {engine}, {label}] ...")

    # Display steps/rules
    if is_v2:
        for s in pipeline_spec.enabled_steps:
            ref = s.params.get("ref_layer", "")
            suffix = f" (ref: {ref})" if ref else ""
            typer.echo(f"  [{s.capability or s.type}] {s.id}{suffix}")
    else:
        from rules.loader import load_rules
        rule_list = load_rules(rules)
        for r in sorted([r for r in rule_list if r.enabled], key=lambda r: r.order):
            ref = r.config.get("ref_layer", "")
            suffix = f" (ref: {ref})" if ref else ""
            typer.echo(f"  [{r.capability}] {r.name}{suffix}")

    # Run via SessionManager
    from orchestration.session_manager import SessionManager

    sm = SessionManager(engine=engine)

    if is_v2:
        # v2: delegate directly to PipelineExecutor (no Rule conversion)
        result = sm.run_pipeline_v2(
            input_path=input_file,
            spec=pipeline_spec,
            output_path=output,
            layer=layer,
            output_layer=output_layer,
            crs=crs,
            ref_sources=ref_sources or None,
        )

        typer.echo(f"  {result.features_in} features in -> {result.features_out} features out")
        typer.echo(f"  {result.rules_applied} step(s) executed [engine: {result.engine_used}]")
        typer.echo(f"Output written to {output} ({out_ext})")
    elif all_layers:
        # v1 multi-layer mode: process every layer, copy styles
        multi_result = sm.run_pipeline_multi(
            input_path=input_file,
            rules=rule_list,
            output_path=output,
            crs=crs,
            copy_styles=True,
        )
        for lname, lr in multi_result.layer_results.items():
            typer.echo(f"  [{lname}] {lr.features_in} -> {lr.features_out} features, {lr.rules_applied} rule(s)")
        typer.echo(f"  Total: {multi_result.total_features_in} in -> {multi_result.total_features_out} out")
        typer.echo(f"  {multi_result.rules_applied} rule(s) applied [engine: {multi_result.engine_used}]")
        if multi_result.styles_copied:
            typer.echo(f"  {multi_result.styles_copied} style(s) copied")
        typer.echo(f"Output written to {output} ({out_ext})")
    else:
        # v1 single-layer mode
        result = sm.run_pipeline(
            input_path=input_file,
            rules=rule_list,
            output_path=output,
            layer=layer,
            output_layer=output_layer,
            crs=crs,
            ref_sources=ref_sources or None,
        )

        typer.echo(f"  {result.features_in} features in -> {result.features_out} features out")
        typer.echo(f"  {result.rules_applied} rule(s) applied [engine: {result.engine_used}]")
        typer.echo(f"Output written to {output} ({out_ext})")


@app.command()
def layers(
    input_file: Path = typer.Argument(..., help="Spatial file path (GPKG, GDB, SQLite, GeoJSON, Shapefile, ...)."),
) -> None:
    """List layers in a spatial file."""
    from persistence.io import detect_format, list_layers

    if not input_file.exists():
        typer.echo(f"Error: file not found: {input_file}", err=True)
        raise typer.Exit(1)

    driver = detect_format(str(input_file))
    if driver is None:
        typer.echo(f"Error: unsupported format '{input_file.suffix}'.", err=True)
        raise typer.Exit(1)

    layer_names = list_layers(str(input_file))
    if not layer_names or layer_names == [""]:
        typer.echo(f"Single-layer format ({driver}), no named layers.")
        return
    typer.echo(f"{len(layer_names)} layer(s):")
    for name in layer_names:
        typer.echo(f"  - {name}")


@app.command()
def formats() -> None:
    """List supported input/output formats."""
    from persistence.io import VECTOR_DRIVERS, WRITABLE_FORMATS

    typer.echo("Supported formats:")
    typer.echo("")
    typer.echo(f"  {'Extension':<12} {'Driver':<20} {'Read':>5} {'Write':>5}")
    typer.echo(f"  {'─' * 12} {'─' * 20} {'─' * 5} {'─' * 5}")
    for ext in sorted(VECTOR_DRIVERS):
        driver = VECTOR_DRIVERS[ext]
        writable = "yes" if ext in WRITABLE_FORMATS else "no"
        typer.echo(f"  {ext:<12} {driver:<20} {'yes':>5} {writable:>5}")

    try:
        from persistence.raster_io import RASTER_DRIVERS, RASTER_WRITABLE
        typer.echo("")
        typer.echo("Raster formats:")
        typer.echo("")
        typer.echo(f"  {'Extension':<12} {'Driver':<20} {'Read':>5} {'Write':>5}")
        typer.echo(f"  {'─' * 12} {'─' * 20} {'─' * 5} {'─' * 5}")
        for ext in sorted(RASTER_DRIVERS):
            driver = RASTER_DRIVERS[ext]
            writable = "yes" if ext in RASTER_WRITABLE else "no"
            typer.echo(f"  {ext:<12} {driver:<20} {'yes':>5} {writable:>5}")
    except ImportError:
        pass


@app.command()
def serve(
    input_file: Path = typer.Argument(..., help="Spatial file to view (GPKG, GeoJSON, Shapefile, ...)."),
    port: int = typer.Option(8765, "--port", "-p", help="Port to listen on."),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to."),
    dev: bool = typer.Option(False, "--dev", help="Dev mode: API only, no static files (use with Vite dev server)."),
) -> None:
    """Launch the embedded viewer for a spatial file (read-only)."""
    from persistence.io import detect_format

    if not input_file.exists():
        typer.echo(f"Error: file not found: {input_file}", err=True)
        raise typer.Exit(1)

    driver = detect_format(str(input_file))
    if driver is None:
        typer.echo(f"Error: unsupported format '{input_file.suffix}'.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loading {input_file} ({driver}) ...")

    from gispulse.adapters.http.serve_app import create_serve_app, _VIEWER_DIST

    # In dev mode, don't mount static files
    static_dir = None if dev else _VIEWER_DIST
    app = create_serve_app(str(input_file), static_dir=static_dir)

    if dev:
        typer.echo(f"Dev mode: API at http://{host}:{port}/v1/viewer/layers")
        typer.echo("Run 'cd viewer && npm run dev' for the frontend.")
    elif not _VIEWER_DIST.exists():
        typer.echo("Warning: viewer/dist/ not found. Run 'cd viewer && npm run build' first.")
        typer.echo(f"API-only at http://{host}:{port}/v1/viewer/layers")
    else:
        typer.echo(f"Viewer at http://{host}:{port}")

    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")


@app.command()
def portal(
    port: int = typer.Option(8001, "--port", "-p", help="Port to listen on."),
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind to (0.0.0.0 = LAN accessible)."),
    data_dir: str = typer.Option("~/.gispulse/data", "--data-dir", "-d", help="Directory for uploaded datasets."),
    dev: bool = typer.Option(False, "--dev", help="Dev mode: API only, no static files (use with Vite dev server)."),
) -> None:
    """Launch the GISPulse Portal — visual pipeline editor and dataset manager."""
    import webbrowser

    from gispulse.adapters.http.portal_app import create_portal_app, _PORTAL_DIST

    static_dir = None if dev else _PORTAL_DIST
    portal_app = create_portal_app(data_dir=data_dir, static_dir=static_dir)

    if dev:
        typer.echo(f"Portal API at http://{host}:{port}/api/portal/datasets")
        typer.echo("Run 'cd portal && npm run dev' for the frontend.")
    elif not _PORTAL_DIST.exists():
        typer.echo("Warning: portal/dist/ not found. Run 'cd portal && npm run build' first.")
        typer.echo(f"API-only at http://{host}:{port}/api/portal/datasets")
    else:
        url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
        typer.echo(f"GISPulse Portal at {url}")
        webbrowser.open(url)

    import uvicorn
    uvicorn.run(portal_app, host=host, port=port, log_level="info")


@app.command()
def validate(
    rules_file: Path = typer.Argument(..., help="JSON rules file to validate."),
) -> None:
    """Validate rules without executing them (dry-run)."""
    from rules.loader import load_rules
    from rules.validation import validate_rules_batch

    if not rules_file.exists():
        typer.echo(f"Error: rules file not found: {rules_file}", err=True)
        raise typer.Exit(1)

    try:
        rule_list = load_rules(rules_file)
    except Exception as e:
        typer.echo(f"Error loading rules: {e}", err=True)
        raise typer.Exit(1)

    if not rule_list:
        typer.echo("Warning: no rules found in file.", err=True)
        raise typer.Exit(0)

    results = validate_rules_batch(rule_list)
    id_to_name = {str(r.id): r.name or str(r.id) for r in rule_list}
    all_valid = True

    for rule_id, result in results.items():
        label = id_to_name.get(rule_id, rule_id)
        if result.valid:
            typer.echo(f"  OK  {label}")
        else:
            all_valid = False
            typer.echo(f"  FAIL  {label}")
            for err in result.errors:
                typer.echo(f"        - [{err.field}] {err.message}")

    if all_valid:
        typer.echo(f"\n{len(rule_list)} rule(s) valid.")
    else:
        typer.echo("\nValidation failed.", err=True)
        raise typer.Exit(1)


@app.command()
def capabilities() -> None:
    """List available capabilities."""
    from capabilities.registry import list_all

    caps = list_all()
    typer.echo(f"{len(caps)} capability(ies):")
    for cap in caps:
        schema = cap.get("schema", {})
        params = list(schema.get("properties", {}).keys())
        param_str = f" ({', '.join(params)})" if params else ""
        typer.echo(f"  - {cap['name']}{param_str}: {cap['description']}")


@app.command()
def info(
    input_file: Path = typer.Argument(..., help="Spatial file to inspect."),
) -> None:
    """Show metadata for a spatial file (layers, CRS, feature count, format)."""
    from persistence.io import dataset_from_file, detect_format

    if not input_file.exists():
        typer.echo(f"Error: file not found: {input_file}", err=True)
        raise typer.Exit(1)

    driver = detect_format(str(input_file))
    if driver is None:
        typer.echo(f"Error: unsupported format '{input_file.suffix}'.", err=True)
        raise typer.Exit(1)

    ds = dataset_from_file(str(input_file))
    size_mb = input_file.stat().st_size / (1024 * 1024)

    typer.echo(f"File:     {input_file}")
    typer.echo(f"Format:   {ds.format}")
    typer.echo(f"Size:     {size_mb:.2f} MB")
    typer.echo(f"CRS:      {ds.crs}")
    typer.echo(f"Category: {ds.data_category}")

    layers = ds.metadata.get("layers", [])
    if layers:
        typer.echo(f"\n{len(layers)} layer(s):")
        for layer in layers:
            fc = layer.get("feature_count", "?")
            gt = layer.get("geometry_type", "?")
            crs = layer.get("crs", "?")
            typer.echo(f"  - {layer['name']}: {fc} features, {gt}, {crs}")

    # Show styles info for GPKG files
    if input_file.suffix.lower() == ".gpkg":
        from persistence.gpkg import read_styles
        styles = read_styles(str(input_file))
        if styles:
            typer.echo(f"\n{len(styles)} style(s):")
            for s in styles:
                has_qml = "QML" if s.get("styleQML") else ""
                has_sld = "SLD" if s.get("styleSLD") else ""
                fmt = " + ".join(filter(None, [has_qml, has_sld])) or "empty"
                typer.echo(f"  - {s['f_table_name']}/{s.get('styleName', '?')} ({fmt})")


@app.command()
def doctor() -> None:
    """Run system diagnostics and check environment health."""
    import os
    import platform
    import shutil
    import sys

    has_critical = False
    results: list[tuple[str, str, str]] = []  # (status_icon, check_name, detail)

    # --- 1. GISPulse version ---
    try:
        from importlib.metadata import version as pkg_version

        gp_version = pkg_version("gispulse")
        results.append(("\u2713", "GISPulse", f"v{gp_version}"))
    except Exception:
        results.append(("\u2713", "GISPulse", "v0.1.0 (source)"))

    # --- 2. Python version ---
    py_ver = platform.python_version()
    py_tuple = sys.version_info[:2]
    if py_tuple >= (3, 10):
        results.append(("\u2713", "Python", f"v{py_ver}"))
    else:
        results.append(("\u2717", "Python", f"v{py_ver} (>= 3.10 required)"))
        has_critical = True

    # --- 3. GDAL ---
    try:
        from osgeo import gdal

        gdal_ver = gdal.VersionInfo("RELEASE_NAME")
        results.append(("\u2713", "GDAL", f"v{gdal_ver}"))
    except ImportError:
        results.append(("\u26a0", "GDAL", "not installed (optional, needed for raster)"))

    # --- 4. DuckDB + spatial extension ---
    try:
        import duckdb

        ddb_ver = duckdb.__version__
        detail = f"v{ddb_ver}"
        try:
            conn = duckdb.connect(":memory:")
            conn.execute("INSTALL spatial; LOAD spatial;")
            conn.execute("SELECT ST_Point(0, 0);")
            detail += " + spatial extension"
            conn.close()
        except Exception:
            detail += " (spatial extension NOT available)"
        results.append(("\u2713", "DuckDB", detail))
    except ImportError:
        results.append(("\u2717", "DuckDB", "not installed (required)"))
        has_critical = True

    # --- 5. PostGIS connectivity ---
    from core.config import settings as _cfg
    db_url = _cfg.database.dsn or None
    if db_url:
        try:
            import psycopg2

            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute("SELECT PostGIS_Version();")
            pg_ver = cur.fetchone()[0]
            cur.close()
            conn.close()
            results.append(("\u2713", "PostGIS", f"v{pg_ver}"))
        except ImportError:
            results.append(("\u26a0", "PostGIS", "psycopg2 not installed (pip install gispulse[postgis])"))
        except Exception as e:
            results.append(("\u2717", "PostGIS", f"connection failed: {e}"))
            has_critical = True
    else:
        results.append(("\u26a0", "PostGIS", "GISPULSE_DATABASE_URL not set (optional)"))

    # --- 6. Disk space ---
    try:
        usage = shutil.disk_usage(os.getcwd())
        free_gb = usage.free / (1024**3)
        if free_gb < 1.0:
            results.append(("\u26a0", "Disk space", f"{free_gb:.1f} GB free (< 1 GB warning)"))
        else:
            results.append(("\u2713", "Disk space", f"{free_gb:.1f} GB free"))
    except OSError:
        results.append(("\u26a0", "Disk space", "unable to check"))

    # --- 7. Optional dependencies ---
    optional_deps = {
        "geopandas": "geopandas",
        "shapely": "shapely",
        "fiona": "fiona",
        "pyogrio": "pyogrio",
        "rasterio": "rasterio",
    }
    for display_name, module_name in optional_deps.items():
        try:
            mod = __import__(module_name)
            ver = getattr(mod, "__version__", getattr(mod, "gdal_version", "?"))
            results.append(("\u2713", display_name, f"v{ver}"))
        except ImportError:
            results.append(("\u26a0", display_name, "not installed (optional)"))

    # --- 8. OIDC / Session secret ---
    from core.config import settings as _cfg2
    oidc_issuer = _cfg2.oidc.issuer.strip()
    session_secret = _cfg2.session.secret.strip()
    if oidc_issuer:
        if session_secret:
            results.append(("\u2713", "OIDC session secret", "set"))
        else:
            results.append(("\u2717", "OIDC session secret", "GISPULSE_SESSION_SECRET not set — OIDC will refuse to start"))
            has_critical = True
    else:
        results.append(("\u26a0", "OIDC", "not configured (optional)"))

    # --- 9. Portal assets ---
    portal_dist = Path(__file__).resolve().parent.parent / "portal" / "dist"
    if portal_dist.exists() and any(portal_dist.iterdir()):
        results.append(("\u2713", "Portal assets", str(portal_dist)))
    else:
        results.append(("\u26a0", "Portal assets", "portal/dist/ not found (run: cd portal && npm run build)"))

    # --- Render output ---
    _doctor_render(results)

    if has_critical:
        raise typer.Exit(1)


def _doctor_render(results: list[tuple[str, str, str]]) -> None:
    """Render doctor results as a formatted table, using rich if available."""
    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(title="GISPulse Doctor", show_header=True, header_style="bold")
        table.add_column("", width=3, justify="center")
        table.add_column("Check", min_width=16)
        table.add_column("Detail")

        style_map = {"\u2713": "green", "\u2717": "red bold", "\u26a0": "yellow"}
        for icon, name, detail in results:
            style = style_map.get(icon, "")
            table.add_row(f"[{style}]{icon}[/{style}]", name, detail)

        Console().print(table)
    except ImportError:
        # Fallback: plain text
        typer.echo("")
        typer.echo("GISPulse Doctor")
        typer.echo("=" * 60)
        name_width = max(len(r[1]) for r in results)
        for icon, name, detail in results:
            typer.echo(f"  {icon}  {name:<{name_width}}  {detail}")
        typer.echo("=" * 60)


@app.command()
def engine(
    port: int = typer.Option(0, "--port", "-p", help="Port to listen on (0 = auto-detect free port)."),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to."),
    engine_backend: str = typer.Option("duckdb", "--engine", "-e", help="Spatial engine: 'duckdb' (local) or 'postgis'."),
    data_dir: str = typer.Option("~/.gispulse/data", "--data-dir", "-d", help="Data directory."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser on start."),
) -> None:
    """Start the full GISPulse engine (API + Portal + Viewer) as a single process.

    This is the entry point used by the Tauri desktop sidecar and for standalone usage.
    When port=0, a free port is auto-selected and printed to stdout for the parent process.
    """
    import os
    import json
    import socket

    os.environ["GISPULSE_ENGINE"] = engine_backend
    os.environ.setdefault("GISPULSE_STORAGE", "sqlite")

    # Resolve data dir
    resolved_data = Path(data_dir).expanduser().resolve()
    resolved_data.mkdir(parents=True, exist_ok=True)

    # Auto-detect free port if port=0
    if port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            port = s.getsockname()[1]

    # Print machine-readable startup info to stdout (Tauri reads this)
    startup_info = {"port": port, "host": host, "engine": engine_backend, "pid": os.getpid()}
    typer.echo(f"GISPULSE_READY:{json.dumps(startup_info)}")

    if not no_browser:
        import webbrowser
        url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
        typer.echo(f"GISPulse at {url}")
        webbrowser.open(url)

    import uvicorn
    uvicorn.run(
        "gispulse.adapters.http.app:create_app",
        host=host,
        port=port,
        factory=True,
        log_level="info",
    )


# ---------------------------------------------------------------------------
# jobs sub-group
# ---------------------------------------------------------------------------

jobs_app = typer.Typer(
    name="jobs",
    help="Manage GISPulse jobs (list, status, cancel).",
    add_completion=False,
)
app.add_typer(jobs_app)


def _jobs_http(host: str, api_key: str | None):
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

    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", [])

    if not items:
        typer.echo("No jobs found.")
        return

    header = f"{'ID':<38}  {'STATUS':<10}  {'NAME':<24}  {'ATTEMPTS':>8}  {'DURATION':>10}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for job in items:
        duration = job.get("duration_seconds")
        dur_str = f"{duration:.1f}s" if duration is not None else "—"
        typer.echo(
            f"{job['id']:<38}  {job['status']:<10}  {job.get('name', ''):<24}"
            f"  {job.get('attempts', 0):>8}  {dur_str:>10}"
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

    job = resp.json()
    typer.echo(f"ID:        {job['id']}")
    typer.echo(f"Name:      {job.get('name', '')}")
    typer.echo(f"Status:    {job['status']}")
    typer.echo(f"Attempts:  {job.get('attempts', 0)}")
    if job.get("dataset_id"):
        typer.echo(f"Dataset:   {job['dataset_id']}")
    if job.get("started_at"):
        typer.echo(f"Started:   {job['started_at']}")
    if job.get("completed_at"):
        typer.echo(f"Completed: {job['completed_at']}")
    if job.get("duration_seconds") is not None:
        typer.echo(f"Duration:  {job['duration_seconds']:.1f}s")
    if job.get("error_message"):
        typer.echo(f"Error:     {job['error_message']}")
    if job.get("result_path"):
        typer.echo(f"Result:    {job['result_path']}")


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

    job = resp.json()
    typer.echo(f"Job '{job_id}' cancelled (status: {job['status']}).")


# ---------------------------------------------------------------------------
# update command + startup check
# ---------------------------------------------------------------------------

_GITHUB_RELEASES_URL = "https://api.github.com/repos/imagodata/gispulse/releases/latest"
_UPDATE_CHECK_CACHE = Path("~/.gispulse/update-check.json").expanduser()
_UPDATE_CHECK_INTERVAL_SECONDS = 86400  # 24 h


def _get_installed_version() -> str:
    """Return the installed version of gispulse."""
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("gispulse")
    except Exception:
        return "0.1.0"


def _fetch_latest_release() -> dict | None:
    """Fetch latest release info from GitHub API. Returns None on failure."""
    import json
    import urllib.request

    req = urllib.request.Request(
        _GITHUB_RELEASES_URL,
        headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "gispulse-cli"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _parse_version(v: str) -> Version:
    """Parse a version string, stripping leading 'v' if present."""
    from packaging.version import Version

    return Version(v.lstrip("v"))


def _detect_install_mode() -> str:
    """Detect how gispulse was installed: 'pip', 'homebrew', or 'binary'."""
    import shutil
    import subprocess
    import sys

    # pip: running from a site-packages environment
    if "site-packages" in (sys.executable or ""):
        return "pip"

    # homebrew: brew is available and gispulse is in its list
    brew = shutil.which("brew")
    if brew:
        try:
            result = subprocess.run(
                [brew, "list", "--formula"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if "gispulse" in result.stdout.split():
                return "homebrew"
        except Exception:
            pass

    return "binary"


def _truncate_changelog(body: str | None, max_lines: int = 12) -> str:
    """Truncate release body for display."""
    if not body:
        return "(no changelog)"
    lines = body.strip().splitlines()
    if len(lines) <= max_lines:
        return body.strip()
    return "\n".join(lines[:max_lines]) + f"\n  ... ({len(lines) - max_lines} more lines)"


@app.command()
def update(
    check: bool = typer.Option(False, "--check", help="Check only, do not install."),
    force: bool = typer.Option(False, "--force", help="Update even if already at latest version."),
) -> None:
    """Check for updates and self-update GISPulse."""
    import subprocess
    import shutil
    import sys
    import tempfile

    current = _get_installed_version()
    typer.echo(f"Current version: v{current}")

    release = _fetch_latest_release()
    if release is None:
        typer.echo("Error: could not reach GitHub API (no network or rate-limited).", err=True)
        raise typer.Exit(1)

    tag = release.get("tag_name", "")
    if not tag:
        typer.echo("Error: no tag found in latest release.", err=True)
        raise typer.Exit(1)

    try:
        current_v = _parse_version(current)
        latest_v = _parse_version(tag)
    except Exception as e:
        typer.echo(f"Error parsing versions: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Latest version:  v{latest_v}")

    is_outdated = latest_v > current_v

    if check:
        if is_outdated:
            typer.echo(f"\nUpdate available: v{current} -> v{latest_v}")
            typer.echo("Run `gispulse update` to upgrade.")
            raise typer.Exit(1)
        else:
            typer.echo(f"\nGISPulse v{current} is up to date.")
            raise typer.Exit(0)

    if not is_outdated and not force:
        typer.echo(f"\nGISPulse v{current} is up to date.")
        return

    # Show changelog
    changelog = _truncate_changelog(release.get("body"))
    typer.echo(f"\nChangelog:\n  {changelog.replace(chr(10), chr(10) + '  ')}")

    # Confirm unless --force
    if not force:
        confirm = typer.confirm(f"\nUpgrade to v{latest_v}?")
        if not confirm:
            typer.echo("Cancelled.")
            raise typer.Exit(0)

    mode = _detect_install_mode()
    typer.echo(f"\nInstall mode: {mode}")

    if mode == "pip":
        typer.echo(f"Running: pip install --upgrade gispulse=={latest_v}")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", f"gispulse=={latest_v}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            typer.echo(f"pip upgrade failed:\n{result.stderr}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Successfully updated to v{latest_v}.")

    elif mode == "homebrew":
        brew = shutil.which("brew")
        typer.echo("Running: brew upgrade gispulse")
        result = subprocess.run(
            [brew, "upgrade", "gispulse"],  # type: ignore[arg-type]
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            typer.echo(f"brew upgrade failed:\n{result.stderr}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Successfully updated to v{latest_v}.")

    elif mode == "binary":
        # Find a matching asset for the current platform
        import platform
        import urllib.request

        system = platform.system().lower()  # linux, darwin, windows
        machine = platform.machine().lower()  # x86_64, arm64, aarch64

        assets = release.get("assets", [])
        matching = [
            a for a in assets
            if system in a["name"].lower() and (machine in a["name"].lower()
                                                  or ("amd64" in a["name"].lower() and machine == "x86_64")
                                                  or ("arm64" in a["name"].lower() and machine == "aarch64"))
        ]

        if not matching:
            typer.echo(
                f"Error: no binary asset found for {system}/{machine} in release v{latest_v}.\n"
                f"Available assets: {[a['name'] for a in assets]}",
                err=True,
            )
            typer.echo("Try: pip install --upgrade gispulse", err=True)
            raise typer.Exit(1)

        asset = matching[0]
        download_url = asset["browser_download_url"]
        typer.echo(f"Downloading {asset['name']} ...")

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(asset["name"]).suffix) as tmp:
                req = urllib.request.Request(download_url, headers={"User-Agent": "gispulse-cli"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    tmp.write(resp.read())
                tmp_path = Path(tmp.name)
        except Exception as e:
            typer.echo(f"Download failed: {e}", err=True)
            raise typer.Exit(1)

        # Replace current binary
        current_bin = Path(shutil.which("gispulse") or sys.executable)
        try:
            import os
            import stat

            tmp_path.chmod(current_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            backup = current_bin.with_suffix(".bak")
            shutil.copy2(current_bin, backup)
            shutil.move(str(tmp_path), str(current_bin))
            typer.echo(f"Successfully updated to v{latest_v}.")
            typer.echo(f"Backup saved as {backup}")
        except PermissionError:
            typer.echo(
                f"Permission denied. Try:\n  sudo cp {tmp_path} {current_bin}",
                err=True,
            )
            raise typer.Exit(1)

    # Update the cache so startup check doesn't nag
    _write_update_cache(str(latest_v))


def _write_update_cache(latest_version: str) -> None:
    """Write update check result to cache file."""
    import json
    import time

    _UPDATE_CHECK_CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache = {
        "checked_at": time.time(),
        "latest_version": latest_version,
    }
    try:
        _UPDATE_CHECK_CACHE.write_text(json.dumps(cache))
    except OSError:
        pass  # non-critical


def _read_update_cache() -> dict | None:
    """Read cached update check result. Returns None if stale or missing."""
    import json
    import time

    if not _UPDATE_CHECK_CACHE.exists():
        return None
    try:
        cache = json.loads(_UPDATE_CHECK_CACHE.read_text())
        if time.time() - cache.get("checked_at", 0) > _UPDATE_CHECK_INTERVAL_SECONDS:
            return None
        return cache
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def _startup_update_check() -> None:
    """Non-blocking update check at CLI startup. Prints a notice if outdated.

    Respects GISPULSE_NO_UPDATE_CHECK=1 env var.
    Caches results for 24h in ~/.gispulse/update-check.json.
    """
    import os

    from core.config import settings as _cfg3
    if _cfg3.telemetry.no_update_check:
        return

    # Check cache first
    cache = _read_update_cache()
    if cache is None:
        # Perform the check (synchronous but fast — single HTTP call)
        release = _fetch_latest_release()
        if release is None:
            return  # no network, silently skip
        tag = release.get("tag_name", "")
        if not tag:
            return
        latest_str = tag.lstrip("v")
        _write_update_cache(latest_str)
    else:
        latest_str = cache["latest_version"]

    try:
        current_v = _parse_version(_get_installed_version())
        latest_v = _parse_version(latest_str)
    except Exception:
        return

    if latest_v > current_v:
        typer.echo(
            f"\nA new version of GISPulse is available: v{latest_v} (current: v{current_v}).\n"
            f"Run `gispulse update` to upgrade.\n",
            err=True,
        )


# Register the startup check as a Typer callback
@app.callback(invoke_without_command=True)
def _cli_callback(ctx: typer.Context) -> None:
    """GISPulse CLI entrypoint with startup update check."""
    if ctx.invoked_subcommand is None and ctx.info_name == "gispulse":
        # No subcommand: show help
        typer.echo(ctx.get_help())
        raise typer.Exit(0)
    # Don't run update check for the 'update' command itself
    if ctx.invoked_subcommand != "update":
        _startup_update_check()


# ---------------------------------------------------------------------------
# telemetry sub-group
# ---------------------------------------------------------------------------

telemetry_app = typer.Typer(
    name="telemetry",
    help="Manage anonymous telemetry (opt-in usage statistics).",
    add_completion=False,
    invoke_without_command=True,
)
app.add_typer(telemetry_app)


@telemetry_app.callback(invoke_without_command=True)
def telemetry_callback(
    ctx: typer.Context,
    status: bool = typer.Option(False, "--status", "-s", help="Show current telemetry status."),
    enable: bool = typer.Option(False, "--enable", help="Enable telemetry."),
    disable: bool = typer.Option(False, "--disable", help="Disable telemetry."),
) -> None:
    """View or change telemetry settings."""
    from gispulse.telemetry import get_status, set_enabled

    if enable and disable:
        typer.echo("Error: cannot use --enable and --disable together.", err=True)
        raise typer.Exit(1)

    if enable:
        set_enabled(True)
        typer.echo("Telemetry enabled.")
        return

    if disable:
        set_enabled(False)
        typer.echo("Telemetry disabled.")
        return

    # Default: show status (also when --status is explicit)
    typer.echo(get_status())


# ---------------------------------------------------------------------------
# Marketplace sub-commands
# ---------------------------------------------------------------------------
marketplace_app = typer.Typer(
    name="marketplace",
    help="Manage GISPulse capability plugins.",
    add_completion=False,
)
app.add_typer(marketplace_app, name="marketplace")

_PLUGIN_PREFIX = "gispulse-cap-"
_REGISTRY_URL = (
    "https://raw.githubusercontent.com/gispulse/marketplace/main/registry.json"
)


@marketplace_app.command("list")
def marketplace_list() -> None:
    """List installed GISPulse plugins (entry-point based)."""
    from capabilities.registry import list_plugins

    plugins = list_plugins()
    if not plugins:
        typer.echo("No plugins installed.")
        typer.echo(
            "\nInstall one with:  gispulse marketplace install <name>"
        )
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
    import urllib.error

    url = f"https://pypi.org/simple/"
    typer.echo(f"Searching PyPI for '{_PLUGIN_PREFIX}*' matching '{query}'...")

    # Use PyPI JSON API to search by project name pattern
    search_url = f"https://pypi.org/simple/"
    try:
        req = urllib.request.Request(search_url, headers={"Accept": "application/vnd.pypi.simple.v1+json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            projects = data.get("projects", [])
            matches = [
                p["name"]
                for p in projects
                if p["name"].startswith(_PLUGIN_PREFIX) and query.lower() in p["name"].lower()
            ]
    except Exception:
        # Fallback: try the curated registry
        matches = []
        try:
            req = urllib.request.Request(_REGISTRY_URL)
            with urllib.request.urlopen(req, timeout=10) as resp:
                registry = json.loads(resp.read())
                matches = [
                    p["package"]
                    for p in registry.get("plugins", [])
                    if query.lower() in p.get("name", "").lower()
                    or query.lower() in p.get("description", "").lower()
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
    typer.echo(f"\nInstall with:  gispulse marketplace install <name>")


@marketplace_app.command("install")
def marketplace_install(
    name: str = typer.Argument(
        ..., help="Plugin name (e.g. 'ftth'). Will install gispulse-cap-<name>."
    ),
    upgrade: bool = typer.Option(False, "--upgrade", "-U", help="Upgrade if already installed."),
) -> None:
    """Install a GISPulse capability plugin from PyPI."""
    import subprocess
    import sys

    package = f"{_PLUGIN_PREFIX}{name}" if not name.startswith(_PLUGIN_PREFIX) else name
    cmd = [sys.executable, "-m", "pip", "install", package]
    if upgrade:
        cmd.append("--upgrade")

    typer.echo(f"Installing {package}...")
    result = subprocess.run(cmd, capture_output=True, text=True)

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
    import subprocess
    import sys

    package = f"{_PLUGIN_PREFIX}{name}" if not name.startswith(_PLUGIN_PREFIX) else name

    typer.echo(f"Uninstalling {package}...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", package],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        typer.echo(f"Error uninstalling {package}:\n{result.stderr}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Uninstalled {package}.")


@marketplace_app.command("info")
def marketplace_info(
    name: str = typer.Argument(
        ..., help="Plugin name (e.g. 'ftth')."
    ),
) -> None:
    """Show details about an installed plugin."""
    from importlib.metadata import PackageNotFoundError

    package = f"{_PLUGIN_PREFIX}{name}" if not name.startswith(_PLUGIN_PREFIX) else name

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


# ---------------------------------------------------------------------------
# template sub-group
# ---------------------------------------------------------------------------

template_app = typer.Typer(
    name="template",
    help="Manage and use GISPulse pipeline templates.",
    add_completion=False,
)
app.add_typer(template_app)

#: Directory where built-in templates are stored (relative to this file's package root).
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _load_template_index() -> list[dict]:
    """Return metadata for every .json template in the templates directory."""
    import json

    if not _TEMPLATES_DIR.exists():
        return []

    entries: list[dict] = []
    for tpl_path in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(tpl_path.read_text(encoding="utf-8"))
            steps = len(data) if isinstance(data, list) else 1
            entries.append(
                {
                    "name": tpl_path.stem,
                    "path": tpl_path,
                    "steps": steps,
                    "capabilities": (
                        sorted({r.get("capability", "?") for r in data})
                        if isinstance(data, list)
                        else []
                    ),
                }
            )
        except Exception:
            entries.append({"name": tpl_path.stem, "path": tpl_path, "steps": 0, "capabilities": []})

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
    typer.echo(f"  {'─' * 35} {'─' * 5}  {'─' * 40}")
    for entry in entries:
        caps = ", ".join(entry["capabilities"]) or "—"
        typer.echo(f"  {entry['name']:<35} {entry['steps']:>5}  {caps}")

    typer.echo(f"\nUse: gispulse template use <name> [-o <dest>]")


@template_app.command("use")
def template_use(
    name: str = typer.Argument(..., help="Template name (without .json extension)."),
    output: Path = typer.Option(
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

    # Resolve template source
    tpl_path = _TEMPLATES_DIR / f"{name}.json"
    if not tpl_path.exists():
        # Try exact match (user passed name.json)
        tpl_path = _TEMPLATES_DIR / name
    if not tpl_path.exists():
        entries = _load_template_index()
        available = [e["name"] for e in entries]
        typer.echo(
            f"Error: template '{name}' not found.\n"
            f"Available: {', '.join(available) or 'none'}",
            err=True,
        )
        raise typer.Exit(1)

    dest = output or Path.cwd() / tpl_path.name
    if dest.exists():
        typer.echo(f"Warning: {dest} already exists — overwriting.", err=True)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tpl_path, dest)
    typer.echo(f"Template '{name}' copied to {dest}")
    typer.echo(f"Edit the file then run: gispulse run <input> --rules {dest} -o output.gpkg")


@template_app.command("workflow")
def workflow_execute(
    name: str = typer.Argument(..., help="Workflow name (ex: ftth_network_analysis)"),
    input_path: Path = typer.Argument(..., help="Chemin vers le GPKG d'entrée"),
    output_path: Optional[Path] = typer.Option(None, "--output", "-o", help="Chemin de sortie (GPKG)"),
) -> None:
    """Exécute un workflow intégré (ex: ftth_network_analysis).

    Example::

        gispulse template workflow ftth_network_analysis input.gpkg -o output.gpkg
    """
    from gispulse.workflows.ftth_network_analysis import FTTHNetworkAnalysisWorkflow
    from gispulse.persistence.gpkg import read_gpkg

    dataset = read_gpkg(input_path)
    template_path = _TEMPLATES_DIR / f"{name}.json"
    
    if not template_path.exists():
        typer.echo(f"Error: workflow template '{name}' not found.", err=True)
        raise typer.Exit(1)

    workflow = FTTHNetworkAnalysisWorkflow(template_path, dataset)
    result = workflow.run(output_path)
    typer.echo(f"Workflow '{name}' terminé. Résultat: {len(result.layers)} couches.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
