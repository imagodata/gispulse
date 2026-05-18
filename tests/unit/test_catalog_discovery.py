"""Tests for catalog-provider discovery via the ExtensionHub (#193).

Issue #193 moved the single ``gispulse.catalog_providers`` entry-point
scan into :class:`~core.plugin_hub.ExtensionHub`; :func:`_discover_providers`
now *consumes* ``ExtensionHub.records`` instead of scanning a second time.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gispulse.catalog.registry import (
    PROVIDERS,
    _CATALOG_PROVIDER_GROUP,
    _discover_providers,
    register_provider,
)
from gispulse.core import plugin_hub
from gispulse.core.plugin_model import PluginKind, PluginRecord, PluginState


@pytest.fixture(autouse=True)
def _restore_providers():
    """Save and restore PROVIDERS between tests."""
    original = dict(PROVIDERS)
    yield
    PROVIDERS.clear()
    PROVIDERS.update(original)


class _FakeEP:
    """Stand-in for ``importlib.metadata.EntryPoint``."""

    def __init__(self, name: str, value: str, group: str) -> None:
        self.name = name
        self.value = value
        self.group = group


class _FakeHub:
    """Minimal stand-in for the ExtensionHub — only ``records`` is consumed."""

    def __init__(self, records: list[PluginRecord]) -> None:
        self.records = records


def _catalog_record(
    name: str,
    *,
    obj=None,
    state: PluginState = PluginState.ACTIVE,
    detail: str = "",
    group: str = _CATALOG_PROVIDER_GROUP,
) -> PluginRecord:
    rec = PluginRecord(
        name=name,
        kind=PluginKind.EXTENSION,
        entry_point=_FakeEP(name, f"{name}_pkg.catalog:register", group),
    )
    rec.state = state
    rec.detail = detail
    rec.obj = obj
    return rec


def _with_hub(records: list[PluginRecord]):
    """Patch ``ExtensionHub.get()`` to return a fake hub holding ``records``."""
    return patch.object(
        plugin_hub.ExtensionHub, "get", return_value=_FakeHub(records)
    )


class TestDiscoverProviders:
    def test_no_records_returns_empty(self):
        with _with_hub([]):
            assert _discover_providers() == []

    def test_loads_active_catalog_provider_record(self):
        fake_provider = MagicMock()
        fake_provider.domain.value = "test"
        fake_provider.name = "fake_prov"

        def register_fn():
            register_provider(fake_provider)

        rec = _catalog_record("test_plugin", obj=register_fn)
        with _with_hub([rec]):
            result = _discover_providers()

        assert len(result) == 1
        assert result[0]["name"] == "test_plugin"
        assert result[0]["status"] == "ok"
        assert "test:fake_prov" in PROVIDERS

    def test_failing_register_callable_does_not_crash(self):
        def boom():
            raise RuntimeError("provider blew up")

        rec = _catalog_record("broken_plugin", obj=boom)
        with _with_hub([rec]):
            result = _discover_providers()

        assert len(result) == 1
        assert result[0]["status"].startswith("error:")

    def test_locked_record_is_skipped_not_invoked(self):
        sentinel = MagicMock()
        rec = _catalog_record(
            "pro_plugin",
            obj=sentinel,
            state=PluginState.LOCKED,
            detail="requires the 'pro' tier",
        )
        with _with_hub([rec]):
            result = _discover_providers()

        assert result[0]["status"].startswith("skipped:")
        assert "pro" in result[0]["status"]
        sentinel.assert_not_called()

    def test_non_catalog_records_are_ignored(self):
        router = PluginRecord(
            name="admin_router",
            kind=PluginKind.EXTENSION,
            entry_point=_FakeEP("admin_router", "pkg:router", "gispulse.routers"),
        )
        router.state = PluginState.ACTIVE
        router.obj = MagicMock()
        with _with_hub([router]):
            result = _discover_providers()

        assert result == []
        router.obj.assert_not_called()

    def test_hub_failure_returns_empty(self):
        with patch.object(
            plugin_hub.ExtensionHub, "get", side_effect=RuntimeError("hub down")
        ):
            assert _discover_providers() == []

    def test_existing_providers_unaffected(self):
        provider_count_before = len(PROVIDERS)
        with _with_hub([]):
            _discover_providers()
        assert len(PROVIDERS) == provider_count_before
