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

# Single source of truth lives in ``core.plugin_model`` (epic #175).
# Re-exported (redundant alias) so existing importers of
# ``core.plugin_contracts`` — notably ``core.plugin_hub`` — keep working.
from gispulse.core.plugin_model import PROTOCOL_VERSION as PROTOCOL_VERSION

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
