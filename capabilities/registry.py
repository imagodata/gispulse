"""
Capability registry for GISPulse.

Provides a global REGISTRY dict and helper functions to register, retrieve,
and list capabilities. Capabilities auto-register via the @register decorator.

Built on :class:`core.registry.PluginRegistry` for thread-safe operations
and entry-point discovery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.observability import MetricsCollector
from core.logging import get_logger
from core.registry import PluginRegistry

if TYPE_CHECKING:
    from capabilities.base import Capability

log = get_logger(__name__)
_metrics = MetricsCollector.get()

# Global registry: name -> capability class
REGISTRY: PluginRegistry[type["Capability"]] = PluginRegistry("capabilities")


def register(cls: type["Capability"]) -> type["Capability"]:
    """Class decorator that registers a Capability in the global REGISTRY.

    Usage::

        @register
        class MyCapability(Capability):
            name = "my_capability"
            ...

    Args:
        cls: Capability subclass to register.

    Returns:
        The class unchanged (decorator pattern).

    Raises:
        ValueError: If a capability with the same name is already registered.
    """
    cap_name: str = getattr(cls, "name", None)  # type: ignore[assignment]
    if not cap_name:
        raise ValueError(f"Capability class {cls.__name__} must define a 'name' attribute.")
    REGISTRY.register(cap_name, cls)
    log.debug("capability_registered", capability=cap_name, class_name=cls.__name__)
    return cls


def get(name: str) -> "Capability":
    """Instantiate and return a capability by name.

    Args:
        name: Registered capability name (e.g. 'buffer', 'filter').

    Returns:
        Fresh instance of the requested Capability.

    Raises:
        KeyError: If no capability with this name is registered.
    """
    _metrics.inc("capabilities_executed_total")
    cls = REGISTRY.get(name)
    return cls()


def list_all() -> list[dict]:
    """Return metadata for all registered capabilities.

    Returns:
        List of dicts with keys ``name``, ``description``, ``schema``.
    """
    _ensure_defaults_loaded()
    return [
        {
            "name": cls.name,
            "description": cls.description,
            "schema": cls().get_schema(),
        }
        for cls in REGISTRY.values()
    ]


def _discover_plugins() -> list[dict[str, str]]:
    """Discover capabilities from installed packages via entry-points."""
    # Reset flag to allow re-discovery (needed for testing with mocked entry-points)
    REGISTRY._plugins_discovered = False
    return REGISTRY.discover_plugins("gispulse.capabilities")


def list_plugins() -> list[dict[str, str]]:
    """Return metadata for all installed plugin entry-points."""
    results: list[dict[str, str]] = []
    try:
        from importlib.metadata import entry_points

        eps = entry_points(group="gispulse.capabilities")
        for ep in eps:
            results.append({"name": ep.name, "module": ep.value})
    except Exception:
        pass
    return results


import threading as _threading

_registry_lock = _threading.Lock()
_defaults_loaded = False
_plugins_discovered = False


def _ensure_defaults_loaded() -> None:
    """Import built-in capability modules so they self-register, then discover plugins."""
    global _defaults_loaded, _plugins_discovered
    if _defaults_loaded and _plugins_discovered:
        return
    with _registry_lock:
        if not _defaults_loaded:
            import capabilities.vector  # noqa: F401
            import capabilities.validation  # noqa: F401
            import capabilities.network_topology  # noqa: F401
            import capabilities.polygon_topology  # noqa: F401
            import capabilities.spatial_stats  # noqa: F401
            import capabilities.density  # noqa: F401
            import capabilities.classification  # noqa: F401
            try:
                import capabilities.raster  # noqa: F401
            except ImportError:
                pass
            try:
                import capabilities.network  # noqa: F401
            except ImportError:
                pass
            try:
                import capabilities.postgis_sql  # noqa: F401
            except ImportError:
                pass
            try:
                import capabilities.clustering  # noqa: F401
            except ImportError:
                pass
            _defaults_loaded = True
        if not _plugins_discovered:
            _discover_plugins()
            _plugins_discovered = True
