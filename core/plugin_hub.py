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

import re
from importlib.metadata import entry_points
from threading import Lock
from typing import Any, ClassVar

from core.logging import get_logger
from core.plugin_contracts import (
    PROTOCOL_VERSION,
    AuthProvider,
    BillingProvider,
    Connector,
    LifecycleHook,
    LicenceProvider,
    LicenceState,
    McpResourceFactory,
    McpToolFactory,
    MiddlewareFactory,
    RouterFactory,
)
from core.plugin_model import (
    ENTRYPOINT_GROUPS,
    PluginKind,
    PluginRecord,
    PluginState,
)

log = get_logger(__name__)

# The nine host-extension entry-point groups. Every entry-point in these
# maps to ``PluginKind.EXTENSION`` in the unified inventory; the four
# ETL/single-group kinds come from :data:`core.plugin_model.ENTRYPOINT_GROUPS`.
_EXTENSION_GROUPS: tuple[str, ...] = (
    "gispulse.routers",
    "gispulse.middleware",
    "gispulse.auth_provider",
    "gispulse.billing_provider",
    "gispulse.licence_provider",
    "gispulse.connectors",
    "gispulse.lifecycle",
    "gispulse.mcp_tools",
    "gispulse.mcp_resources",
)


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
    lifecycle: list[LifecycleHook]
    mcp_tools: list[McpToolFactory]
    mcp_resources: list[McpResourceFactory]
    # Unified inventory across all 11 entry-point groups (issue #177).
    # Additive: the typed collections above remain the wiring surface.
    records: list[PluginRecord]

    def __init__(self) -> None:
        self.routers = {}
        self.middleware = []
        self.auth_providers = {}
        self.billing_provider = None
        self.licence_provider = NoOpLicenceProvider()
        self.connectors = {}
        self.lifecycle = []
        self.mcp_tools = []
        self.mcp_resources = []
        self.records = []

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
        hub._load_lifecycle()
        hub._load_mcp_tools()
        hub._load_mcp_resources()
        hub._discover_records()
        log.info(
            "plugin_hub_initialized",
            routers=sorted(hub.routers),
            middleware=[m.name for m in hub.middleware],
            auth=sorted(hub.auth_providers),
            billing=(hub.billing_provider.name if hub.billing_provider else None),
            licence=hub.licence_provider.name,
            connectors=sorted(hub.connectors),
            lifecycle=[p.name for p in hub.lifecycle],
            mcp_tools=[p.name for p in hub.mcp_tools],
            mcp_resources=[p.name for p in hub.mcp_resources],
            plugin_records=len(hub.records),
        )
        return hub

    # ---------------------------------------------------- unified inventory

    def _discover_records(self) -> None:
        """Build the unified plugin inventory across all 11 entry-point groups.

        Each entry-point becomes a :class:`~core.plugin_model.PluginRecord`
        and runs the ``discover → resolve → gate → activate`` cycle. The
        nine host-extension sub-groups collapse to
        :attr:`~core.plugin_model.PluginKind.EXTENSION`; capabilities /
        data_sources / data_sinks / protocols map to their own kind.

        Additive by design: the typed collections (``routers``,
        ``middleware``, …) populated by the ``_load_*`` methods stay the
        wiring surface consumed by the app. Issues #180 and the extension
        migration move consumers onto ``records``.
        """
        groups: list[tuple[str, PluginKind]] = [
            (group, PluginKind.EXTENSION) for group in _EXTENSION_GROUPS
        ]
        groups += [(group, kind) for kind, group in ENTRYPOINT_GROUPS.items()]
        for group, kind in groups:
            for ep in _eps(group):
                rec = PluginRecord(name=ep.name, kind=kind, entry_point=ep)
                self._resolve(rec)
                if self._gate(rec):
                    self._activate(rec)
                else:
                    rec.state = PluginState.LOCKED
                self.records.append(rec)

    def _resolve(self, rec: PluginRecord) -> None:
        """Resolve ``origin`` / ``trust`` / ``tier_required`` for a record.

        Placeholder (issue #177): records keep the
        :class:`~core.plugin_model.PluginRecord` defaults — external,
        community trust, community tier. Issue #182 fills this from the
        curated marketplace registry and the first-party distribution
        allowlist.
        """

    def _gate(self, rec: PluginRecord) -> bool:
        """Decide whether a record may be activated.

        Placeholder (issue #177): always allows activation. Issue #182
        implements the tier gate (``tier_satisfies`` against the licence
        provider) and the trust gate (``GISPULSE_PLUGINS_ALLOW_UNVERIFIED``).
        """
        return True

    def _activate(self, rec: PluginRecord) -> None:
        """Load the entry-point to confirm importability; set ACTIVE/FAILED.

        ``rec.obj`` holds the loaded callable/class. The actual wiring
        (instantiation, routing, capability registration) still runs
        through the existing ``_load_*`` paths and the capability
        registry — issue #177 only establishes the inventory and its
        lifecycle state.
        """
        try:
            rec.obj = rec.entry_point.load()
        except Exception as exc:  # noqa: BLE001 — isolate a bad plugin
            rec.state = PluginState.FAILED
            rec.detail = str(exc)
            log.warning(
                "plugin_record_load_failed",
                name=rec.name,
                kind=rec.kind.value,
                error=str(exc),
            )
            return
        rec.state = PluginState.ACTIVE

    def records_by_kind(self, kind: PluginKind) -> list[PluginRecord]:
        """Return the inventory records of a given :class:`PluginKind`."""
        return [r for r in self.records if r.kind is kind]

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

    def _load_lifecycle(self) -> None:
        for ep in _eps("gispulse.lifecycle"):
            obj = _safe_load(ep, "lifecycle")
            if obj is None:
                continue
            self.lifecycle.append(obj)

    def _load_mcp_tools(self) -> None:
        for ep in _eps("gispulse.mcp_tools"):
            obj = _safe_load(ep, "mcp_tool")
            if obj is None:
                continue
            self.mcp_tools.append(obj)

    def _load_mcp_resources(self) -> None:
        for ep in _eps("gispulse.mcp_resources"):
            obj = _safe_load(ep, "mcp_resource")
            if obj is None:
                continue
            self.mcp_resources.append(obj)


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
    After loading, checks ``requires_protocol`` against ``PROTOCOL_VERSION``
    and warns if the plugin's declared specifier is not satisfied.
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
    _check_protocol_version(obj, ep.name, kind)
    return obj


# ---------------------------------------------------------------------------
# Protocol version check (stdlib-only, no `packaging` dep)
# ---------------------------------------------------------------------------

_SPEC_RE = re.compile(
    r"(?P<op>>=|<=|!=|==|>|<|~=)\s*(?P<ver>\d+(?:\.\d+)*)"
)


def _version_tuple(ver: str) -> tuple[int, ...]:
    return tuple(int(x) for x in ver.split("."))


def _version_satisfies(specifier_str: str, version: str) -> bool:
    """Return True if *version* satisfies all clauses in *specifier_str*.

    Supports: ``>=``, ``<=``, ``!=``, ``==``, ``>``, ``<``.
    Multiple clauses may be comma-separated (e.g. ``">=1.0,<2.0"``).
    """
    v = _version_tuple(version)
    for clause in specifier_str.split(","):
        clause = clause.strip()
        m = _SPEC_RE.fullmatch(clause)
        if m is None:
            # Unrecognised clause — skip rather than hard-fail.
            continue
        op, req_ver = m.group("op"), m.group("ver")
        r = _version_tuple(req_ver)
        if op == ">=":
            if not (v >= r):
                return False
        elif op == "<=":
            if not (v <= r):
                return False
        elif op == "!=":
            if not (v != r):
                return False
        elif op == "==":
            if not (v == r):
                return False
        elif op == ">":
            if not (v > r):
                return False
        elif op == "<":
            if not (v < r):
                return False
        elif op == "~=":
            # Compatible release: >=X.Y and ==X.*
            if len(r) < 2:
                if not (v >= r):
                    return False
            else:
                prefix = r[:-1]
                if not (v >= r and v[: len(prefix)] == prefix):
                    return False
    return True


def _check_protocol_version(obj: object, name: str, kind: str) -> None:
    """Warn when the plugin's ``requires_protocol`` is not satisfied."""
    spec = getattr(obj, "requires_protocol", None)
    if spec is None:
        return
    if not isinstance(spec, str):
        log.warning(
            "plugin_protocol_version_invalid",
            kind=kind,
            name=name,
            requires_protocol=repr(spec),
            reason="requires_protocol must be a string",
        )
        return
    if not _version_satisfies(spec, PROTOCOL_VERSION):
        log.warning(
            "plugin_protocol_version_mismatch",
            kind=kind,
            name=name,
            requires_protocol=spec,
            host_protocol_version=PROTOCOL_VERSION,
        )
