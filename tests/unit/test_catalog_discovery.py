"""Tests for catalog provider entry-point discovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from catalog.registry import PROVIDERS, _discover_providers, register_provider


@pytest.fixture(autouse=True)
def _restore_providers():
    """Save and restore PROVIDERS between tests."""
    original = dict(PROVIDERS)
    yield
    PROVIDERS.clear()
    PROVIDERS.update(original)


class TestDiscoverProviders:
    def test_no_entrypoints_returns_empty(self):
        with patch("importlib.metadata.entry_points", return_value=[]):
            result = _discover_providers()
        assert result == []

    def test_loads_plugin_provider(self):
        fake_provider = MagicMock()
        fake_provider.domain.value = "test"
        fake_provider.name = "fake_prov"

        def register_fn():
            register_provider(fake_provider)

        ep = MagicMock()
        ep.name = "test_plugin"
        ep.value = "my_pkg.catalog:register"
        ep.load.return_value = register_fn

        with patch("importlib.metadata.entry_points", return_value=[ep]):
            result = _discover_providers()

        assert len(result) == 1
        assert result[0]["name"] == "test_plugin"
        assert result[0]["status"] == "ok"
        assert "test:fake_prov" in PROVIDERS

    def test_failing_plugin_does_not_crash(self):
        ep = MagicMock()
        ep.name = "broken_plugin"
        ep.value = "bad_pkg:register"
        ep.load.side_effect = ImportError("no such module")

        with patch("importlib.metadata.entry_points", return_value=[ep]):
            result = _discover_providers()

        assert len(result) == 1
        assert result[0]["status"].startswith("error:")

    def test_existing_providers_unaffected(self):
        provider_count_before = len(PROVIDERS)

        with patch("importlib.metadata.entry_points", return_value=[]):
            _discover_providers()

        assert len(PROVIDERS) == provider_count_before
