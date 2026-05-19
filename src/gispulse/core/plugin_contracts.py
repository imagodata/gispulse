"""Plugin contracts for the gispulse / gispulse-enterprise split.

This module defines the runtime Protocols that external plugin packages
implement to extend the OSS engine via Python entry-points. The core
discovery / instantiation logic lives in :mod:`core.plugin_hub`.

Pattern reference: ``gispulse.capabilities`` (existing) — see
``capabilities/registry.py``. The same mechanism extends the host app and
MCP facade without hard-coding optional packages.

Entry-point groups
------------------
- ``gispulse.routers``         — :class:`RouterFactory`
- ``gispulse.middleware``      — :class:`MiddlewareFactory`
- ``gispulse.auth_provider``   — :class:`AuthProvider`
- ``gispulse.billing_provider``— :class:`BillingProvider`
- ``gispulse.licence_provider``— :class:`LicenceProvider`
- ``gispulse.connectors``      — :class:`Connector`
- ``gispulse.lifecycle``       — :class:`LifecycleHook`
- ``gispulse.mcp_tools``       — :class:`McpToolFactory`
- ``gispulse.mcp_resources``   — :class:`McpResourceFactory`
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

# --- compat 1.x -> 2.0 (retirer en 2.1) -----------------------------------
# B-4 (audit breaking-changes 2.0.0, 2026-05-19).
#
# The v1.8.0 consolidation introduced ``core.plugin_model`` as the single,
# import-free source of truth for the plugin vocabulary. ``PROTOCOL_VERSION``
# was the *only* symbol that physically moved out of this module — every
# Protocol and ``LicenceState`` below were defined here in 1.6.2 and still
# are. It is re-exported here so 1.x importers of ``core.plugin_contracts``
# (notably ``core.plugin_hub``) keep resolving the name unchanged.
#
# NOTE — scope intentionally narrow. The enums/dataclasses ``Tier``,
# ``Origin``, ``PluginKind``, ``PluginManifest``, ``DataPackManifest`` etc.
# were *never* importable from this module in 1.6.2 (verified against the
# published wheel: 1.6.2 ``plugin_contracts.py`` exposed only the 8 names
# in ``__all__`` below; ``Tier`` lived in ``persistence/tier.py`` as plain
# strings). They are net-new v1.8.0 symbols and live solely in
# ``core.plugin_model``. They are deliberately NOT re-exported here — doing
# so would invent a compat contract that never existed and permanently
# widen this module's public surface. New code must import them from
# ``gispulse.core.plugin_model`` directly.
from gispulse.core.plugin_model import PROTOCOL_VERSION as PROTOCOL_VERSION

# --------------------------------------------------------------------------

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginHostContext:
    """Stable host context passed to context-aware plugin factories.

    This first #68 slice is intentionally small. It exposes only the
    runtime objects plugins already need at mount time without requiring
    them to inspect ``app.state`` directly.
    """

    app: "FastAPI"
    settings: Any
    logger: Any
    plugin_hub: Any


@runtime_checkable
class RouterFactory(Protocol):
    """Plugin that mounts an :class:`fastapi.APIRouter` on the host app.

    The factory is responsible for any connectivity / config check; if a
    required dependency is missing it must return ``None`` so the host
    can degrade gracefully (no router mounted, no crash).
    """

    name: str

    def create(self, app: "FastAPI") -> "APIRouter | None":
        ...


@runtime_checkable
class ContextAwareRouterFactory(Protocol):
    """Additive router factory contract receiving :class:`PluginHostContext`."""

    name: str

    def create(self, context: PluginHostContext) -> "APIRouter | None":
        ...


@runtime_checkable
class MiddlewareFactory(Protocol):
    """Plugin that registers an ASGI middleware on the host app."""

    name: str

    def install(self, app: "FastAPI") -> None:
        ...


@runtime_checkable
class LifecycleHook(Protocol):
    """Plugin with explicit FastAPI startup and shutdown hooks."""

    name: str

    def on_startup(self, app: "FastAPI") -> Any:
        ...

    def on_shutdown(self, app: "FastAPI") -> Any:
        ...


# ---------------------------------------------------------------------------
# Auth / billing / licence
# ---------------------------------------------------------------------------


@runtime_checkable
class AuthProvider(Protocol):
    """Authentication backend.

    The OSS core ships a default API-key implementation. Enterprise
    injects OIDC / SAML via this Protocol.
    """

    name: str

    async def authenticate(self, request: Any) -> Any | None:
        ...

    async def required_scopes(self) -> set[str]:
        ...


@runtime_checkable
class BillingProvider(Protocol):
    """Billing backend (Stripe, Paddle, ...). ``None`` means community mode."""

    name: str

    @property
    def available(self) -> bool:
        ...

    async def create_checkout_session(self, org_id: str, tier: str) -> str:
        ...

    async def handle_webhook(self, payload: bytes, signature: str) -> None:
        ...


@dataclass(frozen=True)
class LicenceState:
    """Snapshot of the licence state for the current org / instance."""

    org_id: str | None
    tier: str
    valid: bool
    expires_at: datetime | None = None
    features: frozenset[str] = field(default_factory=frozenset)


@runtime_checkable
class LicenceProvider(Protocol):
    """Source of truth for the active licence state."""

    name: str

    def current(self) -> LicenceState:
        ...


# ---------------------------------------------------------------------------
# Connectors (proprietary FTTH, PLU, ...)
# ---------------------------------------------------------------------------


@runtime_checkable
class Connector(Protocol):
    """Proprietary domain connector (FTTH, PLU CNIG, ...)."""

    name: str
    schema_version: str

    def supports(self, source_type: str) -> bool:
        ...


# ---------------------------------------------------------------------------
# MCP extension points
# ---------------------------------------------------------------------------


@runtime_checkable
class McpToolFactory(Protocol):
    """Plugin that registers tools on a FastMCP server instance."""

    name: str

    def register(self, mcp: Any) -> None:
        ...


@runtime_checkable
class McpResourceFactory(Protocol):
    """Plugin that registers resources on a FastMCP server instance."""

    name: str

    def register(self, mcp: Any) -> None:
        ...


# The eight names below are the public surface 1.6.2 exposed from this
# module (it shipped no ``__all__``). They MUST stay importable from
# ``gispulse.core.plugin_contracts`` for the whole 2.x line — see the
# regression test ``test_plugin_contracts_compat``. ``PROTOCOL_VERSION`` is
# re-exported from ``plugin_model`` (compat block above); the seven
# Protocols / ``LicenceState`` are defined in this module. The additive
# v1.8.0 names (``PluginHostContext``, ``ContextAwareRouterFactory``,
# ``LifecycleHook``, ``McpToolFactory``, ``McpResourceFactory``) are also
# listed — they were absent in 1.6.2 but are part of the 2.0 surface.
__all__ = [
    # --- 1.6.2 public surface (B-4 compat guarantee) ---
    "PROTOCOL_VERSION",
    "RouterFactory",
    "MiddlewareFactory",
    "AuthProvider",
    "BillingProvider",
    "LicenceState",
    "LicenceProvider",
    "Connector",
    # --- additive since v1.8.0 ---
    "PluginHostContext",
    "ContextAwareRouterFactory",
    "LifecycleHook",
    "McpToolFactory",
    "McpResourceFactory",
]
