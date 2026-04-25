"""Generic plugin registry for GISPulse.

Provides a type-safe, thread-safe registry with entry-point discovery.
Used by both the capability registry and the catalog provider registry.

Usage::

    from core.registry import PluginRegistry

    # Create a typed registry
    registry: PluginRegistry[MyBaseClass] = PluginRegistry("my_plugins")

    # Register
    registry.register("my_plugin", MyPluginClass)

    # Retrieve
    instance = registry.get("my_plugin")

    # Discover from entry-points
    registry.discover_plugins("gispulse.my_plugins")
"""

from __future__ import annotations

import threading
from typing import Any, Generic, TypeVar

from core.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


class PluginRegistry(Generic[T]):
    """Thread-safe registry for named plugins.

    Args:
        name: Human-readable registry name for logging.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._items: dict[str, T] = {}
        self._lock = threading.Lock()
        self._plugins_discovered = False

    def register(self, key: str, item: T, *, override: bool = False) -> T:
        """Register an item under the given key.

        Args:
            key:      Unique name for the item.
            item:     The item (class, instance, etc.) to register.
            override: If True, allow replacing an existing registration.

        Returns:
            The registered item (for use as a decorator).

        Raises:
            ValueError: If key is already registered and override is False.
        """
        with self._lock:
            if key in self._items and not override:
                raise ValueError(
                    f"[{self.name}] '{key}' is already registered. "
                    f"Pass override=True to replace."
                )
            self._items[key] = item
            log.debug("plugin_registered", registry=self.name, key=key)
        return item

    def get(self, key: str) -> T:
        """Retrieve a registered item by key.

        Raises:
            KeyError: If no item is registered under this key.
        """
        if key not in self._items:
            available = sorted(self._items.keys())
            raise KeyError(
                f"[{self.name}] No item named '{key}'. "
                f"Available: {available}"
            )
        return self._items[key]

    def list_keys(self) -> list[str]:
        """Return sorted list of all registered keys."""
        return sorted(self._items.keys())

    def items(self) -> list[tuple[str, T]]:
        """Return all (key, item) pairs."""
        return list(self._items.items())

    def __getitem__(self, key: str) -> T:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __iter__(self):
        return iter(self._items)

    def keys(self):
        return self._items.keys()

    def values(self):
        return self._items.values()

    def discover_plugins(self, entrypoint_group: str) -> list[dict[str, str]]:
        """Discover and load plugins from installed packages via entry-points.

        Scans the given entry-point group. Each entry-point must point to a
        callable that registers items when invoked.

        Args:
            entrypoint_group: Entry-point group name (e.g. "gispulse.capabilities").

        Returns:
            List of dicts with ``name``, ``module``, and ``status``.
        """
        if self._plugins_discovered:
            return []

        loaded: list[dict[str, str]] = []
        try:
            from importlib.metadata import entry_points

            eps = entry_points(group=entrypoint_group)
            for ep in eps:
                try:
                    register_fn = ep.load()
                    register_fn()
                    log.info("plugin_loaded", registry=self.name, plugin=ep.name, module=ep.value)
                    loaded.append({"name": ep.name, "module": ep.value, "status": "ok"})
                except Exception as exc:
                    log.warning("plugin_load_failed", registry=self.name, plugin=ep.name, error=str(exc))
                    loaded.append({"name": ep.name, "module": ep.value, "status": f"error: {exc}"})
        except Exception:
            pass

        with self._lock:
            self._plugins_discovered = True

        return loaded
