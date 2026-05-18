"""Bounded LRU cache for GISPulse.

Provides a dict-compatible LRU cache used for layer GeoDataFrame caching
in the HTTP layer. Evicts oldest entries when maxsize is reached.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


class BoundedLayerCache(dict):
    """Dict-compatible LRU cache with a max size for layer GeoDataFrames.

    Evicts the oldest entry when maxsize is reached. Compatible with
    existing code that uses dict operations (get, __setitem__, __contains__).

    Args:
        maxsize: Maximum number of entries before eviction. Default 50.
    """

    def __init__(self, maxsize: int = 50) -> None:
        super().__init__()
        self._maxsize = maxsize
        self._order: OrderedDict = OrderedDict()

    def __setitem__(self, key: Any, value: Any) -> None:
        if key in self._order:
            self._order.move_to_end(key)
        else:
            if len(self._order) >= self._maxsize:
                oldest = next(iter(self._order))
                self._order.pop(oldest)
                super().pop(oldest, None)
            self._order[key] = None
        super().__setitem__(key, value)

    def __getitem__(self, key: Any) -> Any:
        self._order.move_to_end(key)
        return super().__getitem__(key)

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            self._order.move_to_end(key)
            return super().__getitem__(key)
        return default
