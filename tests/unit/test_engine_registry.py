"""Tests for pluggable engine backend registration."""

from __future__ import annotations

import pytest

from persistence.engine_factory import (
    _BACKENDS,
    create_spatial_engine,
    register_engine_backend,
)


@pytest.fixture(autouse=True)
def _restore_backends():
    """Save and restore the module-level _BACKENDS dict between tests."""
    original = dict(_BACKENDS)
    yield
    _BACKENDS.clear()
    _BACKENDS.update(original)


# ------------------------------------------------------------------
# Built-in registration
# ------------------------------------------------------------------


class TestBuiltins:
    def test_builtins_registered(self):
        assert "duckdb" in _BACKENDS
        assert "postgis" in _BACKENDS
        assert "hybrid" in _BACKENDS

    def test_create_duckdb_returns_session(self):
        engine = create_spatial_engine("duckdb")
        assert engine is not None
        assert engine.backend_name == "duckdb"


# ------------------------------------------------------------------
# Custom backend registration
# ------------------------------------------------------------------


class TestRegisterEngineBackend:
    def test_register_custom_backend(self):
        class FakeEngine:
            backend_name = "fake"

        def fake_factory(*, dsn=None, duckdb_path=":memory:", **_kw):
            return FakeEngine()

        register_engine_backend("fake", fake_factory)
        assert "fake" in _BACKENDS

        engine = create_spatial_engine("fake")
        assert engine.backend_name == "fake"

    def test_register_duplicate_raises(self):
        def factory(**_kw):
            pass

        register_engine_backend("custom1", factory)
        with pytest.raises(ValueError, match="already registered"):
            register_engine_backend("custom1", factory)

    def test_register_override(self):
        class E1:
            backend_name = "v1"

        class E2:
            backend_name = "v2"

        register_engine_backend("myeng", lambda **_kw: E1())
        register_engine_backend("myeng", lambda **_kw: E2(), override=True)

        engine = create_spatial_engine("myeng")
        assert engine.backend_name == "v2"


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


class TestErrors:
    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown engine backend"):
            create_spatial_engine("nonexistent")

    def test_unknown_backend_lists_available(self):
        with pytest.raises(ValueError, match="Available:"):
            create_spatial_engine("nope")
