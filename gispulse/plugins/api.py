"""Transitional public API for external GISPulse plugin authors.

The goal is to give plugins one documented import path while the runtime
keeps its existing internal module layout.
"""

from capabilities.base import Capability
from capabilities.registry import register as register_capability
from core.plugin_contracts import PluginHostContext
from gispulse.plugins.pipeline import PipelineExecutor, PipelineSpec, StepSpec
from gispulse.plugins.sources import (
    CatalogEntry,
    FluxEntry,
    OGCSourceConfig,
    fetch_wfs,
    get_catalog_entry,
    get_flux_entry,
)
from gispulse.plugins.spatial import is_angular, suggest_metric_crs

__all__ = [
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
