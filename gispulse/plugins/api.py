"""Transitional public API for external GISPulse plugin authors.

The goal is to give plugins one documented import path while the runtime
keeps its existing internal module layout.
"""

from capabilities.base import Capability
from capabilities.registry import register as register_capability
from core.plugin_contracts import PluginHostContext

_LAZY_EXPORTS = {
    "ApiCartoGeoJsonClient": "gispulse.plugins.sources",
    "CatalogEntry": "gispulse.plugins.sources",
    "FluxEntry": "gispulse.plugins.sources",
    "OGCSourceConfig": "gispulse.plugins.sources",
    "fetch_wfs": "gispulse.plugins.sources",
    "get_catalog_entry": "gispulse.plugins.sources",
    "get_flux_entry": "gispulse.plugins.sources",
    "PipelineExecutor": "gispulse.plugins.pipeline",
    "PipelineSpec": "gispulse.plugins.pipeline",
    "StepSpec": "gispulse.plugins.pipeline",
    "is_angular": "gispulse.plugins.spatial",
    "suggest_metric_crs": "gispulse.plugins.spatial",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value

__all__ = [
    "Capability",
    "PluginHostContext",
    "register_capability",
    *_LAZY_EXPORTS,
]
