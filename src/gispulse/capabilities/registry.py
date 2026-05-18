"""
Capability registry for GISPulse.

Provides a global REGISTRY dict and helper functions to register, retrieve,
and list capabilities. Capabilities auto-register via the @register decorator.

Built on :class:`core.registry.PluginRegistry` for thread-safe operations
and entry-point discovery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gispulse.core.observability import MetricsCollector
from gispulse.core.logging import get_logger
from gispulse.core.registry import PluginRegistry

if TYPE_CHECKING:
    from gispulse.capabilities.base import Capability

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
    """Register capability plugins discovered by the unified ExtensionHub.

    Issue #180 — end of the double discovery: the hub
    (:class:`core.plugin_hub.ExtensionHub`) owns entry-point scanning for
    all eleven groups. Here we simply invoke the ``register()`` callable
    of every ACTIVE capability record so the capabilities land in
    :data:`REGISTRY`. Records the hub marked FAILED were already logged
    there and surface as errors in the returned report.
    """
    from gispulse.core.plugin_hub import ExtensionHub
    from gispulse.core.plugin_model import PluginKind, PluginState

    results: list[dict[str, str]] = []
    for rec in ExtensionHub.get().records_by_kind(PluginKind.CAPABILITY):
        module = getattr(rec.entry_point, "value", rec.name)
        if rec.state is not PluginState.ACTIVE:
            results.append(
                {"name": rec.name, "module": module,
                 "status": f"error: {rec.detail or 'plugin not active'}"}
            )
            continue
        try:
            rec.obj()  # the plugin's register() callable
            results.append({"name": rec.name, "module": module, "status": "ok"})
        except Exception as exc:  # noqa: BLE001 — isolate a bad plugin
            log.warning("capability_plugin_register_failed", plugin=rec.name, error=str(exc))
            results.append(
                {"name": rec.name, "module": module, "status": f"error: {exc}"}
            )
    return results


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
            import gispulse.capabilities.vector  # noqa: F401
            import gispulse.capabilities.validation  # noqa: F401
            import gispulse.capabilities.network_topology  # noqa: F401
            import gispulse.capabilities.polygon_topology  # noqa: F401
            import gispulse.capabilities.spatial_stats  # noqa: F401
            import gispulse.capabilities.density  # noqa: F401
            import gispulse.capabilities.classification  # noqa: F401
            try:
                import gispulse.capabilities.raster  # noqa: F401
            except ImportError:
                pass
            try:
                import gispulse.capabilities.network  # noqa: F401
            except ImportError:
                pass
            try:
                import gispulse.capabilities.postgis_sql  # noqa: F401
            except ImportError:
                pass
            try:
                import gispulse.capabilities.clustering  # noqa: F401
            except ImportError:
                pass
            _defaults_loaded = True
        if not _plugins_discovered:
            _discover_plugins()
            _plugins_discovered = True
