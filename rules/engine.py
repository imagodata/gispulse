"""
Rule engine for GISPulse.

Applies Rule objects to GeoDataFrames by resolving the named capability
from the registry and passing the rule's config as keyword arguments.
"""

from __future__ import annotations

from typing import Callable

import geopandas as gpd

from core.observability import MetricsCollector
from capabilities import get as get_capability
from capabilities.strategy import ExecutionContext
from core.logging import get_logger
from core.models import Rule
from persistence.repository import Repository
from rules.validation import validate_rule

log = get_logger(__name__)
_metrics = MetricsCollector.get()

# Type alias: resolves a layer name to a GeoDataFrame
LayerResolver = Callable[[str], gpd.GeoDataFrame]


class RuleEngine:
    """
    Moteur d'application des règles métier.

    Usage::

        engine = RuleEngine(repository=repo)
        result_gdf = engine.apply(rule, gdf)
        result_gdf = engine.apply_all([rule1, rule2], gdf)

    Cross-layer operations::

        # Provide a resolver so rules can reference other layers
        resolver = lambda name: gpd.read_file("data.gpkg", layer=name)
        result = engine.apply_all(rules, gdf, layer_resolver=resolver)
    """

    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository

    # ------------------------------------------------------------------
    # Single rule
    # ------------------------------------------------------------------

    def apply(
        self,
        rule: Rule,
        gdf: gpd.GeoDataFrame,
        layer_resolver: LayerResolver | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> gpd.GeoDataFrame:
        """Apply a single Rule to a GeoDataFrame.

        Resolves the named capability from the registry and calls
        ``execute(gdf, **rule.config)`` or, when an *execution_context*
        is provided, ``execute_with_context(gdf, ctx)`` so that
        engine-accelerated strategies (DuckDB, PostGIS) are selected
        when eligible.

        If ``rule.config`` contains a ``ref_layer`` key and a *layer_resolver*
        is provided, the referenced layer is loaded and injected as
        ``ref_gdf`` into the capability's params.

        Args:
            rule:              Rule domain object with a valid ``capability`` name.
            gdf:               Input GeoDataFrame.
            layer_resolver:    Optional callback to resolve layer names to GeoDataFrames.
            execution_context: Optional engine context for strategy-based execution.

        Returns:
            Transformed GeoDataFrame.

        Raises:
            ValueError: If the rule fails validation or ref_layer cannot be resolved.
        """
        validation = validate_rule(rule)
        if not validation.valid:
            details = "; ".join(
                f"[{err.field}] {err.message}" for err in validation.errors
            )
            raise ValueError(
                f"Rule '{rule.name}' (id={rule.id}) failed validation: {details}"
            )

        log.debug(
            "rule_applying",
            rule_id=str(rule.id),
            rule_name=rule.name,
            capability=rule.capability,
        )

        # Build effective params — resolve cross-layer references
        params = dict(rule.config)
        # Strip rule-level keys that authors sometimes nest inside config by
        # mistake (the legacy ``**_`` swallow used to hide these). ``order``
        # is the canonical Rule attribute and is read off ``rule.order`` —
        # never from ``rule.config``. Stripping silently to keep
        # backwards-compat with existing rule fixtures and YAML configs;
        # validate_rule() handles deprecation messaging.
        for _legacy_key in ("order", "name", "description", "enabled", "target_layer"):
            params.pop(_legacy_key, None)
        ref_layer = params.pop("ref_layer", None)
        if ref_layer is not None:
            if layer_resolver is None:
                raise ValueError(
                    f"Rule '{rule.name}' references layer '{ref_layer}' but no "
                    f"layer_resolver was provided. Use --layer-source or provide "
                    f"a multi-layer file."
                )
            log.debug("resolving_ref_layer", ref_layer=ref_layer, rule=rule.name)
            params["ref_gdf"] = layer_resolver(ref_layer)

        cap = get_capability(rule.capability)

        # Use engine-accelerated path when a context is available
        if execution_context is not None:
            ctx = ExecutionContext(
                engine=execution_context.engine,
                feature_count=len(gdf),
                has_spatial_index=execution_context.has_spatial_index,
                params=params,
            )
            result = cap.execute_with_context(gdf, ctx)
        else:
            result = cap.execute_safe(gdf, **params)

        _metrics.inc("rules_applied_total")
        return result

    # ------------------------------------------------------------------
    # Multiple rules (pipeline)
    # ------------------------------------------------------------------

    def apply_all(
        self,
        rules: list[Rule],
        gdf: gpd.GeoDataFrame,
        layer_resolver: LayerResolver | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> gpd.GeoDataFrame:
        """Apply a list of Rules in order to a GeoDataFrame.

        Rules are sorted by ``rule.order`` (ascending)
        before processing. Disabled rules are skipped.

        Args:
            rules:             List of Rule objects to apply.
            gdf:               Input GeoDataFrame.
            layer_resolver:    Optional callback to resolve cross-layer references.
            execution_context: Optional engine context for strategy-based execution.

        Returns:
            GeoDataFrame after all enabled rules have been applied in order.
        """
        ordered = sorted(rules, key=lambda r: r.order)
        for rule in ordered:
            if rule.enabled:
                gdf = self.apply(
                    rule, gdf,
                    layer_resolver=layer_resolver,
                    execution_context=execution_context,
                )
        return gdf
