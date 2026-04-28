"""
Session manager — orchestrates the full E2E pipeline with engine acceleration.

Provides a high-level API to:
1. Create a DuckDB session (or use Python-only mode)
2. Load input file
3. Execute rules with the best available strategy (DuckDB > Python)
4. Export result to any supported format
5. Clean up session

Usage::

    sm = SessionManager(engine="duckdb")
    result = sm.run_pipeline(
        input_path="input.gpkg",
        rules_path="rules.json",
        output_path="output.gpkg",
        layer="parcels",
    )
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import geopandas as gpd

if TYPE_CHECKING:
    from core.pipeline import PipelineSpec

from core.logging import get_logger
from core.models import Rule

log = get_logger(__name__)


@dataclass
class PipelineResult:
    """Result of a session pipeline execution."""

    gdf: gpd.GeoDataFrame
    output_path: str | None = None
    rules_applied: int = 0
    features_in: int = 0
    features_out: int = 0
    engine_used: str = "python"
    layers_loaded: list[str] = field(default_factory=list)


@dataclass
class MultiLayerResult:
    """Result of a multi-layer pipeline execution."""

    layers: dict[str, gpd.GeoDataFrame]
    output_path: str | None = None
    rules_applied: int = 0
    total_features_in: int = 0
    total_features_out: int = 0
    engine_used: str = "python"
    layer_results: dict[str, PipelineResult] = field(default_factory=dict)
    styles_copied: int = 0


class SessionManager:
    """Orchestrates the full load -> execute -> export pipeline.

    Supports two engine modes:
    - ``"python"``  — GeoPandas only, no DuckDB overhead (default).
    - ``"duckdb"``  — DuckDB session with spatial extension, strategies
                      automatically route capable operations through DuckDB.

    The DuckDB mode registers loaded GeoDataFrames as DuckDB tables,
    builds an :class:`ExecutionContext`, and passes it to the RuleEngine
    so that capabilities with DuckDB strategies use them automatically.
    """

    def __init__(self, engine: str = "python") -> None:
        if engine not in ("python", "duckdb"):
            raise ValueError(f"Unknown engine {engine!r}. Use 'python' or 'duckdb'.")
        self._engine_mode = engine
        self._workspace_id = f"session-{uuid.uuid4().hex[:8]}"

    @property
    def engine_mode(self) -> str:
        return self._engine_mode

    def run_pipeline(
        self,
        input_path: str | Path,
        rules: list[Rule],
        output_path: str | Path | None = None,
        layer: str | None = None,
        output_layer: str | None = None,
        crs: str | None = None,
        ref_sources: dict[str, Path] | None = None,
    ) -> PipelineResult:
        """Execute the full pipeline: load -> apply rules -> export.

        Args:
            input_path:   Path to input spatial file.
            rules:        List of Rule objects to apply.
            output_path:  Path to output file (optional, skip export if None).
            layer:        Layer name for multi-layer inputs.
            output_layer: Layer name in output file.
            crs:          Force input CRS.

        Returns:
            PipelineResult with the processed GeoDataFrame and metadata.
        """
        from persistence.io import read_vector
        from rules.engine import RuleEngine

        input_path = Path(input_path)
        enabled = [r for r in rules if r.enabled]

        # -- Step 1: Load input --
        gdf = read_vector(str(input_path), layer=layer, crs=crs)
        features_in = len(gdf)
        layers_loaded = [layer or "default"]

        log.info(
            "pipeline_input_loaded",
            path=str(input_path),
            features=features_in,
            layer=layer,
        )

        # -- Step 2: Build cross-layer resolver --
        has_cross_layer = any("ref_layer" in r.config for r in enabled)
        _ref_sources = ref_sources or {}
        layer_resolver = None
        if has_cross_layer or _ref_sources:
            _layer_cache: dict[str, gpd.GeoDataFrame] = {}

            def layer_resolver(layer_name: str) -> gpd.GeoDataFrame:
                if layer_name not in _layer_cache:
                    if layer_name in _ref_sources:
                        src = _ref_sources[layer_name]
                        log.info(
                            "resolving_ref_layer_external",
                            layer=layer_name,
                            source=str(src),
                        )
                        _layer_cache[layer_name] = read_vector(
                            str(src), crs=crs,
                        )
                    else:
                        log.info("resolving_ref_layer", layer=layer_name)
                        _layer_cache[layer_name] = read_vector(
                            str(input_path), layer=layer_name, crs=crs,
                        )
                    layers_loaded.append(layer_name)
                return _layer_cache[layer_name]

        # -- Step 3: Execute rules --
        engine_used = self._engine_mode

        if self._engine_mode == "duckdb":
            result_gdf = self._run_with_duckdb(
                gdf, enabled, layer_resolver,
            )
        else:
            engine = RuleEngine()
            result_gdf = engine.apply_all(
                enabled, gdf, layer_resolver=layer_resolver,
            )

        features_out = len(result_gdf)

        log.info(
            "pipeline_rules_applied",
            rules_applied=len(enabled),
            features_in=features_in,
            features_out=features_out,
            engine=engine_used,
        )

        # -- Step 4: Export --
        out_str = self._export(input_path, output_path, output_layer, result_gdf)
        if out_str:
            log.info("pipeline_output_written", path=out_str, features=features_out)

        return PipelineResult(
            gdf=result_gdf,
            output_path=out_str,
            rules_applied=len(enabled),
            features_in=features_in,
            features_out=features_out,
            engine_used=engine_used,
            layers_loaded=layers_loaded,
        )

    def run_pipeline_v2(
        self,
        input_path: str | Path,
        spec: "PipelineSpec",
        output_path: str | Path | None = None,
        layer: str | None = None,
        output_layer: str | None = None,
        crs: str | None = None,
        ref_sources: dict[str, Path] | None = None,
    ) -> PipelineResult:
        """Execute a PipelineSpec v2 natively via PipelineExecutor.

        Unlike :meth:`run_pipeline` which takes Rule objects, this method
        works directly with a :class:`PipelineSpec` — no conversion needed.
        Supports linear and DAG pipelines.

        Args:
            input_path:   Path to input spatial file.
            spec:         PipelineSpec v2 to execute.
            output_path:  Path to output file (optional, skip export if None).
            layer:        Layer name for multi-layer inputs.
            output_layer: Layer name in output file.
            crs:          Force input CRS.
            ref_sources:  External reference layer paths.

        Returns:
            PipelineResult with the processed GeoDataFrame and metadata.
        """
        from orchestration.pipeline_executor import PipelineExecutor
        from persistence.io import read_vector

        input_path = Path(input_path)

        # -- Step 1: Load input --
        gdf = read_vector(str(input_path), layer=layer, crs=crs)
        features_in = len(gdf)
        layers_loaded = [layer or "default"]

        log.info(
            "pipeline_v2_input_loaded",
            path=str(input_path),
            features=features_in,
            layer=layer,
            pipeline=spec.name,
            is_dag=spec.is_dag,
        )

        # -- Step 2: Build inputs dict with ref layers --
        inputs: dict[str, gpd.GeoDataFrame] = {"input": gdf}
        _ref_sources = ref_sources or {}

        # Merge CLI ref_sources into spec.ref_layers
        for name, rpath in _ref_sources.items():
            spec.ref_layers[name] = str(rpath)

        # Load ref layers. For multi-layer containers (GPKG / SpatiaLite),
        # try the alias as the layer name first so a single GPKG can expose
        # several named ref layers under their own aliases.
        for alias, source in spec.ref_layers.items():
            try:
                source_str = str(source)
                is_container = source_str.lower().endswith((".gpkg", ".sqlite"))
                try:
                    inputs[alias] = read_vector(
                        source_str,
                        layer=alias if is_container else None,
                        crs=crs,
                    )
                except Exception:
                    # Fall back to the container's default layer.
                    inputs[alias] = read_vector(source_str, crs=crs)
                layers_loaded.append(alias)
                log.info("pipeline_v2_ref_loaded", alias=alias, source=source)
            except Exception as exc:
                log.warning("pipeline_v2_ref_failed", alias=alias, error=str(exc))

        # -- Step 3: Execute via PipelineExecutor --
        execution_context = None
        engine_used = self._engine_mode

        if self._engine_mode == "duckdb":
            from capabilities.strategy import ExecutionContext
            from persistence.engine_factory import create_spatial_engine

            session = create_spatial_engine("duckdb")
            session.open()
            session.register_gdf("input_layer", gdf)
            execution_context = ExecutionContext(
                engine=session,
                feature_count=len(gdf),
                has_spatial_index=False,
                params={},
            )

        try:
            executor = PipelineExecutor(execution_context=execution_context)
            results = executor.execute(spec, inputs)
        finally:
            if execution_context and hasattr(execution_context.engine, "close"):
                execution_context.engine.close()

        # The last result is the final output
        result_gdf = list(results.values())[-1] if results else gdf
        features_out = len(result_gdf)
        steps_executed = len(results)

        log.info(
            "pipeline_v2_executed",
            pipeline=spec.name,
            steps_executed=steps_executed,
            features_in=features_in,
            features_out=features_out,
            engine=engine_used,
            is_dag=spec.is_dag,
        )

        # -- Step 4: Export --
        out_str = self._export(input_path, output_path, output_layer, result_gdf)
        if out_str:
            log.info("pipeline_v2_output_written", path=out_str, features=features_out)

        return PipelineResult(
            gdf=result_gdf,
            output_path=out_str,
            rules_applied=steps_executed,
            features_in=features_in,
            features_out=features_out,
            engine_used=engine_used,
            layers_loaded=layers_loaded,
        )

    @staticmethod
    def _export(
        input_path: Path,
        output_path: str | Path | None,
        output_layer: str | None,
        result_gdf: gpd.GeoDataFrame,
    ) -> str | None:
        """Export result GeoDataFrame to file. Returns output path or None."""
        if output_path is None:
            return None

        from persistence.io import write_vector

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_vector(result_gdf, str(output_path), layer=output_layer)

        # Copy styles from source GPKG to output GPKG
        if (
            input_path.suffix.lower() == ".gpkg"
            and output_path.suffix.lower() == ".gpkg"
        ):
            from persistence.gpkg import copy_styles as _copy_styles

            copied = _copy_styles(str(input_path), str(output_path))
            if copied:
                log.info("pipeline_styles_copied", count=copied)

        return str(output_path)

    def run_pipeline_multi(
        self,
        input_path: str | Path,
        rules: list[Rule],
        output_path: str | Path | None = None,
        crs: str | None = None,
        copy_styles: bool = True,
    ) -> MultiLayerResult:
        """Execute the pipeline on ALL layers of a multi-layer file."""
        from persistence.io import read_all_vectors, write_all_vectors

        input_path = Path(input_path)
        enabled = [r for r in rules if r.enabled]

        all_layers = read_all_vectors(str(input_path), crs=crs)
        layer_names = list(all_layers.keys())

        log.info(
            "multi_pipeline_loaded",
            path=str(input_path),
            layers=layer_names,
            layer_count=len(layer_names),
        )

        result_layers: dict[str, gpd.GeoDataFrame] = {}
        layer_results: dict[str, PipelineResult] = {}
        total_in = 0
        total_out = 0
        total_rules = 0

        def layer_resolver(layer_name: str) -> gpd.GeoDataFrame:
            if layer_name in all_layers:
                return all_layers[layer_name]
            raise ValueError(f"Layer '{layer_name}' not found in input file.")

        for lname, gdf in all_layers.items():
            layer_rules = [
                r
                for r in enabled
                if not r.config.get("target_layer")
                or r.config["target_layer"] == lname
            ]

            features_in = len(gdf)
            total_in += features_in

            if layer_rules:
                from rules.engine import RuleEngine

                if self._engine_mode == "duckdb":
                    result_gdf = self._run_with_duckdb(
                        gdf, layer_rules, layer_resolver
                    )
                else:
                    engine = RuleEngine()
                    result_gdf = engine.apply_all(
                        layer_rules, gdf, layer_resolver=layer_resolver
                    )
            else:
                result_gdf = gdf

            features_out = len(result_gdf)
            total_out += features_out
            total_rules += len(layer_rules)

            result_layers[lname] = result_gdf
            layer_results[lname] = PipelineResult(
                gdf=result_gdf,
                rules_applied=len(layer_rules),
                features_in=features_in,
                features_out=features_out,
                engine_used=self._engine_mode,
                layers_loaded=[lname],
            )

            log.info(
                "multi_pipeline_layer_done",
                layer=lname,
                features_in=features_in,
                features_out=features_out,
                rules=len(layer_rules),
            )

        out_str = None
        styles_copied = 0
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            write_all_vectors(result_layers, str(output_path))
            out_str = str(output_path)

            if copy_styles and input_path.suffix.lower() == ".gpkg":
                from persistence.gpkg import copy_styles as _copy_styles

                styles_copied = _copy_styles(str(input_path), out_str)
                if styles_copied:
                    log.info(
                        "multi_pipeline_styles_copied", count=styles_copied
                    )

            log.info(
                "multi_pipeline_output_written",
                path=out_str,
                layers=len(result_layers),
                total_features=total_out,
            )

        return MultiLayerResult(
            layers=result_layers,
            output_path=out_str,
            rules_applied=total_rules,
            total_features_in=total_in,
            total_features_out=total_out,
            engine_used=self._engine_mode,
            layer_results=layer_results,
            styles_copied=styles_copied,
        )

    def _run_with_duckdb(
        self,
        gdf: gpd.GeoDataFrame,
        rules: list[Rule],
        layer_resolver: Any | None,
    ) -> gpd.GeoDataFrame:
        """Execute rules using DuckDB-accelerated strategies.

        Creates a DuckDB session, builds an ExecutionContext, and
        delegates to RuleEngine.apply_all() with the context so that
        capabilities with DuckDB strategies use them automatically.
        """
        from capabilities.strategy import ExecutionContext
        from persistence.engine_factory import create_spatial_engine
        from rules.engine import RuleEngine

        session = create_spatial_engine("duckdb")
        session.open()

        try:
            # Register input GDF in DuckDB for SQL-based strategies
            session.register_gdf("input_layer", gdf)

            ctx = ExecutionContext(
                engine=session,
                feature_count=len(gdf),
                has_spatial_index=False,
                params={},
            )

            engine = RuleEngine()
            result = engine.apply_all(
                rules, gdf,
                layer_resolver=layer_resolver,
                execution_context=ctx,
            )

            return result
        finally:
            session.close()
