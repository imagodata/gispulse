"""Transitional public API for external GISPulse plugin authors.

The goal is to give plugins one documented import path while the runtime
keeps its existing internal module layout.
"""

from capabilities.base import Capability
from capabilities.registry import register as register_capability
from core.plugin_contracts import PluginHostContext

__all__ = ["Capability", "PluginHostContext", "register_capability"]
