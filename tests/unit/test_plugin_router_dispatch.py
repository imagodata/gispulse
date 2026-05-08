"""Focused tests for _create_plugin_router dispatch logic.

Covers both the legacy ``create(self, app)`` shape and the context-aware
``create(self, ctx: PluginHostContext)`` shape, as well as the full set of
accepted parameter names and the annotation-based detection path.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, FastAPI

from core.plugin_contracts import PluginHostContext
from gispulse.adapters.http.app import _create_plugin_router, _plugin_factory_wants_context


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app() -> FastAPI:
    return FastAPI()


@pytest.fixture()
def ctx(app: FastAPI) -> PluginHostContext:
    from core import plugin_hub as ph

    return PluginHostContext(
        app=app,
        settings=MagicMock(),
        logger=MagicMock(),
        plugin_hub=ph.PluginHub(),
    )


# ---------------------------------------------------------------------------
# Legacy shape: create(self, app) — parameter name not in accepted set
# ---------------------------------------------------------------------------


class TestLegacyDispatch:
    def test_legacy_receives_app_not_context(self, app: FastAPI, ctx: PluginHostContext) -> None:
        received: list[object] = []

        class LegacyFactory:
            name = "legacy"

            def create(self, app):
                received.append(app)
                return APIRouter()

        router = _create_plugin_router(LegacyFactory(), app, ctx)

        assert isinstance(router, APIRouter)
        assert received == [app]
        assert received[0] is not ctx

    def test_unrelated_param_name_routes_to_legacy(
        self, app: FastAPI, ctx: PluginHostContext
    ) -> None:
        """Parameter named 'application' must not be treated as context-aware."""
        received: list[object] = []

        class LegacyApplicationFactory:
            name = "legacy-application"

            def create(self, application):
                received.append(application)
                return APIRouter()

        router = _create_plugin_router(LegacyApplicationFactory(), app, ctx)

        assert isinstance(router, APIRouter)
        assert received == [app]

    def test_param_named_fastapp_routes_to_legacy(
        self, app: FastAPI, ctx: PluginHostContext
    ) -> None:
        received: list[object] = []

        class OtherLegacyFactory:
            name = "other-legacy"

            def create(self, fastapp):
                received.append(fastapp)
                return APIRouter()

        router = _create_plugin_router(OtherLegacyFactory(), app, ctx)

        assert received == [app]


# ---------------------------------------------------------------------------
# Context-aware shape — parameter names
# ---------------------------------------------------------------------------


class TestContextAwareDispatchByName:
    """All accepted parameter names must route to context-aware path."""

    def test_param_ctx_receives_context(self, app: FastAPI, ctx: PluginHostContext) -> None:
        received: list[object] = []

        class CtxFactory:
            name = "ctx-factory"

            def create(self, ctx):
                received.append(ctx)
                return APIRouter()

        router = _create_plugin_router(CtxFactory(), app, ctx)
        assert isinstance(router, APIRouter)
        assert received == [ctx]

    def test_param_context_receives_context(self, app: FastAPI, ctx: PluginHostContext) -> None:
        received: list[object] = []

        class ContextFactory:
            name = "context-factory"

            def create(self, context):
                received.append(context)
                return APIRouter()

        router = _create_plugin_router(ContextFactory(), app, ctx)
        assert isinstance(router, APIRouter)
        assert received == [ctx]

    def test_param_host_context_receives_context(
        self, app: FastAPI, ctx: PluginHostContext
    ) -> None:
        received: list[object] = []

        class HostContextFactory:
            name = "host-context-factory"

            def create(self, host_context):
                received.append(host_context)
                return APIRouter()

        router = _create_plugin_router(HostContextFactory(), app, ctx)
        assert isinstance(router, APIRouter)
        assert received == [ctx]

    def test_param_plugin_context_receives_context(
        self, app: FastAPI, ctx: PluginHostContext
    ) -> None:
        received: list[object] = []

        class PluginContextFactory:
            name = "plugin-context-factory"

            def create(self, plugin_context):
                received.append(plugin_context)
                return APIRouter()

        router = _create_plugin_router(PluginContextFactory(), app, ctx)
        assert isinstance(router, APIRouter)
        assert received == [ctx]


# ---------------------------------------------------------------------------
# Context-aware shape — annotation-based detection
# ---------------------------------------------------------------------------


class TestContextAwareDispatchByAnnotation:
    def test_annotated_with_plugin_host_context_receives_context(
        self, app: FastAPI, ctx: PluginHostContext
    ) -> None:
        received: list[object] = []

        class AnnotatedFactory:
            name = "annotated"

            def create(self, host: PluginHostContext):
                received.append(host)
                return APIRouter()

        router = _create_plugin_router(AnnotatedFactory(), app, ctx)

        assert isinstance(router, APIRouter)
        assert received == [ctx]

    def test_string_annotation_plugin_host_context_receives_context(
        self, app: FastAPI, ctx: PluginHostContext
    ) -> None:
        """String annotation ``'PluginHostContext'`` (forward ref) is detected."""
        received: list[object] = []

        class StringAnnotatedFactory:
            name = "string-annotated"

            def create(self, host: "PluginHostContext"):
                received.append(host)
                return APIRouter()

        router = _create_plugin_router(StringAnnotatedFactory(), app, ctx)

        assert isinstance(router, APIRouter)
        assert received == [ctx]


# ---------------------------------------------------------------------------
# Return-value passthrough
# ---------------------------------------------------------------------------


class TestReturnValues:
    def test_none_from_legacy_factory_is_forwarded(
        self, app: FastAPI, ctx: PluginHostContext
    ) -> None:
        class NoneReturningFactory:
            name = "none-legacy"

            def create(self, app):
                return None

        result = _create_plugin_router(NoneReturningFactory(), app, ctx)
        assert result is None

    def test_none_from_context_factory_is_forwarded(
        self, app: FastAPI, ctx: PluginHostContext
    ) -> None:
        class NoneContextFactory:
            name = "none-context"

            def create(self, ctx):
                return None

        result = _create_plugin_router(NoneContextFactory(), app, ctx)
        assert result is None


# ---------------------------------------------------------------------------
# _plugin_factory_wants_context unit tests (lower-level)
# ---------------------------------------------------------------------------


def _make_first_param(func) -> inspect.Parameter:
    """Return the first parameter of *func* (excluding 'self' for methods)."""
    params = list(inspect.signature(func).parameters.values())
    return params[0]


class TestPluginFactoryWantsContext:
    """Direct tests of _plugin_factory_wants_context with concrete functions."""

    def test_ctx_param_name(self) -> None:
        def create(ctx): ...

        assert _plugin_factory_wants_context(create, _make_first_param(create)) is True

    def test_context_param_name(self) -> None:
        def create(context): ...

        assert _plugin_factory_wants_context(create, _make_first_param(create)) is True

    def test_host_context_param_name(self) -> None:
        def create(host_context): ...

        assert _plugin_factory_wants_context(create, _make_first_param(create)) is True

    def test_plugin_context_param_name(self) -> None:
        def create(plugin_context): ...

        assert _plugin_factory_wants_context(create, _make_first_param(create)) is True

    def test_unrelated_name_returns_false(self) -> None:
        def create(application): ...

        assert _plugin_factory_wants_context(create, _make_first_param(create)) is False

    def test_annotation_is_plugin_host_context_class(self) -> None:
        def create(host: PluginHostContext): ...

        assert _plugin_factory_wants_context(create, _make_first_param(create)) is True

    def test_annotation_is_unrelated_class_returns_false(self) -> None:
        def create(host: FastAPI): ...

        assert _plugin_factory_wants_context(create, _make_first_param(create)) is False

    def test_string_annotation_plugin_host_context_returns_true(self) -> None:
        def create(host: "PluginHostContext"): ...

        assert _plugin_factory_wants_context(create, _make_first_param(create)) is True

    def test_app_param_name_returns_false(self) -> None:
        def create(app): ...

        assert _plugin_factory_wants_context(create, _make_first_param(create)) is False
