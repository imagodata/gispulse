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

from gispulse.core import plugin_hub
from gispulse.core.plugin_hub import _version_satisfies, _check_protocol_version
from gispulse.core.plugin_contracts import LicenceState, PluginHostContext, PROTOCOL_VERSION
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
        assert hub.lifecycle == []
        assert hub.mcp_tools == []
        assert hub.mcp_resources == []
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

    def test_lifecycle_plugins_are_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeLifecycle:
            name = "fake-lifecycle"

            def on_startup(self, app): return None

            def on_shutdown(self, app): return None

        _patch_eps(
            monkeypatch,
            {"gispulse.lifecycle": [_FakeEntryPoint("fake-lifecycle", FakeLifecycle)]},
        )
        hub = plugin_hub.PluginHub.get()
        assert [p.name for p in hub.lifecycle] == ["fake-lifecycle"]

    def test_mcp_plugins_are_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeTools:
            name = "fake-tools"

            def register(self, mcp): return None

        class FakeResources:
            name = "fake-resources"

            def register(self, mcp): return None

        _patch_eps(
            monkeypatch,
            {
                "gispulse.mcp_tools": [_FakeEntryPoint("fake-tools", FakeTools)],
                "gispulse.mcp_resources": [_FakeEntryPoint("fake-resources", FakeResources)],
            },
        )
        hub = plugin_hub.PluginHub.get()
        assert [p.name for p in hub.mcp_tools] == ["fake-tools"]
        assert [p.name for p in hub.mcp_resources] == ["fake-resources"]


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
    assert PROTOCOL_VERSION == "1.1"


# ---------------------------------------------------------------------------
# Protocol version handshake
# ---------------------------------------------------------------------------


class TestVersionSatisfies:
    """Unit tests for the stdlib-only version specifier helper."""

    def test_ge_satisfied(self) -> None:
        assert _version_satisfies(">=1.0", "1.0") is True
        assert _version_satisfies(">=1.0", "1.1") is True
        assert _version_satisfies(">=1.0", "2.0") is True

    def test_ge_not_satisfied(self) -> None:
        assert _version_satisfies(">=1.1", "1.0") is False

    def test_lt_satisfied(self) -> None:
        assert _version_satisfies("<2.0", "1.0") is True
        assert _version_satisfies("<2.0", "1.9") is True

    def test_lt_not_satisfied(self) -> None:
        assert _version_satisfies("<2.0", "2.0") is False
        assert _version_satisfies("<2.0", "2.1") is False

    def test_compound_satisfied(self) -> None:
        assert _version_satisfies(">=1.0,<2.0", "1.0") is True
        assert _version_satisfies(">=1.0,<2.0", "1.5") is True

    def test_compound_not_satisfied(self) -> None:
        assert _version_satisfies(">=1.0,<2.0", "0.9") is False
        assert _version_satisfies(">=1.0,<2.0", "2.0") is False

    def test_eq_satisfied(self) -> None:
        assert _version_satisfies("==1.0", "1.0") is True

    def test_eq_not_satisfied(self) -> None:
        assert _version_satisfies("==1.0", "1.1") is False

    def test_ne_satisfied(self) -> None:
        assert _version_satisfies("!=1.0", "1.1") is True

    def test_ne_not_satisfied(self) -> None:
        assert _version_satisfies("!=1.0", "1.0") is False


class TestProtocolVersionHandshake:
    """Tests for requires_protocol discovery and version-check helpers."""

    def test_matching_plugin_still_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class MatchingFactory:
            name = "matching"
            requires_protocol = ">=1.0,<2.0"

            def create(self, app):
                return None

        _patch_eps(
            monkeypatch,
            {"gispulse.routers": [_FakeEntryPoint("matching", MatchingFactory)]},
        )
        hub = plugin_hub.PluginHub.get()
        assert "matching" in hub.routers

    def test_mismatched_plugin_still_loads_but_log_warning_is_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plugin with incompatible requires_protocol still loads; warning is issued."""
        class FutureFactory:
            name = "future"
            requires_protocol = ">=99.0"

            def create(self, app):
                return None

        _patch_eps(
            monkeypatch,
            {"gispulse.routers": [_FakeEntryPoint("future", FutureFactory)]},
        )
        warned: list[str] = []
        real_warning = plugin_hub.log.warning

        def capture_warning(event: str, **kw: object) -> None:
            warned.append(event)
            real_warning(event, **kw)

        monkeypatch.setattr(plugin_hub.log, "warning", capture_warning)
        hub = plugin_hub.PluginHub.get()

        # Plugin must survive — warning, not hard error.
        assert "future" in hub.routers
        assert any("mismatch" in w for w in warned), f"Expected mismatch warning, got: {warned}"

    def test_plugin_without_requires_protocol_loads_with_no_protocol_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class SilentFactory:
            name = "silent"

            def create(self, app):
                return None

        _patch_eps(
            monkeypatch,
            {"gispulse.routers": [_FakeEntryPoint("silent", SilentFactory)]},
        )
        warned: list[str] = []

        def capture_warning(event: str, **kw: object) -> None:
            warned.append(event)

        monkeypatch.setattr(plugin_hub.log, "warning", capture_warning)
        hub = plugin_hub.PluginHub.get()

        assert "silent" in hub.routers
        protocol_warns = [w for w in warned if "protocol" in w]
        assert protocol_warns == [], f"Expected no protocol warnings, got: {protocol_warns}"

    def test_check_protocol_version_does_not_raise(self) -> None:
        """_check_protocol_version never raises; it only logs warnings."""
        class Compatible:
            requires_protocol = ">=1.0,<2.0"

        class Incompatible:
            requires_protocol = ">=99.0"

        class NoSpec:
            pass

        _check_protocol_version(Compatible(), "compat", "router")
        _check_protocol_version(Incompatible(), "incompat", "router")
        _check_protocol_version(NoSpec(), "nospec", "router")
