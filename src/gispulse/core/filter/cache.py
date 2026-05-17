"""
Filter Result Cache — LRU with TTL, per-layer invalidation.

Ported from FilterMate infrastructure/cache/query_cache.py,
adapted for GISPulse (stores FilterResult with GeoDataFrame).
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from gispulse.core.filter.result import FilterResult


@dataclass(frozen=True)
class CacheStats:
    """Statistics about cache usage."""

    hits: int = 0
    misses: int = 0
    size: int = 0
    max_size: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def utilization(self) -> float:
        return self.size / self.max_size if self.max_size > 0 else 0.0


@dataclass
class _CacheEntry:
    result: FilterResult
    expires_at: float  # time.monotonic() deadline
    layer_key: str = ""
    created_at: float = field(default_factory=time.monotonic)


class FilterCache:
    """Thread-safe LRU cache with TTL for FilterResult objects.

    Args:
        max_size:           Maximum number of cached entries.
        default_ttl_seconds: Default time-to-live in seconds.
    """

    def __init__(
        self,
        max_size: int = 256,
        default_ttl_seconds: float = 300.0,
    ) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl_seconds
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[FilterResult]:
        """Retrieve a cached result, or None if missing/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._store.move_to_end(key)
            self._hits += 1
            return entry.result

    def set(
        self,
        key: str,
        result: FilterResult,
        ttl_seconds: Optional[float] = None,
    ) -> None:
        """Store a result in the cache."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        entry = _CacheEntry(
            result=result,
            expires_at=time.monotonic() + ttl,
            layer_key=result.layer_key,
        )
        with self._lock:
            if key in self._store:
                del self._store[key]
            self._store[key] = entry
            self._store.move_to_end(key)
            # Evict LRU if over capacity
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], FilterResult],
        ttl_seconds: Optional[float] = None,
    ) -> FilterResult:
        """Return cached result or compute, cache, and return."""
        result = self.get(key)
        if result is not None:
            return result
        result = compute_fn()
        self.set(key, result, ttl_seconds)
        return result

    def invalidate_layer(self, layer_key: str) -> int:
        """Remove all entries for a given layer. Returns count removed."""
        with self._lock:
            keys_to_remove = [
                k for k, v in self._store.items() if v.layer_key == layer_key
            ]
            for k in keys_to_remove:
                del self._store[k]
            return len(keys_to_remove)

    def clear(self) -> int:
        """Remove all entries. Returns count removed."""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            self._hits = 0
            self._misses = 0
            return count

    def get_stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                size=len(self._store),
                max_size=self._max_size,
            )

    @staticmethod
    def make_key(layer_key: str, expression_raw: str, **extra: Any) -> str:
        """Build a deterministic cache key from components."""
        parts = f"{layer_key}|{expression_raw}"
        if extra:
            parts += "|" + "|".join(f"{k}={v}" for k, v in sorted(extra.items()))
        return hashlib.sha256(parts.encode()).hexdigest()[:24]


class NullCache(FilterCache):
    """No-op cache for testing or when caching is disabled."""

    def __init__(self) -> None:
        super().__init__(max_size=0, default_ttl_seconds=0)

    def get(self, key: str) -> Optional[FilterResult]:
        return None

    def set(self, key: str, result: FilterResult, ttl_seconds: Optional[float] = None) -> None:
        pass

    def get_or_compute(
        self, key: str, compute_fn: Callable[[], FilterResult], ttl_seconds: Optional[float] = None
    ) -> FilterResult:
        return compute_fn()

    def invalidate_layer(self, layer_key: str) -> int:
        return 0

    def clear(self) -> int:
        return 0

    def get_stats(self) -> CacheStats:
        return CacheStats()
