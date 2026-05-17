"""Application layer — the single in-process façade over GISPulse.

Before v1.8.0 the four delivery surfaces — the pip API, the ``gispulse``
CLI, the FastAPI server and the MCP server — each re-wired the engine,
repositories and services on their own. There was no shared entry point,
so the same use-case ("apply a capability", "run a pipeline", "browse the
catalog") was spelled differently, and sometimes wired against stale
models, on every surface.

:class:`GISPulseApp` is the Chantier B remedy of the v1.8.0 "Foundations"
refonte: a Gang-of-Four *façade* over ``capabilities``, ``core`` /
``orchestration``, ``catalog``, the templates directory, the
:class:`~gispulse.core.plugin_hub.ExtensionHub` and the headless trigger
runtime. It wires each subsystem once and exposes coarse, surface-agnostic
use-cases. The delivery surfaces are meant to become *thin* adapters over
this object — input parsing and output formatting only, no business logic.

Imports of the heavy subsystems are deferred to the method that needs
them, so ``import gispulse`` (which re-exports :class:`GISPulseApp` lazily
via :pep:`562`) stays cheap — no geopandas / FastAPI import on a bare
``import gispulse``.

This module is **additive**: the existing ``adapters/http/app.py`` and
``cli.py`` wiring still works untouched. Migrating those surfaces onto
:class:`GISPulseApp` is tracked as a separate Chantier B follow-up.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import geopandas as gpd

    from gispulse.catalog.models import CatalogEntry
    from gispulse.core.pipeline import PipelineSpec
    from gispulse.core.plugin_model import PluginRecord
    from gispulse.runtime.headless_runtime import HeadlessRuntime

# Built-in pipeline templates live at the repo root; ``app.py`` sits at
# ``src/gispulse/app.py`` so ``parents[2]`` is the repo root. This mirrors
# the resolution used by ``cli.py`` (``template`` sub-command).
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


class GISPulseApp:
    """In-process façade exposing GISPulse use-cases to every surface.

    Construct one per process and share it. The object is cheap — it holds
    no engine or repository handle of its own; each use-case wires what it
    needs lazily. A process-wide default instance is available through
    :func:`get_app`.
    """

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------
    def list_capabilities(self) -> list[dict]:
        """Return metadata (``name``, ``description``, ``schema``) for every
        registered capability, including those discovered via plugins."""
        from gispulse.capabilities import list_all

        return list_all()

    def apply_capability(
        self, name: str, gdf: "gpd.GeoDataFrame", **params: Any
    ) -> "gpd.GeoDataFrame":
        """Run a single capability on a GeoDataFrame.

        Args:
            name:   Registered capability name (e.g. ``"buffer"``).
            gdf:    Input features.
            params: Capability parameters — validated against the
                    capability signature (unknown kwargs raise rather
                    than being silently swallowed).

        Returns:
            The transformed GeoDataFrame.

        Raises:
            KeyError: If no capability is registered under ``name``.
            UnknownParameterError: On an unrecognised parameter.
        """
        from gispulse.capabilities import get
        from gispulse.capabilities.base import safe_execute

        return safe_execute(get(name), gdf, **params)

    # ------------------------------------------------------------------
    # Pipelines
    # ------------------------------------------------------------------
    def load_pipeline(
        self, source: "str | Path", *, validate: bool = True
    ) -> "PipelineSpec":
        """Parse a pipeline definition from a JSON file into a
        :class:`~gispulse.core.pipeline.PipelineSpec`."""
        from gispulse.core.pipeline import load_pipeline

        return load_pipeline(source, validate=validate)

    def run_pipeline(
        self,
        spec: "PipelineSpec | str | Path | dict | list",
        inputs: "dict[str, gpd.GeoDataFrame]",
        params: dict[str, Any] | None = None,
    ) -> "dict[str, gpd.GeoDataFrame]":
        """Execute a pipeline and return one GeoDataFrame per producing step.

        Args:
            spec:   A parsed :class:`PipelineSpec`, a path to a pipeline
                    JSON file, or a raw v2 dict / v1 list.
            inputs: Named input layers. Linear pipelines consume the first
                    value; DAG pipelines match keys to dataset node ids.
            params: Optional ``$var`` substitution values.

        Returns:
            Mapping of step id → result GeoDataFrame.
        """
        from gispulse.core.pipeline import (
            PipelineSpec,
            _parse_v1,
            _parse_v2,
            load_pipeline,
        )
        from gispulse.orchestration.pipeline_executor import PipelineExecutor

        if isinstance(spec, PipelineSpec):
            resolved = spec
        elif isinstance(spec, (str, Path)):
            resolved = load_pipeline(spec)
        elif isinstance(spec, dict):
            resolved = _parse_v2(spec)
        elif isinstance(spec, list):
            resolved = _parse_v1(spec)
        else:  # pragma: no cover - defensive
            raise TypeError(f"Unsupported pipeline spec type: {type(spec).__name__}")

        return PipelineExecutor().execute(resolved, inputs, params)

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------
    def load_dataset(
        self, path: "str | Path", layer: str | None = None
    ) -> "gpd.GeoDataFrame":
        """Read one layer from a GeoPackage (the first layer when ``layer``
        is omitted)."""
        from gispulse.persistence.gpkg import read_gpkg

        return read_gpkg(str(path), layer=layer)

    def list_layers(self, path: "str | Path") -> list[str]:
        """List the layer names available in a GeoPackage."""
        from gispulse.persistence.gpkg import list_layers

        return list_layers(str(path))

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------
    def browse_catalog(
        self,
        *,
        domain: Any | None = None,
        search: str | None = None,
        tags: list[str] | None = None,
        provider: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> "list[CatalogEntry]":
        """Search the unified GIS data catalog across every registered
        provider (projections, basemaps, open-data flux, …)."""
        from gispulse.catalog import registry

        return registry.search(
            domain=domain,
            search=search,
            tags=tags,
            provider=provider,
            limit=limit,
            offset=offset,
        )

    def get_catalog_entry(self, entry_id: str) -> "CatalogEntry | None":
        """Look up a single catalog entry by its full id."""
        from gispulse.catalog import registry

        return registry.get_entry(entry_id)

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------
    def list_templates(self) -> list[dict]:
        """List the built-in pipeline templates.

        Sourced from the ``template-pack`` data packs discovered by the
        :class:`~gispulse.core.plugin_hub.ExtensionHub` (v1.8.0 Chantier
        C). Falls back to a direct scan of the bundled ``templates/``
        directory when no template-pack manifest is present.

        Returns:
            One dict per template with ``name`` (the stem used to fetch
            it), ``title``, ``description`` and ``steps`` keys.
        """
        from gispulse.core.plugin_hub import ExtensionHub

        packs = ExtensionHub.get().data_pack_manifests("template-pack")
        if packs:
            entries: list[dict] = []
            for pack in packs:
                for entry in pack.entries:
                    name = entry.get("name")
                    if not name:
                        continue
                    entries.append(
                        {
                            "name": name,
                            "title": entry.get("title", name),
                            "description": entry.get("description", ""),
                            "steps": entry.get("steps", 0),
                        }
                    )
            return entries
        return self._scan_templates_dir()

    @staticmethod
    def _scan_templates_dir() -> list[dict]:
        """Fallback: scan ``templates/*.json`` directly when no
        template-pack manifest is available (e.g. a trimmed wheel)."""
        if not _TEMPLATES_DIR.is_dir():
            return []
        entries: list[dict] = []
        for tpl_path in sorted(_TEMPLATES_DIR.glob("*.json")):
            try:
                raw = json.loads(tpl_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            meta = raw if isinstance(raw, dict) else {}
            entries.append(
                {
                    "name": tpl_path.stem,
                    "title": meta.get("name", tpl_path.stem),
                    "description": meta.get("description", ""),
                    "steps": len(meta.get("steps", [])),
                }
            )
        return entries

    def get_template(self, name: str) -> dict:
        """Return the raw JSON of a built-in template by name (file stem).

        Raises:
            FileNotFoundError: If no template matches ``name``.
        """
        tpl_path = _TEMPLATES_DIR / f"{name}.json"
        if not tpl_path.exists():
            available = ", ".join(t["name"] for t in self.list_templates())
            raise FileNotFoundError(
                f"Unknown template {name!r}. Available: {available or 'none'}"
            )
        return json.loads(tpl_path.read_text(encoding="utf-8"))

    def instantiate_template(self, name: str) -> "PipelineSpec":
        """Load a built-in template and parse it into a runnable
        :class:`PipelineSpec`, ready to hand to :meth:`run_pipeline`."""
        from gispulse.core.pipeline import _parse_v1, _parse_v2

        raw = self.get_template(name)
        return _parse_v2(raw) if isinstance(raw, dict) else _parse_v1(raw)

    # ------------------------------------------------------------------
    # Plugins
    # ------------------------------------------------------------------
    def list_plugins(self) -> "list[PluginRecord]":
        """Return the inventory records discovered by the unified
        :class:`~gispulse.core.plugin_hub.ExtensionHub` — code plugins
        (sources, capabilities, sinks, extensions) and data packs."""
        from gispulse.core.plugin_hub import ExtensionHub

        return list(ExtensionHub.get().records)

    def list_data_packs(self) -> list[dict]:
        """Return the declarative data packs known to the ExtensionHub.

        Data packs are the *data regime* of the hub (v1.8.0 Chantier C):
        manifest-described bundles of templates / catalog sources /
        basemaps / projections. Each dict carries ``name``, ``content``,
        ``version``, ``display_name``, ``description``, ``tier`` and the
        ``entry_count``.
        """
        from gispulse.core.plugin_hub import ExtensionHub

        return [
            {
                "name": m.name,
                "content": m.content,
                "version": m.version,
                "display_name": m.display_name,
                "description": m.description,
                "tier": m.tier.value,
                "entry_count": len(m.entries),
            }
            for m in ExtensionHub.get().data_pack_manifests()
        ]

    # ------------------------------------------------------------------
    # Trigger runtime (CDC / watch)
    # ------------------------------------------------------------------
    def build_watch_runtime(
        self,
        gpkg_path: "str | Path",
        triggers: Any,
        **kwargs: Any,
    ) -> "HeadlessRuntime":
        """Wire a headless trigger runtime over a GeoPackage.

        Thin pass-through to :func:`gispulse.runtime.build_runtime`; see
        that function for the full keyword surface (``poll_interval``,
        ``webhook_allowlist``, ``batch_limit``, …).
        """
        from gispulse.runtime.headless_runtime import build_runtime

        return build_runtime(gpkg_path, triggers, **kwargs)


@lru_cache(maxsize=1)
def get_app() -> GISPulseApp:
    """Return the process-wide default :class:`GISPulseApp` instance."""
    return GISPulseApp()


# --------------------------------------------------------------------------
# Module-level convenience — the pip façade's verb-shaped entry points.
# Each delegates to the default app so ``from gispulse import apply, run``
# works without the caller constructing anything.
# --------------------------------------------------------------------------
def apply(name: str, gdf: "gpd.GeoDataFrame", **params: Any) -> "gpd.GeoDataFrame":
    """Apply a single capability — shortcut for ``get_app().apply_capability``."""
    return get_app().apply_capability(name, gdf, **params)


def run(
    spec: "PipelineSpec | str | Path | dict | list",
    inputs: "dict[str, gpd.GeoDataFrame]",
    params: dict[str, Any] | None = None,
) -> "dict[str, gpd.GeoDataFrame]":
    """Run a pipeline — shortcut for ``get_app().run_pipeline``."""
    return get_app().run_pipeline(spec, inputs, params)


__all__ = ["GISPulseApp", "get_app", "apply", "run"]
