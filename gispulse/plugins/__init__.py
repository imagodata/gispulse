"""Curated plugin-author API for GISPulse extensions."""

from gispulse.plugins.api import (
    ApiCartoGeoJsonClient,
    CatalogEntry,
    Capability,
    FluxEntry,
    OGCSourceConfig,
    PipelineExecutor,
    PipelineSpec,
    PluginHostContext,
    StepSpec,
    fetch_wfs,
    get_catalog_entry,
    get_flux_entry,
    is_angular,
    register_capability,
    suggest_metric_crs,
)

__all__ = [
    "ApiCartoGeoJsonClient",
    "CatalogEntry",
    "Capability",
    "FluxEntry",
    "OGCSourceConfig",
    "PipelineExecutor",
    "PipelineSpec",
    "PluginHostContext",
    "StepSpec",
    "fetch_wfs",
    "get_catalog_entry",
    "get_flux_entry",
    "is_angular",
    "register_capability",
    "suggest_metric_crs",
]
