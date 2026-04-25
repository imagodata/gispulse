"""Plugin contracts for the gispulse / gispulse-enterprise split.

This module defines the runtime Protocols that external plugin packages
implement to extend the OSS engine via Python entry-points. The core
discovery / instantiation logic lives in :mod:`core.plugin_hub`.

Pattern reference: ``gispulse.capabilities`` (existing) — see
``capabilities/registry.py``. We extend the same mechanism to five new
groups documented in ``docs/PLUGIN_CONTRACT.md``.

Entry-point groups
------------------
- ``gispulse.routers``         — :class:`RouterFactory`
- ``gispulse.middleware``      — :class:`MiddlewareFactory`
- ``gispulse.auth_provider``   — :class:`AuthProvider`
- ``gispulse.billing_provider``— :class:`BillingProvider`
- ``gispulse.licence_provider``— :class:`LicenceProvider`
- ``gispulse.connectors``      — :class:`Connector`
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

# Bumped MAJOR on breaking change, MINOR on additive change. Plugins
# declare ``requires_protocol = ">=1.0,<2.0"`` and the hub warns on
# mismatch.
PROTOCOL_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


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
class MiddlewareFactory(Protocol):
    """Plugin that registers an ASGI middleware on the host app."""

    name: str

    def install(self, app: "FastAPI") -> None:
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
