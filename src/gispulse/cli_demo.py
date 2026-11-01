"""``gispulse demo`` — bundled demos a newcomer can run offline in under a minute.

Usage::

    gispulse demo list                      # bundled templates, which ones ship a sample
    gispulse demo run cadastre_topology_cleanup [--out DIR] [--param STEP.PARAM=VALUE ...] [--force]

The source of truth is ``templates/manifest.yml``. An entry carrying a
``demo:`` block names the sample file(s) under ``examples/``, the layer to
read, optional reference layers (``alias: path#layer``) and parameter
overrides (``"<step_id>.<param>": value``) applied in memory so the
template runs on the sample without being edited::

    demo:
      input: datasets/muret_parcels.gpkg      # primary input, relative to examples/
      layer: parcels                          # layer of the primary input (containers)
      ref_layers:                             # pipeline ref alias -> sample "path#layer"
        zones_inondables: datasets/muret_flood_zones.gpkg#flood_zones
      params: {}                              # "<step_id>.<param>": value overrides
      requires_extras: []                     # optional-dependency groups a step needs
      files:                                  # per sample file, for the download path
        datasets/muret_parcels.gpkg: {sha256: "<hex>", size: 7249920}
      note: "Muret (31): 17 212 parcelles cadastrales"

Sample files and templates resolve, in order, from ``--data-dir`` (a
repository-like root holding ``examples/`` and ``templates/``, a mirror of
``examples/``, or a flat directory of files), the repository checkout when
running from one, then a per-user cache (``$XDG_CACHE_HOME/gispulse/demo``
or ``~/.cache/gispulse/demo``, one sub-directory per download ref) filled by
downloading ``https://raw.githubusercontent.com/imagodata/gispulse/<ref>/...``
where ``<ref>`` is ``v<version>`` for a release, ``main`` otherwise. Sample
files carry a SHA-256 pinned in the manifest that is verified before use;
``templates/`` has no pinned hash. Neither directory ships in the wheel.

``requires_extras`` names optional-dependency groups of ``pyproject.toml``
(``network``, ``raster``); ``demo run`` checks their modules import before
staging anything and prints the ``pip install "gispulse[...]"`` to run.

Execution goes through :meth:`SessionManager.run_pipeline_v2` — the path
behind ``gispulse run`` — and every leaf step of the DAG is written as a
layer of ``<out>/<template>.gpkg``. The output directory carries a
``.gispulse-demo`` marker; ``--force`` only removes a directory that has it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import typer

if TYPE_CHECKING:
    from gispulse.core.pipeline import PipelineSpec, StepSpec

demo_app = typer.Typer(
    name="demo",
    help="Run bundled demos on sample data — offline, in under a minute.",
    add_completion=False,
)

#: Repository root when running from a checkout (src/gispulse/cli_demo.py -> ../../..).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_REL = "templates/manifest.yml"
_RAW_BASE = "https://raw.githubusercontent.com/imagodata/gispulse"
_DEFAULT_OUT = Path("gispulse-demo")
_NO_SAMPLE = "no bundled sample"
_TABLE_RULE = "─"
#: Written in every output directory; ``--force`` only wipes a directory carrying it.
_MARKER = ".gispulse-demo"
#: Optional-dependency group (pyproject ``[project.optional-dependencies]``)
#: -> module(s) that must import for the group to count as installed.
_EXTRA_MODULES: dict[str, tuple[str, ...]] = {
    "network": ("networkx",),
    "raster": ("rasterio",),
}

Fetcher = Callable[[str], bytes]


class DemoError(Exception):
    """A failure reported to the user as a one-line message, exit code 1."""


# ---------------------------------------------------------------------------
# Manifest model
# ---------------------------------------------------------------------------


@dataclass
class DemoSpec:
    """The ``demo:`` block of a manifest entry (see module docstring)."""

    input: str
    layer: str | None = None
    ref_layers: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    note: str = ""
    requires_extras: list[str] = field(default_factory=list)

    @property
    def size_bytes(self) -> int:
        return sum(int(meta.get("size") or 0) for meta in self.files.values())

    def sha256_for(self, rel: str) -> str | None:
        meta = self.files.get(rel) or {}
        value = meta.get("sha256")
        return str(value).lower() if value else None

    def sample_paths(self) -> list[str]:
        """Distinct sample files (relative to ``examples/``) this demo needs."""
        paths = [_split_source(self.input)[0]]
        for source in self.ref_layers.values():
            path = _split_source(source)[0]
            if path not in paths:
                paths.append(path)
        return paths


@dataclass
class DemoEntry:
    """A manifest entry, with or without a bundled sample."""

    name: str
    domain: str
    description: str
    file: str
    demo: DemoSpec | None = None


def _split_source(source: str) -> tuple[str, str | None]:
    """``"path#layer"`` -> ``("path", "layer")``; no ``#`` -> ``("path", None)``."""
    path, _, layer = source.partition("#")
    return path, (layer or None)


def parse_manifest(text: str) -> list[DemoEntry]:
    """Parse ``templates/manifest.yml`` into entries; ``demo`` is None when absent."""
    import yaml

    raw = yaml.safe_load(text) or {}
    entries: list[DemoEntry] = []
    for item in raw.get("entries", []) or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        demo: DemoSpec | None = None
        block = item.get("demo")
        if isinstance(block, dict) and block.get("input"):
            extras = block.get("requires_extras") or []
            if isinstance(extras, str):
                extras = [extras]
            demo = DemoSpec(
                input=str(block["input"]),
                layer=(str(block["layer"]) if block.get("layer") else None),
                ref_layers={str(k): str(v) for k, v in (block.get("ref_layers") or {}).items()},
                params=dict(block.get("params") or {}),
                files={str(k): dict(v or {}) for k, v in (block.get("files") or {}).items()},
                note=str(block.get("note") or ""),
                requires_extras=[str(e) for e in extras if e],
            )
        entries.append(
            DemoEntry(
                name=name,
                domain=str(item.get("domain") or ""),
                description=str(item.get("description") or ""),
                file=str(item.get("file") or f"{name}.json"),
                demo=demo,
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Sample resolver: --data-dir -> checkout -> cache -> download (sha256-checked)
# ---------------------------------------------------------------------------


def _release_ref() -> str:
    """Git ref to download from: the release tag of the installed version, else ``main``."""
    from gispulse import __version__

    version = __version__ or ""
    return f"v{version}" if re.fullmatch(r"\d+\.\d+\.\d+", version) else "main"


def _default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "gispulse" / "demo"


def _http_fetch(url: str) -> bytes:
    import httpx

    try:
        response = httpx.get(url, follow_redirects=True, timeout=120.0)
    except httpx.HTTPError as exc:
        raise DemoError(f"download failed for {url}: {exc}") from exc
    if response.status_code != 200:
        raise DemoError(f"download failed ({response.status_code}) for {url}")
    return response.content


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SampleResolver:
    """Locate a repository-relative file (``examples/...``, ``templates/...``).

    Lookup order: ``data_dir`` (a repository-like root, so
    ``examples/datasets/x.gpkg`` is looked up as ``<data_dir>/examples/datasets/x.gpkg``,
    then ``<data_dir>/datasets/x.gpkg`` — a mirror of ``examples/`` — then
    ``<data_dir>/x.gpkg``; ``templates/manifest.yml`` likewise as
    ``<data_dir>/templates/manifest.yml`` then ``<data_dir>/manifest.yml``), the
    repository checkout(s), the per-user cache under ``<cache_dir>/<ref>/``
    (only when its SHA-256 matches the pinned one, if any), then a download
    from GitHub at ``ref`` — verified against the pinned SHA-256 before it is
    written to the cache. Keying the cache on ``ref`` means an upgrade never
    serves the previous release's manifest or templates.
    """

    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        cache_dir: Path | None = None,
        fetcher: Fetcher | None = None,
        ref: str | None = None,
        repo_roots: list[Path] | None = None,
        echo: Callable[[str], None] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.cache_dir = cache_dir or _default_cache_dir()
        self.fetcher = fetcher or _http_fetch
        self.ref = ref or _release_ref()
        self.repo_roots = repo_roots if repo_roots is not None else [_REPO_ROOT, Path.cwd()]
        self.echo = echo or (lambda _message: None)

    def resolve(self, rel: str, *, sha256: str | None = None) -> Path:
        rel = rel.replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            raise DemoError(f"refusing a sample path that escapes the repository: {rel!r}")

        # (a) explicit data directory: repo-like root, mirror of examples/, or flat
        if self.data_dir is not None:
            for candidate in self._data_dir_candidates(rel):
                if candidate.is_file():
                    return candidate

        # (b) repository checkout
        for root in self.repo_roots:
            candidate = root / rel
            if candidate.is_file():
                return candidate

        # (c) per-user cache, keyed on the download ref
        cached = self.cache_dir / self.ref / rel
        if cached.is_file() and (sha256 is None or sha256_of(cached) == sha256):
            return cached

        # (d) download, verify, cache
        url = f"{_RAW_BASE}/{self.ref}/{rel}"
        self.echo(f"Downloading {rel} from {url} ...")
        data = self.fetcher(url)
        if sha256 is not None:
            digest = hashlib.sha256(data).hexdigest()
            if digest != sha256:
                raise DemoError(
                    f"SHA-256 mismatch for {rel}: expected {sha256}, got {digest} "
                    "— refusing to use the download."
                )
        cached.parent.mkdir(parents=True, exist_ok=True)
        partial = cached.with_name(cached.name + ".part")
        partial.write_bytes(data)
        os.replace(partial, cached)
        return cached

    def _data_dir_candidates(self, rel: str) -> list[Path]:
        assert self.data_dir is not None
        parts = rel.split("/")
        candidates = [self.data_dir / rel]
        if len(parts) > 1:
            candidates.append(self.data_dir.joinpath(*parts[1:]))
        flat = self.data_dir / parts[-1]
        if flat not in candidates:
            candidates.append(flat)
        return candidates


def load_entries(resolver: SampleResolver) -> list[DemoEntry]:
    try:
        manifest = resolver.resolve(_MANIFEST_REL)
    except DemoError as exc:
        raise DemoError(
            f"cannot find {_MANIFEST_REL} — run from a GISPulse checkout, pass --data-dir "
            f"pointing at a copy of templates/ and examples/, or go online once so it can be "
            f"cached ({exc})"
        ) from exc
    return parse_manifest(manifest.read_text(encoding="utf-8"))


def missing_extras(extras: list[str]) -> list[str]:
    """Extras (``requires_extras``) whose module(s) cannot be imported right now."""
    import importlib.util

    missing: list[str] = []
    for extra in extras:
        modules = _EXTRA_MODULES.get(extra)
        if modules is None:
            known = ", ".join(sorted(_EXTRA_MODULES)) or "none"
            raise DemoError(f"manifest names an unknown extra {extra!r} (known: {known})")
        if any(importlib.util.find_spec(module) is None for module in modules):
            missing.append(extra)
    return missing


# ---------------------------------------------------------------------------
# Parameter overrides
# ---------------------------------------------------------------------------


def parse_param(raw: str) -> tuple[str, Any]:
    """``STEP.PARAM=VALUE`` -> ``("STEP.PARAM", value)``; VALUE is JSON when it parses."""
    key, sep, value = raw.partition("=")
    key = key.strip()
    if not sep or "." not in key or key.startswith(".") or key.endswith("."):
        raise DemoError(f"--param expects STEP_ID.PARAM=VALUE, got {raw!r}")
    try:
        parsed: Any = json.loads(value)
    except ValueError:
        parsed = value
    return key, parsed


def apply_overrides(spec: PipelineSpec, overrides: dict[str, Any]) -> list[str]:
    """Set ``spec.steps[step_id].params[param] = value`` for each ``"step_id.param"`` key.

    Returns one ``step.param = value`` line per override, for the run log.
    """
    by_id = {step.id: step for step in spec.steps}
    applied: list[str] = []
    for key, value in overrides.items():
        step_id, _, param = str(key).partition(".")
        if not param or step_id not in by_id:
            known = ", ".join(by_id) or "none"
            raise DemoError(f"override {key!r} does not match a step (steps: {known})")
        by_id[step_id].params[param] = value
        applied.append(f"{step_id}.{param} = {json.dumps(value, ensure_ascii=False)}")
    return applied


def leaf_steps(spec: PipelineSpec) -> list[StepSpec]:
    """Enabled capability steps no other step consumes as ``input`` or ``ref_layer(s)``."""
    used: set[str] = set()
    for step in spec.enabled_steps:
        inputs = step.input if isinstance(step.input, list) else [step.input]
        used.update(i for i in inputs if isinstance(i, str))
        ref = step.params.get("ref_layer") if isinstance(step.params, dict) else None
        if isinstance(ref, str):
            used.add(ref)
        refs = step.params.get("ref_layers") if isinstance(step.params, dict) else None
        if isinstance(refs, list):
            used.update(r for r in refs if isinstance(r, str))
    return [s for s in spec.enabled_steps if s.id not in used and s.kind == "capability"]


# ---------------------------------------------------------------------------
# Staging + execution
# ---------------------------------------------------------------------------


@dataclass
class StagedInputs:
    primary: Path
    layer: str | None
    refs: dict[str, Path]
    sources: dict[str, Path]  # sample rel path -> where it was resolved from


def stage_inputs(demo: DemoSpec, resolver: SampleResolver, out_dir: Path) -> StagedInputs:
    """Copy the sample files into ``<out>/inputs/`` and pin each ref alias to a layer.

    A reference alias whose container layer is not named after the alias is
    extracted into ``<out>/inputs/<alias>.gpkg`` (layer ``<alias>``) so the
    regular ``run_pipeline_v2`` loader — which reads ``layer=alias`` first —
    resolves it deterministically.
    """
    from gispulse.persistence.io import list_layers, read_vector, write_vector

    inputs_dir = out_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    sources: dict[str, Path] = {}
    copied: dict[str, Path] = {}

    def _copy(rel: str) -> Path:
        if rel in copied:
            return copied[rel]
        src = resolver.resolve(f"examples/{rel}", sha256=demo.sha256_for(rel))
        dest = inputs_dir / src.name
        shutil.copyfile(src, dest)
        sources[rel] = src
        copied[rel] = dest
        return dest

    primary_rel, primary_layer = _split_source(demo.input)
    primary = _copy(primary_rel)
    layer = demo.layer or primary_layer

    refs: dict[str, Path] = {}
    for alias, source in demo.ref_layers.items():
        rel, wanted = _split_source(source)
        staged = _copy(rel)
        if staged.suffix.lower() in (".gpkg", ".sqlite"):
            layers = [name for name in list_layers(str(staged)) if name]
            wanted = wanted or (layers[0] if layers else None)
            if wanted and wanted != alias:
                extracted = inputs_dir / f"{alias}.gpkg"
                if extracted.resolve() == staged.resolve():
                    # The sample is already called <alias>.gpkg: keep the raw
                    # copy intact and extract next to it.
                    extracted = inputs_dir / f"{alias}_{wanted}.gpkg"
                write_vector(
                    read_vector(str(staged), layer=wanted),
                    str(extracted),
                    layer=alias,
                    write_style_sidecars=False,
                )
                staged = extracted
        refs[alias] = staged

    return StagedInputs(primary=primary, layer=layer, refs=refs, sources=sources)


def _format_size(size: int) -> str:
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    if size >= 1 << 10:
        return f"{size / (1 << 10):.0f} kB"
    return f"{size} B"


def _format_count(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _truncate(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _next_steps(
    out_dir: Path, out_gpkg: Path, staged: StagedInputs, written: list[str], final: str | None
) -> list[str]:
    import importlib.util

    lines = [f"  gispulse layers {out_gpkg}"]
    if importlib.util.find_spec("gispulse_portal") is not None:
        lines.append("  gispulse portal                      # explore the result in the browser")
    else:
        lines.append(
            f"  Open {out_gpkg.name} in QGIS (drag and drop), "
            "or `pip install gispulse-portal` then `gispulse portal`."
        )
    rerun = [
        "gispulse run",
        f"inputs/{staged.primary.name}",
        "-r pipeline.json",
        "-o rerun.gpkg",
    ]
    if staged.layer:
        rerun.append(f"--layer {staged.layer}")
    # `gispulse run` writes the last executed step only: say so when the
    # demo wrote several leaves, so the hint stays truthful.
    if len(written) <= 1 or final is None:
        comment = "# replay it with the regular runner"
    else:
        comment = f"# replays the pipeline; `gispulse run` writes the final step only ({final})"
    lines.append(f"  cd {out_dir} && {' '.join(rerun)}   {comment}")
    return lines


def _prepare_out_dir(out_dir: Path, force: bool) -> None:
    """Create ``out_dir`` (with its marker); wipe a previous demo output on ``--force``.

    Only a directory carrying the ``.gispulse-demo`` marker is ever removed:
    ``--out ~/Documents --force`` is refused. An existing empty directory is
    simply reused.
    """
    if out_dir.exists():
        if not out_dir.is_dir():
            raise DemoError(f"output path {out_dir} exists and is not a directory")
        if any(out_dir.iterdir()):
            if not force:
                raise DemoError(
                    f"output directory {out_dir} already exists — pass --force to overwrite it"
                )
            resolved = out_dir.resolve()
            if resolved in (Path.cwd().resolve(), Path.home().resolve(), Path(resolved.anchor)):
                raise DemoError(f"refusing to overwrite {resolved}")
            if not (out_dir / _MARKER).is_file():
                raise DemoError(
                    f"{out_dir} is not a gispulse demo output directory (no {_MARKER} marker) "
                    "— remove it yourself or pick another --out"
                )
            shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / _MARKER).write_text(
        "Written by `gispulse demo run`. `--force` only removes a directory carrying this file.\n",
        encoding="utf-8",
    )


def run_demo(
    entry: DemoEntry,
    resolver: SampleResolver,
    *,
    out_dir: Path,
    cli_params: list[str] | None = None,
    force: bool = False,
    echo: Callable[[str], None] = typer.echo,
) -> Path:
    """Stage the sample, run the template, write every leaf layer; return the GPKG path."""
    from gispulse.core.pipeline import load_pipeline, pipeline_to_dict
    from gispulse.orchestration.session_manager import SessionManager
    from gispulse.persistence.io import write_vector
    from gispulse.persistence.tier import TierError

    demo = entry.demo
    if demo is None:
        raise DemoError(f"template '{entry.name}' has no bundled sample ({_NO_SAMPLE})")

    overrides: dict[str, Any] = dict(demo.params)
    for raw in cli_params or []:
        key, value = parse_param(raw)
        overrides[key] = value

    missing = missing_extras(demo.requires_extras)
    if missing:
        raise DemoError(
            f"'{entry.name}' needs the optional extra(s) {', '.join(missing)} — "
            f'pip install "gispulse[{",".join(missing)}]"'
        )

    template_path = resolver.resolve(f"templates/{entry.file}")
    try:
        spec = load_pipeline(template_path)
    except Exception as exc:
        raise DemoError(f"cannot load template {template_path.name}: {exc}") from exc

    _prepare_out_dir(out_dir, force)

    echo(f"Demo: {entry.name} — {_truncate(entry.description, 110)}")
    staged = stage_inputs(demo, resolver, out_dir)
    sample_line = demo.note or ", ".join(demo.sample_paths())
    echo(f"Sample: {sample_line}")
    for rel, src in staged.sources.items():
        echo(f"  {rel}  <-  {src}")

    applied = apply_overrides(spec, overrides)
    if applied:
        echo("Parameter overrides:")
        for line in applied:
            echo(f"  {line}")

    # Make the staged pipeline replayable with `gispulse run` from <out>.
    spec.ref_layers = {alias: f"inputs/{path.name}" for alias, path in staged.refs.items()}
    (out_dir / "pipeline.json").write_text(
        json.dumps(pipeline_to_dict(spec), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    steps = spec.enabled_steps
    echo(f"Running {len(steps)} step(s) [engine: python]:")
    for step in steps:
        ref = step.params.get("ref_layer", "") if isinstance(step.params, dict) else ""
        suffix = f" (ref: {ref})" if ref else ""
        echo(f"  [{step.capability or step.type}] {step.id}{suffix}")

    started = time.perf_counter()
    manager = SessionManager(engine="python")
    try:
        result = manager.run_pipeline_v2(
            input_path=staged.primary,
            spec=spec,
            output_path=None,
            layer=staged.layer,
            ref_sources=staged.refs or None,
            keep_step_results=True,
        )
    except TierError as exc:
        raise DemoError(
            f"a step of '{entry.name}' needs a capability gated on this build — {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 — surface any executor failure as one readable line
        raise DemoError(f"pipeline failed — {type(exc).__name__}: {exc}") from exc
    elapsed = time.perf_counter() - started

    out_gpkg = out_dir / f"{entry.name}.gpkg"
    written: list[tuple[str, int]] = []
    for step in leaf_steps(spec):
        gdf = result.step_results.get(step.id)
        if gdf is None:
            continue
        write_vector(
            gdf,
            str(out_gpkg),
            layer=step.id,
            mode="w" if not written else "a",
            write_style_sidecars=False,
        )
        written.append((step.id, len(gdf)))
    if not written:
        raise DemoError("the pipeline produced no output layer")

    echo(
        f"Done in {elapsed:.1f} s — {_format_count(result.features_in)} feature(s) in, "
        f"{result.rules_applied} step(s) executed."
    )
    echo(f"Output layers ({out_gpkg}):")
    width = max(len(name) for name, _ in written)
    for name, count in written:
        echo(f"  {name:<{width}}  {_format_count(count):>9} feature(s)")
    echo("Next steps:")
    final = next((s.id for s in reversed(steps) if s.kind == "capability"), None)
    for line in _next_steps(out_dir, out_gpkg, staged, [name for name, _ in written], final):
        echo(line)
    return out_gpkg


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _resolver(data_dir: Path | None) -> SampleResolver:
    if data_dir is not None and not data_dir.is_dir():
        raise DemoError(f"--data-dir is not a directory: {data_dir}")
    return SampleResolver(data_dir=data_dir, echo=typer.echo)


_DATA_DIR_HELP = (
    "Directory holding the samples and templates offline: a copy of the repository root "
    "(examples/, templates/), a mirror of examples/, or a flat directory of files."
)


@demo_app.command("list")
def demo_list(
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
) -> None:
    """List the bundled templates and which ones ship a sample dataset."""
    try:
        entries = load_entries(_resolver(data_dir))
    except DemoError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    with_demo = [e for e in entries if e.demo is not None]
    without = [e for e in entries if e.demo is None]
    typer.echo(
        f"Bundled templates: {len(with_demo)} with a sample dataset, {len(without)} without.\n"
    )
    typer.echo(
        f"  {'Template':<32} {'Domain':<20} {'Size':>8}  {'Extras':<8} {'Sample':<44} Description"
    )
    typer.echo(
        f"  {_TABLE_RULE * 32} {_TABLE_RULE * 20} {_TABLE_RULE * 8}  {_TABLE_RULE * 8} "
        f"{_TABLE_RULE * 44} {_TABLE_RULE * 48}"
    )
    for entry in with_demo + without:
        if entry.demo is not None:
            size = _format_size(entry.demo.size_bytes) if entry.demo.size_bytes else "?"
            sample = entry.demo.note or ", ".join(entry.demo.sample_paths())
            extras = ",".join(entry.demo.requires_extras) or "-"
        else:
            size, sample, extras = "-", _NO_SAMPLE, "-"
        typer.echo(
            f"  {_truncate(entry.name, 32):<32} {_truncate(entry.domain or '-', 20):<20} "
            f"{size:>8}  {_truncate(extras, 8):<8} {_truncate(sample, 44):<44} "
            f"{_truncate(entry.description, 48)}"
        )
    typer.echo(
        "\nRun one: gispulse demo run <template>      "
        f"(writes ./{_DEFAULT_OUT.as_posix()}/<template>/<template>.gpkg)\n"
        'Extras: optional dependencies a demo needs — pip install "gispulse[<extra>]".'
    )


@demo_app.command("run")
def demo_run(
    template: str = typer.Argument(..., help="Template name, e.g. cadastre_topology_cleanup."),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Output directory (default: ./gispulse-demo/<template>/)."
    ),
    param: list[str] | None = typer.Option(
        None,
        "--param",
        "-p",
        help="Override a step parameter: STEP_ID.PARAM=VALUE (VALUE parsed as JSON when possible). Repeatable.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite the output directory if it exists (only one written by a previous demo run).",
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
) -> None:
    """Run a bundled template on its sample dataset, offline, and write the result GPKG.

    Example::

        gispulse demo run cadastre_topology_cleanup
        gispulse demo run risques_inondation_parcelles --param "zones_retenues.expression=risk_level == 'high'"
    """
    try:
        resolver = _resolver(data_dir)
        entries = load_entries(resolver)
        entry = next((e for e in entries if e.name == template), None)
        if entry is None:
            available = ", ".join(e.name for e in entries if e.demo is not None) or "none"
            raise DemoError(f"unknown template '{template}'. Demos with a sample: {available}")
        if entry.demo is None:
            available = ", ".join(e.name for e in entries if e.demo is not None) or "none"
            raise DemoError(
                f"template '{template}' has {_NO_SAMPLE}. Demos with a sample: {available}"
            )
        out_dir = out if out is not None else _DEFAULT_OUT / template
        run_demo(entry, resolver, out_dir=out_dir, cli_params=param, force=force)
    except DemoError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)


__all__ = [
    "DemoEntry",
    "DemoError",
    "DemoSpec",
    "SampleResolver",
    "apply_overrides",
    "demo_app",
    "leaf_steps",
    "load_entries",
    "missing_extras",
    "parse_manifest",
    "parse_param",
    "run_demo",
    "sha256_of",
    "stage_inputs",
]
