"""Public API for external GISPulse plugin authors.

The single documented import path for every plugin kind — capability,
source, sink, protocol adapter — while the runtime keeps its internal
module layout. Plugins import from here, never from ``capabilities.*``
or ``core.*`` directly (issue #183).
"""

from capabilities.base import Capability
from capabilities.registry import register as register_capability
from core.plugin_contracts import PluginHostContext
from core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    Payload,
    PluginManifest,
    RuleClause,
    SourceDomain,
    SourceResult,
    WriteSpec,
)
from core.sources import (
    DataSink,
    DataSource,
    DeclarativeSink,
    DeclarativeSource,
    Fetcher,
    ProtocolRegistry,
    RegulatorySource,
    SourceEntryRef,
    Writer,
)

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
    # capability authoring
    "Capability",
    "register_capability",
    # host extension context
    "PluginHostContext",
    # plugin manifest + ETL data shapes
    "PluginManifest",
    "AccessProtocol",
    "AccessSpec",
    "Payload",
    "RuleClause",
    "SourceDomain",
    "SourceResult",
    "WriteSpec",
    # source / sink / protocol contracts
    "DataSource",
    "RegulatorySource",
    "DataSink",
    "Fetcher",
    "Writer",
    "ProtocolRegistry",
    "SourceEntryRef",
    "DeclarativeSource",
    "DeclarativeSink",
    *_LAZY_EXPORTS,
]
