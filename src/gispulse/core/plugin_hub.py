"""Plugin discovery hub for gispulse / gispulse-enterprise split.

Discovers plugins from Python entry-point groups (see
``core.plugin_contracts`` for the contract spec) and exposes them to
the host application through a single lazy singleton :class:`ExtensionHub`.

The OSS engine starts with sensible defaults so that a stand-alone
``pip install gispulse`` works without any plugin: ``NoOpLicenceProvider``
(community tier) and no router/middleware. Installing
``gispulse-enterprise`` (or any other plugin package) injects routers,
middleware, billing and licence providers at process start.
"""
from __future__ import annotations

import json
import os
import re
from importlib.metadata import entry_points
from pathlib import Path
from threading import Lock
from typing import Any, ClassVar

from gispulse.core.logging import get_logger
from gispulse.core.plugin_contracts import (
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
from gispulse.core.plugin_model import (
    ENTRYPOINT_GROUPS,
    DataPackManifest,
    Origin,
    PluginKind,
    PluginRecord,
    PluginState,
    Tier,
    Trust,
    tier_satisfies,
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
    # Discovery-only — catalog providers extend the GIS catalog subsystem.
    # The hub owns the single entry-point scan (issue #193); the records
    # are consumed by ``catalog.registry``. The v1.8 ExtensionHub refonte
    # promotes these to a first-class ``data-pack`` kind.
    "gispulse.catalog_providers",
)

# Distributions shipped by GISPulse itself — their plugins are trusted
# first-party code (issue #182).
_FIRST_PARTY_DISTRIBUTIONS: frozenset[str] = frozenset(
    {"gispulse", "gispulse-enterprise"}
)

# Repo root — ``src/gispulse/core/plugin_hub.py`` -> parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Curated marketplace registry — the tier/trust authority for external
# plugins. Versioned in the OSS repo, hence tamper-evident.
_REGISTRY_PATH = _REPO_ROOT / "marketplace" / "registry.json"

# Bundled first-party data packs — declarative manifests shipped in the
# OSS tree. The v1.9.0 worldwide aggregator adds source-catalog packs.
_BUNDLED_DATA_PACK_MANIFESTS: tuple[Path, ...] = (
    _REPO_ROOT / "templates" / "manifest.yml",
)

# Env var pointing at a directory of extra data-pack manifests
# (``*.yml`` / ``*.yaml`` / ``*.json``) — the user-dir discovery channel.
_DATA_PACKS_DIR_ENV = "GISPULSE_DATA_PACKS_DIR"

# Third-party PyPI data packs declare an entry-point here; the value must
# be a callable that returns an iterable of file paths to manifest YAML/JSON.
# Typical implementation in a sibling package::
#
#     def manifest_paths():
#         from importlib.resources import files
#         return [files("my_pack") / "manifests" / "zoning.yml"]
#
#     # pyproject.toml
#     [project.entry-points."gispulse.data_packs"]
#     my_pack = "my_pack._gispulse_entry:manifest_paths"
#
# Single-string returns are also accepted for the common one-manifest case.
_DATA_PACK_ENTRYPOINT_GROUP = "gispulse.data_packs"

# G1a (#271) — Ed25519 public key (base64 DER) used to verify the
# ``signature`` field of an EXTERNAL data-pack manifest. Mirrors the
# operational shape of ``GISPULSE_LICENCE_PUBLIC_KEY`` so the same
# rotation tooling can configure both.
_DATA_PACK_PUBLIC_KEY_ENV = "GISPULSE_DATA_PACK_PUBLIC_KEY"

# When true, EXTERNAL manifests without a ``signature`` are refused.
# Default false so a fresh install with no rotation tooling still loads
# the bundled packs and unsigned community packs. Recommended in CI for
# any deploy that ships gated content.
_DATA_PACK_REQUIRE_SIGNATURE_ENV = "GISPULSE_DATA_PACK_REQUIRE_SIGNATURE"


def _tier_from_str(value: object) -> Tier:
    """Map a tier string to :class:`Tier`, defaulting to community."""
    try:
        return Tier(str(value).lower())
    except ValueError:
        return Tier.COMMUNITY


def _trust_from_str(value: object) -> Trust:
    """Map a trust string to :class:`Trust`, defaulting to community."""
    try:
        return Trust(str(value).lower())
    except ValueError:
        return Trust.COMMUNITY


def _curated_registry() -> dict[str, dict[str, Any]]:
    """Load ``marketplace/registry.json`` as ``{package: {tier, trust}}``.

    Reads the explicit v3 ``tier`` / ``trust`` fields when present, else
    derives them from the v2 ``requires_pro`` / ``verified`` flags — so
    the gate is correct before and after issue #181. A missing or
    malformed file degrades to an empty mapping (no plugin gated).
    """
    try:
        raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in raw.get("plugins", []):
        package = str(entry.get("package", "")).lower()
        if not package:
            continue
        tier = entry.get("tier")
        trust = entry.get("trust")
        out[package] = {
            "tier": _tier_from_str(tier)
            if tier
            else (Tier.PRO if entry.get("requires_pro") else Tier.COMMUNITY),
            "trust": _trust_from_str(trust)
            if trust
            else (Trust.VERIFIED if entry.get("verified") else Trust.COMMUNITY),
        }
    return out


def _record_package(rec: PluginRecord) -> str:
    """Best-effort distribution name backing a record's entry-point."""
    dist = getattr(rec.entry_point, "dist", None)
    name = getattr(dist, "name", None)
    return str(name).lower() if name else ""


def _allow_unverified() -> bool:
    """Whether community-trust plugins may activate (default: yes)."""
    raw = os.environ.get("GISPULSE_PLUGINS_ALLOW_UNVERIFIED", "true")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


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
        from gispulse.persistence.tier import get_current_tier

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


class ExtensionHub:
    """Lazy singleton aggregating discovered plugins.

    Use :meth:`ExtensionHub.get` to obtain the shared instance. Tests can
    call :meth:`ExtensionHub.reset` to force a fresh discovery (e.g. after
    monkey-patching ``importlib.metadata.entry_points``).
    """

    _instance: ClassVar["ExtensionHub | None"] = None
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
        # Licence tier resolved once per discovery for the activation gate.
        self._licence_tier: Tier = Tier.COMMUNITY

    # ------------------------------------------------------------------ API

    @classmethod
    def get(cls) -> "ExtensionHub":
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
    def _discover(cls) -> "ExtensionHub":
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
        hub._discover_data_packs()
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
            data_packs=len(hub.records_by_kind(PluginKind.DATA_PACK)),
        )
        return hub

    # ---------------------------------------------------- unified inventory

    def _discover_records(self) -> None:
        """Build the unified plugin inventory across every entry-point group.

        Each entry-point becomes a :class:`~core.plugin_model.PluginRecord`
        and runs the ``discover → resolve → gate → activate`` cycle. The
        host-extension sub-groups collapse to
        :attr:`~core.plugin_model.PluginKind.EXTENSION`; capabilities /
        data_sources / data_sinks / protocols map to their own kind.

        Additive by design: the typed collections (``routers``,
        ``middleware``, …) populated by the ``_load_*`` methods stay the
        wiring surface consumed by the app. Issues #180 and the extension
        migration move consumers onto ``records``.
        """
        self._licence_tier = _tier_from_str(self.licence_provider.current().tier)
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

        First-party distributions (:data:`_FIRST_PARTY_DISTRIBUTIONS`) are
        trusted internal code. For every other plugin the curated
        ``marketplace/registry.json`` is the tier/trust authority — an
        external plugin cannot grant itself a tier. Plugins absent from
        the registry keep the community defaults.
        """
        package = _record_package(rec)
        if package in _FIRST_PARTY_DISTRIBUTIONS:
            rec.origin = Origin.INTERNAL
            rec.trust = Trust.FIRST_PARTY
        meta = _curated_registry().get(package)
        if meta is not None:
            rec.tier_required = meta["tier"]
            if rec.trust is not Trust.FIRST_PARTY:
                rec.trust = meta["trust"]

    def _gate(self, rec: PluginRecord) -> bool:
        """Decide whether a record may be activated (issue #182).

        Tier gate: the licence tier must satisfy ``rec.tier_required``.
        Trust gate: a community-trust plugin is refused when
        ``GISPULSE_PLUGINS_ALLOW_UNVERIFIED`` is disabled. A refused
        record is marked LOCKED by the caller — its code is never loaded.
        """
        if not tier_satisfies(self._licence_tier, rec.tier_required):
            rec.detail = (
                f"requires the '{rec.tier_required.value}' tier "
                f"(licence: '{self._licence_tier.value}')"
            )
            return False
        if rec.trust is Trust.COMMUNITY and not _allow_unverified():
            rec.detail = (
                "unverified community plugin blocked by "
                "GISPULSE_PLUGINS_ALLOW_UNVERIFIED"
            )
            return False
        return True

    def _activate(self, rec: PluginRecord) -> None:
        """Load the entry-point to confirm importability; set ACTIVE/FAILED.

        ``rec.obj`` holds the loaded callable/class. The actual wiring
        (instantiation, routing, capability registration) still runs
        through the existing ``_load_*`` paths and the capability
        registry. After a successful load the plugin's declared
        ``requires_protocol`` is checked (warn-only, issue #182).
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
        _check_protocol_version(rec.obj, rec.name, rec.kind.value)

    def records_by_kind(self, kind: PluginKind) -> list[PluginRecord]:
        """Return the inventory records of a given :class:`PluginKind`."""
        return [r for r in self.records if r.kind is kind]

    # ------------------------------------------------------- data-pack regime

    def _discover_data_packs(self) -> None:
        """Discover declarative data packs — the data regime of the hub.

        Three discovery channels are walked: bundled first-party manifests
        (``Origin.INTERNAL``), third-party PyPI packs exposing the
        ``gispulse.data_packs`` entry-point, and manifests under
        ``GISPULSE_DATA_PACKS_DIR``.

        Each manifest becomes a :class:`~core.plugin_model.PluginRecord`
        of kind :attr:`~core.plugin_model.PluginKind.DATA_PACK` in the
        single unified inventory. No code is imported, so a data pack
        never reaches ``FAILED`` — trust is data-driven; a malformed
        manifest is logged and skipped.

        G1a (#271) signature gate: an EXTERNAL manifest carrying
        ``signature`` is verified against
        ``GISPULSE_DATA_PACK_PUBLIC_KEY`` before being registered. A bad
        signature is logged and dropped so a tampered pack never reaches
        an ACTIVE record. Bundled INTERNAL manifests are exempt — the OSS
        tree is the source of truth. Unsigned EXTERNAL manifests are
        admitted by default (rollout-friendly); set
        ``GISPULSE_DATA_PACK_REQUIRE_SIGNATURE=true`` to refuse them.
        """
        for path, origin in _data_pack_manifest_paths():
            raw = _read_manifest(path)
            if raw is None:
                continue
            try:
                manifest = DataPackManifest.from_dict(raw)
            except ValueError as exc:
                log.warning(
                    "data_pack_manifest_invalid", path=str(path), error=str(exc)
                )
                continue
            if origin is Origin.EXTERNAL and not _accept_data_pack_signature(
                manifest, raw, path
            ):
                continue
            rec = PluginRecord(
                name=manifest.name,
                kind=PluginKind.DATA_PACK,
                origin=origin,
                trust=Trust.VERIFIED,
                tier_required=manifest.tier,
                obj=manifest,
            )
            if tier_satisfies(self._licence_tier, manifest.tier):
                rec.state = PluginState.ACTIVE
            else:
                rec.state = PluginState.LOCKED
                rec.detail = (
                    f"requires the '{manifest.tier.value}' tier "
                    f"(licence: '{self._licence_tier.value}')"
                )
            self.records.append(rec)

    def data_pack_manifests(
        self, content: str | None = None
    ) -> list[DataPackManifest]:
        """Return the manifests of every ACTIVE data pack.

        Args:
            content: Optional content-type filter (e.g. ``"template-pack"``).
        """
        out: list[DataPackManifest] = []
        for rec in self.records:
            if rec.kind is not PluginKind.DATA_PACK:
                continue
            if rec.state is not PluginState.ACTIVE:
                continue
            manifest = rec.obj
            if not isinstance(manifest, DataPackManifest):
                continue
            if content is not None and manifest.content != content:
                continue
            out.append(manifest)
        return out

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


# Transitional back-compat alias (v1.8.0 ExtensionHub rename, Chantier C).
# ``PluginHub`` was renamed to :class:`ExtensionHub`; the old name keeps
# out-of-tree consumers (gispulse-enterprise, third-party tooling) working
# until they migrate. Removed in v1.9.0.
PluginHub = ExtensionHub


# ---------------------------------------------------------------------------
# Helpers (kept module-private)
# ---------------------------------------------------------------------------


def _data_pack_manifest_paths() -> list[tuple[Path, Origin]]:
    """Locate every data-pack manifest file, paired with its origin.

    Three discovery channels, walked in this order so first-party always
    wins on a duplicate name and PyPI packs are reproducible across
    environments:

    1. Bundled first-party manifests (``Origin.INTERNAL``).
    2. Third-party PyPI distributions exposing the
       ``gispulse.data_packs`` entry-point — story T5 (#269). Each
       entry-point is a callable returning either a path or an iterable
       of paths; a malformed entry is logged and skipped, never raised.
    3. Manifests under ``GISPULSE_DATA_PACKS_DIR`` (``Origin.EXTERNAL``).

    Missing files / directories are silently skipped — discovery must
    never hard-fail, that would brick any ``pip install`` of the engine
    on a partially-installed system.
    """
    paths: list[tuple[Path, Origin]] = [
        (p, Origin.INTERNAL) for p in _BUNDLED_DATA_PACK_MANIFESTS if p.is_file()
    ]

    for ep_path in _entrypoint_data_pack_paths():
        paths.append((ep_path, Origin.EXTERNAL))

    user_dir = os.environ.get(_DATA_PACKS_DIR_ENV, "").strip()
    if user_dir:
        root = Path(user_dir).expanduser()
        if root.is_dir():
            for pattern in ("*.yml", "*.yaml", "*.json"):
                for p in sorted(root.glob(pattern)):
                    paths.append((p, Origin.EXTERNAL))
    return paths


def _entrypoint_data_pack_paths() -> list[Path]:
    """Resolve every manifest path exposed via ``gispulse.data_packs``.

    Each entry-point loads to a callable; the callable returns either a
    single path-like or an iterable of path-likes. Any failure (load,
    call, non-existent path, surprising return type) is logged and
    contributes zero paths — *one* bad pack must not lock out the rest.
    """
    out: list[Path] = []
    for ep in _eps(_DATA_PACK_ENTRYPOINT_GROUP):
        try:
            factory = ep.load()
        except Exception as exc:  # noqa: BLE001 — isolate a bad pack
            log.warning(
                "data_pack_entrypoint_load_failed", name=ep.name, error=str(exc)
            )
            continue
        if not callable(factory):
            log.warning(
                "data_pack_entrypoint_not_callable",
                name=ep.name,
                type=type(factory).__name__,
            )
            continue
        try:
            result = factory()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "data_pack_entrypoint_call_failed",
                name=ep.name,
                error=str(exc),
            )
            continue
        # Accept either a single path-like or an iterable of path-likes.
        # ``str``/``os.PathLike`` are themselves iterable so handle them
        # explicitly to avoid iterating their characters.
        if isinstance(result, (str, os.PathLike)):
            items: list[Any] = [result]
        else:
            try:
                items = list(result)
            except TypeError:
                log.warning(
                    "data_pack_entrypoint_bad_return",
                    name=ep.name,
                    type=type(result).__name__,
                )
                continue
        for item in items:
            try:
                p = Path(os.fspath(item))
            except TypeError:
                log.warning(
                    "data_pack_entrypoint_bad_item",
                    name=ep.name,
                    type=type(item).__name__,
                )
                continue
            if not p.is_file():
                log.warning(
                    "data_pack_entrypoint_missing_file",
                    name=ep.name,
                    path=str(p),
                )
                continue
            out.append(p)
    return out


def _read_manifest(path: Path) -> dict[str, Any] | None:
    """Read a data-pack manifest file as a mapping.

    JSON is always supported; YAML requires PyYAML. A parse error or a
    non-mapping top level is logged and yields ``None``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("data_pack_manifest_unreadable", path=str(path), error=str(exc))
        return None
    try:
        if path.suffix.lower() in {".yml", ".yaml"}:
            import yaml  # type: ignore[import-untyped]

            raw = yaml.safe_load(text)
        else:
            raw = json.loads(text)
    except ImportError:
        log.warning("data_pack_yaml_unavailable", path=str(path))
        return None
    except Exception as exc:  # noqa: BLE001 — isolate a bad manifest file
        log.warning("data_pack_manifest_parse_failed", path=str(path), error=str(exc))
        return None
    if not isinstance(raw, dict):
        log.warning("data_pack_manifest_not_a_mapping", path=str(path))
        return None
    return raw


_DATA_PACK_PUBLIC_KEY_CACHE: dict[str, Any] = {}


def _data_pack_public_key() -> Any | None:
    """Return the configured Ed25519 public key, or ``None`` if absent.

    Cached on the env-var value so a key rotation requires a restart but
    repeated discovery calls during the same process are cheap.
    """
    raw = os.environ.get(_DATA_PACK_PUBLIC_KEY_ENV, "").strip()
    if not raw:
        return None
    if raw in _DATA_PACK_PUBLIC_KEY_CACHE:
        return _DATA_PACK_PUBLIC_KEY_CACHE[raw]
    try:
        from gispulse.core.data_pack_signature import load_public_key_b64

        key = load_public_key_b64(raw)
    except Exception as exc:  # noqa: BLE001 — gracefully degrade
        log.warning(
            "data_pack_public_key_unavailable",
            env=_DATA_PACK_PUBLIC_KEY_ENV,
            error=str(exc),
        )
        return None
    _DATA_PACK_PUBLIC_KEY_CACHE[raw] = key
    return key


def _require_data_pack_signature() -> bool:
    """True when ``GISPULSE_DATA_PACK_REQUIRE_SIGNATURE`` is truthy."""
    raw = os.environ.get(_DATA_PACK_REQUIRE_SIGNATURE_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _accept_data_pack_signature(
    manifest: "DataPackManifest", raw: dict[str, Any], path: Path
) -> bool:
    """Gate an EXTERNAL data-pack on its Ed25519 signature.

    Returns:
        ``True`` if the manifest should be registered; ``False`` if it
        must be dropped (bad signature, or signature required but missing
        / unverifiable). Always logs the decision.

    Policy (G1a, #271):

    * unsigned manifest + signature not required → admit;
    * unsigned manifest + signature required → drop;
    * signed manifest but no public key configured → drop (we cannot
      validate the claim, so refusing is the safe default);
    * signed manifest + key configured + valid signature → admit;
    * signed manifest + key configured + invalid signature → drop.
    """
    public_key = _data_pack_public_key()
    has_sig = bool(manifest.signature)

    if not has_sig:
        if _require_data_pack_signature():
            log.warning(
                "data_pack_signature_required_missing",
                name=manifest.name,
                path=str(path),
            )
            return False
        return True

    if public_key is None:
        log.warning(
            "data_pack_signature_no_public_key",
            name=manifest.name,
            path=str(path),
        )
        return False

    from gispulse.core.data_pack_signature import (
        DataPackSignatureError,
        verify_manifest_dict,
    )

    try:
        verify_manifest_dict(raw, manifest.signature or "", public_key)
    except DataPackSignatureError as exc:
        log.warning(
            "data_pack_signature_invalid",
            name=manifest.name,
            path=str(path),
            error=str(exc),
        )
        return False

    log.debug(
        "data_pack_signature_verified",
        name=manifest.name,
        path=str(path),
    )
    return True


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
