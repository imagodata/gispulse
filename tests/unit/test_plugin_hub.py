"""Tests for ``core.plugin_hub`` — entry-point discovery and defaults.

Plugins are discovered through ``importlib.metadata.entry_points``;
we monkey-patch that callable to inject fake plugins per scenario.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, FastAPI

from core import plugin_hub
from core.plugin_contracts import LicenceState, PluginHostContext, PROTOCOL_VERSION
from gispulse.adapters.http.app import _create_plugin_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeEntryPoint:
    """Minimal stand-in for :class:`importlib.metadata.EntryPoint`.

    The real EntryPoint has more attributes (group, value, ...), but
    plugin_hub only touches ``name`` and ``load()``.
    """

    name: str
    value: Any

    def load(self):  # noqa: D401 — match EntryPoint API
        return self.value


def _patch_eps(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, list[_FakeEntryPoint]]) -> None:
    """Replace ``importlib.metadata.entry_points`` for the duration of a test."""

    def fake(group: str | None = None, **_kwargs):
        if group is None:
            return []
        return mapping.get(group, [])

    monkeypatch.setattr(plugin_hub, "entry_points", fake)
    plugin_hub.PluginHub.reset()


# ---------------------------------------------------------------------------
# Discovery scenarios
# ---------------------------------------------------------------------------


class TestPluginHubDiscovery:
    def test_no_plugins_yields_defaults_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_eps(monkeypatch, {})
        hub = plugin_hub.PluginHub.get()
        assert hub.routers == {}
        assert hub.middleware == []
        assert hub.auth_providers == {}
        assert hub.billing_provider is None
        assert hub.connectors == {}
        # Default licence provider is the OSS NoOp.
        assert hub.licence_provider.name == "noop"

    def test_single_router_plugin_is_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeRouterFactory:
            name = "fake-billing"

            def create(self, app):
                return MagicMock(spec_set=[])

        _patch_eps(
            monkeypatch,
            {"gispulse.routers": [_FakeEntryPoint("fake-billing", FakeRouterFactory)]},
        )
        hub = plugin_hub.PluginHub.get()
        assert "fake-billing" in hub.routers
        assert hub.routers["fake-billing"].name == "fake-billing"

    def test_two_routers_same_group_both_loaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FactoryA:
            name = "a"

            def create(self, app): return None

        class FactoryB:
            name = "b"

            def create(self, app): return None

        _patch_eps(
            monkeypatch,
            {
                "gispulse.routers": [
                    _FakeEntryPoint("a", FactoryA),
                    _FakeEntryPoint("b", FactoryB),
                ]
            },
        )
        hub = plugin_hub.PluginHub.get()
        assert set(hub.routers) == {"a", "b"}

    def test_billing_first_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class StripeFake:
            name = "stripe"

            @property
            def available(self): return True

            async def create_checkout_session(self, org_id, tier): return ""

            async def handle_webhook(self, payload, signature): return None

        class PaddleFake(StripeFake):
            name = "paddle"

        _patch_eps(
            monkeypatch,
            {
                "gispulse.billing_provider": [
                    _FakeEntryPoint("stripe", StripeFake),
                    _FakeEntryPoint("paddle", PaddleFake),
                ]
            },
        )
        hub = plugin_hub.PluginHub.get()
        assert hub.billing_provider is not None
        assert hub.billing_provider.name == "stripe"

    def test_load_failure_does_not_crash_hub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class BoomEntry:
            name = "boom"

            def load(self):
                raise RuntimeError("import error simulated")

        _patch_eps(
            monkeypatch,
            {"gispulse.routers": [BoomEntry()]},  # type: ignore[list-item]
        )
        hub = plugin_hub.PluginHub.get()
        assert hub.routers == {}
        # Other defaults still in place.
        assert hub.licence_provider.name == "noop"

    def test_factory_create_returning_none_is_skipped_by_caller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The hub itself does NOT call create(); that's the host app's
        # job. We only verify the factory survives the hub round-trip.
        class MissingDepsFactory:
            name = "needs-stripe"

            def create(self, app): return None

        _patch_eps(
            monkeypatch,
            {"gispulse.routers": [_FakeEntryPoint("needs-stripe", MissingDepsFactory)]},
        )
        hub = plugin_hub.PluginHub.get()
        assert hub.routers["needs-stripe"].create(app=None) is None


# ---------------------------------------------------------------------------
# Host router creation contract
# ---------------------------------------------------------------------------


class TestPluginRouterCreation:
    def test_context_aware_factory_receives_plugin_host_context(self) -> None:
        app = FastAPI()
        hub = plugin_hub.PluginHub()
        context = PluginHostContext(app=app, settings=object(), logger=object(), plugin_hub=hub)
        seen: list[PluginHostContext] = []

        class ContextAwareFactory:
            name = "context-aware"

            def create(self, ctx):
                seen.append(ctx)
                return APIRouter()

        router = _create_plugin_router(ContextAwareFactory(), app, context)

        assert isinstance(router, APIRouter)
        assert seen == [context]

    def test_context_factory_can_be_detected_by_annotation_only(self) -> None:
        app = FastAPI()
        hub = plugin_hub.PluginHub()
        context = PluginHostContext(app=app, settings=object(), logger=object(), plugin_hub=hub)
        seen: list[PluginHostContext] = []

        class AnnotatedFactory:
            name = "annotated-context"

            def create(self, host: PluginHostContext):
                seen.append(host)
                return APIRouter()

        router = _create_plugin_router(AnnotatedFactory(), app, context)

        assert isinstance(router, APIRouter)
        assert seen == [context]

    def test_legacy_factory_receives_app_when_signature_is_legacy(self) -> None:
        app = FastAPI()
        hub = plugin_hub.PluginHub()
        context = PluginHostContext(app=app, settings=object(), logger=object(), plugin_hub=hub)
        seen: list[FastAPI] = []

        class LegacyFactory:
            name = "legacy"

            def create(self, app_arg):
                if not isinstance(app_arg, FastAPI):
                    raise TypeError("legacy factory expects FastAPI")
                seen.append(app_arg)
                return APIRouter()

        router = _create_plugin_router(LegacyFactory(), app, context)

        assert isinstance(router, APIRouter)
        assert seen == [app]

    def test_context_factory_type_error_is_not_treated_as_legacy_signature(self) -> None:
        app = FastAPI()
        hub = plugin_hub.PluginHub()
        context = PluginHostContext(app=app, settings=object(), logger=object(), plugin_hub=hub)

        class BrokenContextAwareFactory:
            name = "broken-context-aware"

            def create(self, ctx):
                raise TypeError("plugin bug")

        with pytest.raises(TypeError, match="plugin bug"):
            _create_plugin_router(BrokenContextAwareFactory(), app, context)

    def test_plugin_host_context_is_exported_from_plugin_author_api(self) -> None:
        from gispulse.plugins.api import PluginHostContext as PublicPluginHostContext

        assert PublicPluginHostContext is PluginHostContext


# ---------------------------------------------------------------------------
# Default licence provider (OSS NoOp)
# ---------------------------------------------------------------------------


class TestNoOpLicenceProvider:
    def test_returns_current_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GISPULSE_TIER", "community")
        provider = plugin_hub.NoOpLicenceProvider()
        state = provider.current()
        assert isinstance(state, LicenceState)
        assert state.tier == "community"
        assert state.valid is True

    def test_features_inherit_chain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GISPULSE_TIER", "team")
        monkeypatch.setenv("GISPULSE_LICENCE_SKIP_VERIFY", "true")
        state = plugin_hub.NoOpLicenceProvider().current()
        # Team inherits pro+community features (cf. core/pricing_catalog.yml).
        assert "core_engine" in state.features  # community
        assert "postgis" in state.features      # pro
        assert "rbac" in state.features         # team
        assert "sso_saml_oidc" not in state.features  # enterprise


# ---------------------------------------------------------------------------
# Singleton semantics
# ---------------------------------------------------------------------------


class TestPluginHubSingleton:
    def test_get_returns_same_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_eps(monkeypatch, {})
        a = plugin_hub.PluginHub.get()
        b = plugin_hub.PluginHub.get()
        assert a is b

    def test_reset_clears_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_eps(monkeypatch, {})
        a = plugin_hub.PluginHub.get()
        plugin_hub.PluginHub.reset()
        b = plugin_hub.PluginHub.get()
        assert a is not b


# ---------------------------------------------------------------------------
# Protocol version surface
# ---------------------------------------------------------------------------


def test_protocol_version_exposed() -> None:
    assert PROTOCOL_VERSION == "1.0"
