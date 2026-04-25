"""Plugin discovery hub for gispulse / gispulse-enterprise split.

Discovers plugins from Python entry-point groups (see
``core.plugin_contracts`` for the contract spec) and exposes them to
the host application through a single lazy singleton :class:`PluginHub`.

The OSS engine starts with sensible defaults so that a stand-alone
``pip install gispulse`` works without any plugin: ``NoOpLicenceProvider``
(community tier) and no router/middleware. Installing
``gispulse-enterprise`` (or any other plugin package) injects routers,
middleware, billing and licence providers at process start.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from threading import Lock
from typing import Any, ClassVar

from core.logging import get_logger
from core.plugin_contracts import (
    AuthProvider,
    BillingProvider,
    Connector,
    LicenceProvider,
    LicenceState,
    MiddlewareFactory,
    RouterFactory,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Default implementations (OSS-only, feature-locked)
# ---------------------------------------------------------------------------


class NoOpLicenceProvider:
    """Default licence provider used when no plugin claims the slot.

    Reads tier and licence state from :mod:`persistence.tier`, which itself
    delegates to ``GISPULSE_TIER`` / ``GISPULSE_LICENSE_KEY`` env vars and
    Ed25519 signature verification (the public key is shipped in OSS;
    the private signing key lives in ``gispulse-enterprise``).
    """

    name: str = "noop"

    def current(self) -> LicenceState:
        from persistence.tier import get_current_tier

        tier = get_current_tier()
        features = _features_for_tier(tier)
        return LicenceState(
            org_id=None,
            tier=tier,
            valid=True,
            expires_at=None,
            features=features,
        )


def _features_for_tier(tier: str) -> frozenset[str]:
    """Resolve the feature set for ``tier`` from ``core/pricing_catalog.yml``.

    Walks the ``inherits`` chain so e.g. ``team`` collects its own
    features plus those of ``pro`` and ``community``. Returns an empty
    set if the catalog or PyYAML is missing — the gates themselves rely
    on :func:`persistence.tier.check_tier`, so feature introspection is
    purely informational.
    """
    try:
        from pathlib import Path

        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return frozenset()

    catalog_path = Path(__file__).parent / "pricing_catalog.yml"
    try:
        data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    except OSError:
        return frozenset()

    tiers_section: dict[str, Any] = data.get("tiers", {}) or {}
    collected: set[str] = set()
    seen: set[str] = set()
    cur: str | None = tier
    while cur and cur not in seen:
        seen.add(cur)
        block = tiers_section.get(cur) or {}
        for feature in (block.get("features") or []):
            collected.add(str(feature))
        cur = block.get("inherits")
    return frozenset(collected)


# ---------------------------------------------------------------------------
# Singleton hub
# ---------------------------------------------------------------------------


class PluginHub:
    """Lazy singleton aggregating discovered plugins.

    Use :meth:`PluginHub.get` to obtain the shared instance. Tests can
    call :meth:`PluginHub.reset` to force a fresh discovery (e.g. after
    monkey-patching ``importlib.metadata.entry_points``).
    """

    _instance: ClassVar["PluginHub | None"] = None
    _lock: ClassVar[Lock] = Lock()

    routers: dict[str, RouterFactory]
    middleware: list[MiddlewareFactory]
    auth_providers: dict[str, AuthProvider]
    billing_provider: BillingProvider | None
    licence_provider: LicenceProvider
    connectors: dict[str, Connector]

    def __init__(self) -> None:
        self.routers = {}
        self.middleware = []
        self.auth_providers = {}
        self.billing_provider = None
        self.licence_provider = NoOpLicenceProvider()
        self.connectors = {}

    # ------------------------------------------------------------------ API

    @classmethod
    def get(cls) -> "PluginHub":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls._discover()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------ discovery

    @classmethod
    def _discover(cls) -> "PluginHub":
        hub = cls()
        hub._load_routers()
        hub._load_middleware()
        hub._load_auth_providers()
        hub._load_billing_provider()
        hub._load_licence_provider()
        hub._load_connectors()
        log.info(
            "plugin_hub_initialized",
            routers=sorted(hub.routers),
            middleware=[m.name for m in hub.middleware],
            auth=sorted(hub.auth_providers),
            billing=(hub.billing_provider.name if hub.billing_provider else None),
            licence=hub.licence_provider.name,
            connectors=sorted(hub.connectors),
        )
        return hub

    def _load_routers(self) -> None:
        for ep in _eps("gispulse.routers"):
            obj = _safe_load(ep, "router")
            if obj is None:
                continue
            self.routers[ep.name] = obj

    def _load_middleware(self) -> None:
        for ep in _eps("gispulse.middleware"):
            obj = _safe_load(ep, "middleware")
            if obj is None:
                continue
            self.middleware.append(obj)

    def _load_auth_providers(self) -> None:
        for ep in _eps("gispulse.auth_provider"):
            obj = _safe_load(ep, "auth_provider")
            if obj is None:
                continue
            self.auth_providers[ep.name] = obj

    def _load_billing_provider(self) -> None:
        for ep in _eps("gispulse.billing_provider"):
            obj = _safe_load(ep, "billing_provider")
            if obj is None:
                continue
            # First-wins: a single billing backend per process.
            self.billing_provider = obj
            break

    def _load_licence_provider(self) -> None:
        for ep in _eps("gispulse.licence_provider"):
            obj = _safe_load(ep, "licence_provider")
            if obj is None:
                continue
            self.licence_provider = obj
            break

    def _load_connectors(self) -> None:
        for ep in _eps("gispulse.connectors"):
            obj = _safe_load(ep, "connector")
            if obj is None:
                continue
            self.connectors[ep.name] = obj


# ---------------------------------------------------------------------------
# Helpers (kept module-private)
# ---------------------------------------------------------------------------


def _eps(group: str):
    """Wrapper around ``importlib.metadata.entry_points`` that returns
    an empty list when the group is missing (Python <3.10 compat is not
    needed here; this just shields us from KeyError on EntryPoints)."""
    try:
        return list(entry_points(group=group))
    except Exception as exc:
        log.warning("plugin_entry_points_failed", group=group, error=str(exc))
        return []


def _safe_load(ep, kind: str):
    """Load an entry-point and instantiate it if the loaded object is a class.

    Plugins may declare an entry-point pointing to either an instance
    (``module:factory``) or a class (``module:Factory``); we accept both.
    """
    try:
        obj = ep.load()
    except Exception as exc:
        log.warning("plugin_load_failed", kind=kind, name=ep.name, error=str(exc))
        return None
    if isinstance(obj, type):
        try:
            obj = obj()
        except Exception as exc:
            log.warning("plugin_instantiate_failed", kind=kind, name=ep.name, error=str(exc))
            return None
    return obj
