"""Tests for core.cache.BoundedLayerCache — LRU eviction, ordering."""
from __future__ import annotations

from core.cache import BoundedLayerCache


class TestBoundedLayerCacheBasic:
    def test_setitem_and_getitem(self):
        c = BoundedLayerCache(maxsize=3)
        c["a"] = 1
        c["b"] = 2
        assert c["a"] == 1
        assert c["b"] == 2
        assert len(c) == 2

    def test_get_with_default(self):
        c = BoundedLayerCache(maxsize=3)
        c["a"] = 1
        assert c.get("a") == 1
        assert c.get("missing") is None
        assert c.get("missing", "fallback") == "fallback"

    def test_contains(self):
        c = BoundedLayerCache(maxsize=3)
        c["x"] = 42
        assert "x" in c
        assert "y" not in c

    def test_overwrite_existing_key_does_not_evict(self):
        c = BoundedLayerCache(maxsize=2)
        c["a"] = 1
        c["b"] = 2
        c["a"] = 99  # overwrite, not insert
        assert len(c) == 2
        assert c["a"] == 99
        assert c["b"] == 2


class TestBoundedLayerCacheEviction:
    def test_evicts_oldest_when_full(self):
        c = BoundedLayerCache(maxsize=2)
        c["a"] = 1
        c["b"] = 2
        c["c"] = 3  # triggers eviction of "a"
        assert "a" not in c
        assert c["b"] == 2
        assert c["c"] == 3
        assert len(c) == 2

    def test_get_refreshes_lru_order(self):
        c = BoundedLayerCache(maxsize=2)
        c["a"] = 1
        c["b"] = 2
        _ = c["a"]  # touch "a" → "b" becomes oldest
        c["c"] = 3  # should evict "b", not "a"
        assert "a" in c
        assert "b" not in c
        assert "c" in c

    def test_get_method_also_refreshes_lru(self):
        c = BoundedLayerCache(maxsize=2)
        c["a"] = 1
        c["b"] = 2
        c.get("a")  # same effect as __getitem__
        c["c"] = 3
        assert "a" in c
        assert "b" not in c

    def test_maxsize_1_always_keeps_latest(self):
        c = BoundedLayerCache(maxsize=1)
        c["a"] = 1
        c["b"] = 2
        c["c"] = 3
        assert "a" not in c
        assert "b" not in c
        assert c["c"] == 3

    def test_repeated_overwrite_under_limit(self):
        c = BoundedLayerCache(maxsize=3)
        for i in range(100):
            c["key"] = i
        assert len(c) == 1
        assert c["key"] == 99
