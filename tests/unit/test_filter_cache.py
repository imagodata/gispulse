"""Tests for core.filter.cache — FilterCache LRU + TTL.

Thread-safe LRU cache with TTL + per-layer invalidation. Bugs here
either serve stale data (TTL ignored), leak memory (LRU eviction
broken), or silently cross tenants (wrong layer invalidation).
"""
from __future__ import annotations

import threading
import time

import geopandas as gpd
from shapely.geometry import Point

from gispulse.core.filter.cache import CacheStats, FilterCache, NullCache
from gispulse.core.filter.result import FilterResult


def _make_result(layer_key: str = "l", expr: str = "x = 1") -> FilterResult:
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [Point(0, 0)]}, crs="EPSG:4326"
    )
    return FilterResult.success(
        gdf=gdf, layer_key=layer_key, expression_raw=expr, backend_name="test"
    )


# ---------------------------------------------------------------------------
# CacheStats
# ---------------------------------------------------------------------------


class TestCacheStats:
    def test_hit_rate_no_activity(self):
        s = CacheStats()
        assert s.hit_rate == 0.0

    def test_hit_rate_computed(self):
        s = CacheStats(hits=3, misses=1)
        assert s.hit_rate == 0.75

    def test_utilization_empty_capacity(self):
        s = CacheStats(size=0, max_size=0)
        assert s.utilization == 0.0

    def test_utilization_half_full(self):
        s = CacheStats(size=50, max_size=100)
        assert s.utilization == 0.5


# ---------------------------------------------------------------------------
# FilterCache basics
# ---------------------------------------------------------------------------


class TestFilterCacheBasic:
    def test_get_missing_returns_none(self):
        cache = FilterCache()
        assert cache.get("nope") is None

    def test_set_then_get(self):
        cache = FilterCache()
        r = _make_result()
        cache.set("k1", r)
        assert cache.get("k1") is r

    def test_miss_increments_miss_counter(self):
        cache = FilterCache()
        cache.get("missing")
        cache.get("missing")
        stats = cache.get_stats()
        assert stats.misses == 2
        assert stats.hits == 0

    def test_hit_increments_hit_counter(self):
        cache = FilterCache()
        cache.set("k", _make_result())
        cache.get("k")
        cache.get("k")
        stats = cache.get_stats()
        assert stats.hits == 2
        assert stats.misses == 0

    def test_set_replaces_existing_key(self):
        cache = FilterCache()
        cache.set("k", _make_result(expr="first"))
        cache.set("k", _make_result(expr="second"))
        assert cache.get("k").expression_raw == "second"
        stats = cache.get_stats()
        assert stats.size == 1


# ---------------------------------------------------------------------------
# TTL expiration
# ---------------------------------------------------------------------------


class TestFilterCacheTtl:
    def test_entry_expires_after_ttl(self):
        cache = FilterCache(default_ttl_seconds=0.05)
        cache.set("k", _make_result())
        time.sleep(0.07)
        assert cache.get("k") is None

    def test_expired_entry_counted_as_miss(self):
        cache = FilterCache(default_ttl_seconds=0.05)
        cache.set("k", _make_result())
        time.sleep(0.07)
        cache.get("k")
        stats = cache.get_stats()
        assert stats.misses == 1

    def test_custom_ttl_per_set(self):
        cache = FilterCache(default_ttl_seconds=10)
        cache.set("short", _make_result(), ttl_seconds=0.05)
        cache.set("long", _make_result())
        time.sleep(0.07)
        assert cache.get("short") is None
        assert cache.get("long") is not None


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


class TestFilterCacheLRU:
    def test_capacity_eviction(self):
        cache = FilterCache(max_size=2)
        cache.set("a", _make_result(expr="a"))
        cache.set("b", _make_result(expr="b"))
        cache.set("c", _make_result(expr="c"))
        # "a" was evicted (LRU: a was added first, never accessed)
        assert cache.get("a") is None
        assert cache.get("b") is not None
        assert cache.get("c") is not None

    def test_get_refreshes_lru_order(self):
        cache = FilterCache(max_size=2)
        cache.set("a", _make_result(expr="a"))
        cache.set("b", _make_result(expr="b"))
        # Touch "a" → "b" becomes oldest
        cache.get("a")
        cache.set("c", _make_result(expr="c"))
        # "b" evicted, "a" still present
        assert cache.get("a") is not None
        assert cache.get("b") is None

    def test_size_reported_in_stats(self):
        cache = FilterCache(max_size=10)
        cache.set("a", _make_result(expr="a"))
        cache.set("b", _make_result(expr="b"))
        stats = cache.get_stats()
        assert stats.size == 2
        assert stats.max_size == 10


# ---------------------------------------------------------------------------
# get_or_compute
# ---------------------------------------------------------------------------


class TestGetOrCompute:
    def test_cache_miss_invokes_compute(self):
        cache = FilterCache()
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return _make_result(expr="computed")

        result = cache.get_or_compute("k", compute)
        assert result.expression_raw == "computed"
        assert calls["n"] == 1

    def test_cache_hit_skips_compute(self):
        cache = FilterCache()
        cache.set("k", _make_result(expr="prebuilt"))

        def compute():
            raise AssertionError("compute must not be called on cache hit")

        result = cache.get_or_compute("k", compute)
        assert result.expression_raw == "prebuilt"

    def test_compute_result_is_stored(self):
        cache = FilterCache()
        cache.get_or_compute("k", lambda: _make_result(expr="v"))
        assert cache.get("k").expression_raw == "v"


# ---------------------------------------------------------------------------
# invalidate_layer
# ---------------------------------------------------------------------------


class TestInvalidateLayer:
    def test_removes_all_entries_for_layer(self):
        cache = FilterCache()
        cache.set("k1", _make_result(layer_key="A"))
        cache.set("k2", _make_result(layer_key="A"))
        cache.set("k3", _make_result(layer_key="B"))
        removed = cache.invalidate_layer("A")
        assert removed == 2
        assert cache.get("k1") is None
        assert cache.get("k2") is None
        # Layer B untouched
        assert cache.get("k3") is not None

    def test_unknown_layer_returns_zero(self):
        cache = FilterCache()
        cache.set("k", _make_result(layer_key="A"))
        assert cache.invalidate_layer("never-existed") == 0


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_removes_all_and_resets_counters(self):
        cache = FilterCache()
        cache.set("a", _make_result())
        cache.set("b", _make_result())
        cache.get("a")
        cache.get("missing")
        removed = cache.clear()
        assert removed == 2
        stats = cache.get_stats()
        assert stats.size == 0
        assert stats.hits == 0
        assert stats.misses == 0

    def test_clear_empty_returns_zero(self):
        assert FilterCache().clear() == 0


# ---------------------------------------------------------------------------
# make_key
# ---------------------------------------------------------------------------


class TestMakeKey:
    def test_deterministic(self):
        k1 = FilterCache.make_key("layer", "expr")
        k2 = FilterCache.make_key("layer", "expr")
        assert k1 == k2

    def test_different_layers_different_keys(self):
        k1 = FilterCache.make_key("A", "expr")
        k2 = FilterCache.make_key("B", "expr")
        assert k1 != k2

    def test_different_expressions_different_keys(self):
        k1 = FilterCache.make_key("l", "a = 1")
        k2 = FilterCache.make_key("l", "a = 2")
        assert k1 != k2

    def test_extra_kwargs_change_key(self):
        k1 = FilterCache.make_key("l", "e", bbox="1,2,3,4")
        k2 = FilterCache.make_key("l", "e", bbox="5,6,7,8")
        assert k1 != k2

    def test_extra_kwargs_are_order_insensitive(self):
        """kwargs sorted before hashing → different call order → same key."""
        k1 = FilterCache.make_key("l", "e", a=1, b=2)
        k2 = FilterCache.make_key("l", "e", b=2, a=1)
        assert k1 == k2

    def test_key_is_short_hex(self):
        key = FilterCache.make_key("l", "e")
        assert len(key) == 24
        assert all(c in "0123456789abcdef" for c in key)


# ---------------------------------------------------------------------------
# Thread safety smoke test
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_set_and_get_no_race(self):
        cache = FilterCache(max_size=100)

        def worker(tid: int):
            for i in range(50):
                cache.set(f"{tid}_{i}", _make_result(expr=f"v_{tid}_{i}"))
                cache.get(f"{tid}_{i}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = cache.get_stats()
        # Each thread: 50 sets + 50 gets → 250 hits total (no misses)
        assert stats.size <= 100
        assert stats.hits == 250


# ---------------------------------------------------------------------------
# NullCache
# ---------------------------------------------------------------------------


class TestNullCache:
    def test_get_always_none(self):
        cache = NullCache()
        cache.set("k", _make_result())
        assert cache.get("k") is None

    def test_get_or_compute_always_runs_compute(self):
        cache = NullCache()
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return _make_result()

        cache.get_or_compute("k", compute)
        cache.get_or_compute("k", compute)
        assert calls["n"] == 2

    def test_invalidate_returns_zero(self):
        assert NullCache().invalidate_layer("x") == 0

    def test_clear_returns_zero(self):
        assert NullCache().clear() == 0

    def test_stats_is_empty(self):
        stats = NullCache().get_stats()
        assert stats.size == 0
        assert stats.max_size == 0
