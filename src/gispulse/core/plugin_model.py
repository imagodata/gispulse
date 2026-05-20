"""Typed vocabulary for the unified GISPulse plugin model.

This module is the dependency-free foundation of the plugin platform
(epic #175). It defines *what* a plugin is — its kind, origin, trust
level, lifecycle state — and the data shapes exchanged across the ETL
graph ``source → capability → sink``.

Deliberately importless beyond the stdlib: no geopandas, no fastapi, no
network client. Every other plugin module (``core.plugin_hub``,
``core.plugin_contracts``, ``core.sources``) builds on this one, so it
must stay cheap to import and free of circular dependencies.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

# Protocol version of the host plugin contract. Bumped MAJOR on a
# breaking change, MINOR on an additive one. Plugins declare
# ``requires_protocol = ">=1.0,<2.0"`` and the hub warns on mismatch.
# Single source of truth — ``core.plugin_contracts`` re-imports it.
PROTOCOL_VERSION = "1.1"


# ---------------------------------------------------------------------------
# Enums — the classification axes of a plugin
# ---------------------------------------------------------------------------


class PluginKind(str, Enum):
    """The roles a plugin can play in the GISPulse runtime.

    The first five are *code plugins* — Python entry-points loaded into
    the process. ``DATA_PACK`` is the *data regime* added by the v1.8.0
    ExtensionHub refonte (Chantier C): a declarative manifest that ships
    data (templates, catalog sources, basemaps, projections) and never
    any code. The pack's content type is carried by
    :attr:`DataPackManifest.content`.
    """

    SOURCE = "source"          # Extract — yields data into the ETL graph
    CAPABILITY = "capability"  # Transform — operates on a dataset
    SINK = "sink"              # Load — writes data to a destination
    PROTOCOL = "protocol"      # Transport adapter (fetch / write)
    EXTENSION = "extension"    # Extends the host FastAPI app
    DATA_PACK = "data-pack"    # Declarative data bundle — no code


class Origin(str, Enum):
    """Where a plugin comes from."""

    INTERNAL = "internal"  # first-party, shipped by GISPulse / gispulse-enterprise
    EXTERNAL = "external"  # third-party package


class Trust(str, Enum):
    """How much the host trusts a plugin's code."""

    FIRST_PARTY = "first_party"  # maintained by GISPulse
    VERIFIED = "verified"        # listed in the curated marketplace registry
    COMMUNITY = "community"      # unknown third-party package


class PluginState(str, Enum):
    """Lifecycle state of a plugin within the discovery cycle.

    ``discovered → resolve → gate → activate`` — a record ends in exactly
    one of ``ACTIVE``, ``LOCKED`` or ``FAILED``.
    """

    DISCOVERED = "discovered"  # entry-point seen, code not loaded
    LOCKED = "locked"          # tier/trust gate refused activation
    FAILED = "failed"          # load or instantiation raised
    ACTIVE = "active"          # loaded, instantiated and routed


class Tier(str, Enum):
    """Commercial tier required to activate a plugin.

    Ordered: ``community < pro < team < enterprise`` — see
    :func:`tier_satisfies`. Mirrors ``core/pricing_catalog.yml``.
    """

    COMMUNITY = "community"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class SourceDomain(str, Enum):
    """Thematic domain of a data source. Open enum — a new theme is a new
    value and requires no contract change (epic #175 design axiom)."""

    BASE = "base"                    # BD TOPO, ROUTE 500, OSM base
    FONCIER = "foncier"              # cadastre, Parcellaire Express, RPG
    REGLEMENTAIRE = "reglementaire"  # PLU/zoning, permits, SUP, PPR
    RESEAU = "reseau"                # FTTH, telecom, utility networks
    ELEVATION = "elevation"          # MNT / MNS / DEM
    IMAGERIE = "imagerie"            # orthophotos, satellite
    ENVIRONNEMENT = "environnement"  # protected areas, land cover, forests
    OBSERVATION = "observation"      # OSM POI, field surveys, IoT
    STATISTIQUE = "statistique"      # DVF, INSEE, demographics


class Payload(str, Enum):
    """Shape of the data a source yields — discriminates :class:`SourceResult`."""

    VECTOR = "vector"
    RASTER = "raster"
    POINTCLOUD = "pointcloud"
    TILES = "tiles"
    TABLE = "table"  # attribute-only rows, spatially joined via a key


class AccessProtocol(str, Enum):
    """Transport protocol by which an entry is fetched or written.

    Resolved per :class:`AccessSpec` to a registered protocol adapter,
    so a source package stays declarative.
    """

    # OGC cartographic services
    WFS = "wfs"
    WMS = "wms"
    WMTS = "wmts"
    TMS = "tms"
    XYZ = "xyz"
    OGC_FEATURES = "ogc-features"
    OGC_TILES = "ogc-tiles"
    # catalogues / APIs
    STAC = "stac"
    REST_API = "rest-api"
    OVERPASS = "overpass"
    # files
    DOWNLOAD = "download"          # remote zip/shp/gpkg/geojson/csv
    COG = "cog"                    # Cloud-Optimized GeoTIFF (raster reference)
    PMTILES = "pmtiles"            # single-file vector/raster tiles
    # databases / lakehouse
    REMOTE_TABLE = "remote-table"  # remote parquet/flatgeobuf read by DuckDB
    DB = "db"                      # PostGIS / DuckDB / SpatiaLite


class FetchMode(str, Enum):
    """Whether :meth:`DataSource.fetch` materializes or references data."""

    MATERIALIZE = "materialize"  # download into a dataset
    REFERENCE = "reference"      # return a COG/WMTS handle, consumed live


class WriteMode(str, Enum):
    """Write semantics for :meth:`DataSink.write`."""

    UPSERT = "upsert"
    APPEND = "append"
    REPLACE = "replace"


# Entry-point group per single-group kind. ``EXTENSION`` spans nine
# sub-groups (``gispulse.routers``, ``.middleware``, …) handled directly
# by ``core.plugin_contracts`` and is intentionally absent here.
ENTRYPOINT_GROUPS: dict[PluginKind, str] = {
    PluginKind.SOURCE: "gispulse.data_sources",
    PluginKind.CAPABILITY: "gispulse.capabilities",
    PluginKind.SINK: "gispulse.data_sinks",
    PluginKind.PROTOCOL: "gispulse.protocols",
}

# Tier ordering for the activation gate.
_TIER_RANK: dict[Tier, int] = {
    Tier.COMMUNITY: 0,
    Tier.PRO: 1,
    Tier.TEAM: 2,
    Tier.ENTERPRISE: 3,
}


def tier_satisfies(current: Tier, required: Tier) -> bool:
    """Return True if ``current`` licence tier may activate a ``required`` plugin.

    ``enterprise`` satisfies everything; ``community`` only satisfies
    ``community``. Used by the load-time gate (issue #182).
    """
    return _TIER_RANK[current] >= _TIER_RANK[required]


# ---------------------------------------------------------------------------
# Data shapes — exchanged across the ETL graph
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginManifest:
    """Declarative metadata a plugin ships in ``[tool.gispulse.plugin]``.

    For *external* plugins the authoritative ``tier`` / ``trust`` come
    from the curated ``marketplace/registry.json`` — an external plugin
    cannot grant itself a tier here. *Internal* (first-party) plugins
    may assert ``tier`` directly, their code being trusted.
    """

    name: str
    kind: PluginKind
    protocol: str = ">=1.0,<2.0"      # requires_protocol specifier
    display_name: str = ""
    origin: Origin = Origin.EXTERNAL
    tier: Tier | None = None          # asserted only by internal plugins
    domain: SourceDomain | None = None  # source kind only
    payload: Payload | None = None      # source kind only
    jurisdiction: str = "*"             # ISO-3166 alpha-2, or "*" for global
    metadata: dict[str, Any] = field(default_factory=dict)


# Recognised data-pack content types. Open set — a new content type is a
# new value and needs no contract change, mirroring the SourceDomain axiom.
# ``regulatory-zoning`` (T3, #270) ships urban-planning zonage manifests
# consumed by the data-pack regulatory pack (gispulse-data-regulatory).
DATA_PACK_CONTENTS: frozenset[str] = frozenset(
    {
        "template-pack",
        "source-catalog",
        "basemap-pack",
        "projection-pack",
        "regulatory-zoning",
    }
)


@dataclass(frozen=True)
class DataPackManifest:
    """Declarative manifest of a *data pack* — the data regime of the
    ExtensionHub (v1.8.0 Chantier C).

    A data pack ships *data*, never code: pipeline templates, catalog
    sources, basemaps or projections. It is described by a YAML/JSON
    manifest and discovered without importing anything — so its trust is
    trivially ``verified`` and tier gating is fully data-driven.

    ``content`` discriminates the payload (one of
    :data:`DATA_PACK_CONTENTS`); ``entries`` carries the content-specific
    declarative records.
    """

    name: str
    content: str
    version: str = "0.0.0"
    display_name: str = ""
    description: str = ""
    tier: Tier = Tier.COMMUNITY
    entries: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DataPackManifest":
        """Parse a manifest mapping. Raises :class:`ValueError` on a
        missing/empty ``name`` or an unknown ``content`` type."""
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError("data pack manifest requires a non-empty 'name'")
        content = str(raw.get("content", "")).strip()
        if content not in DATA_PACK_CONTENTS:
            raise ValueError(
                f"data pack {name!r}: unknown content {content!r}; "
                f"expected one of {sorted(DATA_PACK_CONTENTS)}"
            )
        tier_raw = raw.get("tier", "community")
        try:
            tier = Tier(str(tier_raw).lower())
        except ValueError:
            tier = Tier.COMMUNITY
        entries = list(raw.get("entries", []) or [])
        return cls(
            name=name,
            content=content,
            version=str(raw.get("version", "0.0.0")),
            display_name=str(raw.get("display_name", "") or name),
            description=str(raw.get("description", "")),
            tier=tier,
            entries=entries,
            metadata=dict(raw.get("metadata", {}) or {}),
        )


@dataclass(frozen=True)
class AccessSpec:
    """How to reach one catalog entry — dispatched to a protocol adapter."""

    protocol: AccessProtocol
    endpoint: str
    params: dict[str, Any] = field(default_factory=dict)
    format: str | None = None
    auth: str | None = None  # token/key name, resolved via LicenceProvider for pro


def resolve_access_endpoint(access: AccessSpec) -> AccessSpec:
    """Interpolate ``{key}`` placeholders in ``access.endpoint`` from params.

    A :class:`~gispulse.plugins.api.DeclarativeSource` can declare a
    single entry per logical dataset and let the consumer choose a
    sub-zone at fetch time::

        AccessSpec(
            protocol=AccessProtocol.DOWNLOAD,
            endpoint=".../departements/{departement}/file-{departement}.gz",
            params={"departement": "75"},
        )

    Idempotent for the supported inputs — a resolved endpoint has no
    ``{`` and short-circuits on re-entry — and protocol-agnostic.
    ``ProtocolRegistry.dispatch_fetch`` runs it before the SSRF guard so
    every adapter — :class:`LazyFetcher` subclass or structural
    ``Fetcher`` — receives a concrete URL. :class:`LazyFetcher` itself
    re-runs it (no-op when the dispatch layer already resolved) so
    direct fetcher invocations stay safe.

    Precondition: ``params`` values are pre-encoded URL fragments — a
    value that itself contains an unescaped ``{`` would re-templatise
    the endpoint and trip the malformed/missing-param checks on the
    next pass. Use ``urllib.parse.quote`` upstream if the value is
    user-supplied.

    Only bare ``{key}`` placeholders are accepted. Empty ``{}``, numeric
    ``{0}``, attribute ``{a.b}``, index ``{a[0]}``, conversion
    ``{key!r}`` and format-spec ``{key:>3}`` placeholders all alter the
    rendered URL in ways callers rarely expect, and are rejected with a
    contextual :class:`ValueError`. Escaped ``{{literal}}`` braces are
    left untouched.

    Raises:
        ValueError: a placeholder is malformed, carries a conversion or
            format spec, or has no matching ``params`` key.
    """
    endpoint = access.endpoint
    # Hot path: untemplated endpoints pay nothing — no parse, no alloc.
    if "{" not in endpoint:
        return access
    try:
        parsed = list(string.Formatter().parse(endpoint))
    except ValueError as exc:
        raise ValueError(
            f"malformed endpoint template {endpoint!r}: {exc}"
        ) from exc
    for _, name, format_spec, conversion in parsed:
        if name is None:
            continue
        if name == "":
            raise ValueError(
                f"malformed endpoint template {endpoint!r}: "
                f"empty placeholder; expected named '{{key}}'"
            )
        if name.isdigit() or "." in name or "[" in name:
            raise ValueError(
                f"malformed endpoint template {endpoint!r}: "
                f"placeholder {{{name}}} must be a bare named key, "
                f"not a positional/attribute/index reference"
            )
        if conversion:
            raise ValueError(
                f"malformed endpoint template {endpoint!r}: "
                f"placeholder {{{name}!{conversion}}} carries a "
                f"conversion flag; only bare '{{key}}' is accepted"
            )
        if format_spec:
            raise ValueError(
                f"malformed endpoint template {endpoint!r}: "
                f"placeholder {{{name}:{format_spec}}} carries a "
                f"format spec; only bare '{{key}}' is accepted"
            )
    fields = [name for _, name, _, _ in parsed if name]
    if not fields:
        # Only ``{{literal}}`` escapes — leave the access untouched on
        # the hot path; ``format_map`` would copy and unescape, we
        # avoid that allocation here.
        return access
    missing = [f for f in fields if f not in access.params]
    if missing:
        raise ValueError(
            f"endpoint template {endpoint!r} requires params "
            f"{missing!r}, got keys {sorted(access.params)!r}"
        )
    return replace(access, endpoint=endpoint.format_map(access.params))


@dataclass
class SourceResult:
    """Typed return of :meth:`DataSource.fetch`.

    ``data`` is left untyped (``Any``) on purpose — this module imports
    no geopandas/rasterio. Depending on ``payload`` it is a GeoDataFrame,
    a raster handle, a file path or ``None`` when ``mode`` is
    :attr:`FetchMode.REFERENCE` (then ``reference`` carries the live URL).
    """

    payload: Payload
    mode: FetchMode = FetchMode.MATERIALIZE
    data: Any = None
    reference: str | None = None  # COG/WMTS URL when mode is REFERENCE
    crs: str | None = None
    extent: tuple[float, float, float, float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WriteSpec:
    """Where and how a sink writes its output."""

    protocol: AccessProtocol
    destination: str
    layer: str | None = None
    mode: WriteMode = WriteMode.UPSERT
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class WriteReport:
    """Outcome of a :meth:`DataSink.write` call."""

    destination: str
    rows_written: int = 0
    rows_failed: int = 0
    created: bool = False  # True when the destination layer/table was created
    detail: str = ""


@dataclass
class RuleClause:
    """One applicable regulatory clause returned by ``RegulatorySource.ruleset``.

    Jurisdiction-agnostic on purpose: a French PLU zone and a Dutch
    ``Bestemmingsplan`` zone both fill ``constraints`` with whatever is
    machine-readable and point ``source_doc`` at the rest.
    """

    zone_code: str
    jurisdiction: str = "*"
    constraints: dict[str, Any] = field(default_factory=dict)
    source_doc: str | None = None  # e.g. "reglement_UB.pdf#p14"
    label: str = ""


@dataclass
class PluginRecord:
    """Runtime view of one discovered plugin — the hub's source of truth.

    A record is created at ``discover`` time with only ``name``/``kind``/
    ``entry_point`` known; ``resolve`` fills origin/trust/tier from the
    curated registry; ``gate`` may flip it to :attr:`PluginState.LOCKED`;
    ``activate`` loads the code and sets ``obj`` + :attr:`PluginState.ACTIVE`.
    """

    name: str
    kind: PluginKind
    origin: Origin = Origin.EXTERNAL
    trust: Trust = Trust.COMMUNITY
    tier_required: Tier = Tier.COMMUNITY
    state: PluginState = PluginState.DISCOVERED
    detail: str = ""              # lock reason or load error
    entry_point: Any = None       # importlib.metadata.EntryPoint (untyped)
    manifest: PluginManifest | None = None
    obj: Any = None               # loaded instance, None until ACTIVE

    @property
    def available(self) -> bool:
        """True when the plugin is loaded and usable."""
        return self.state is PluginState.ACTIVE

    def as_dict(self) -> dict[str, Any]:
        """Serialize the record for the marketplace / API surface.

        Omits ``entry_point`` and ``obj`` — neither is JSON-safe.
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "origin": self.origin.value,
            "trust": self.trust.value,
            "tier_required": self.tier_required.value,
            "state": self.state.value,
            "detail": self.detail,
        }


__all__ = [
    "PROTOCOL_VERSION",
    "PluginKind",
    "Origin",
    "Trust",
    "PluginState",
    "Tier",
    "SourceDomain",
    "Payload",
    "AccessProtocol",
    "FetchMode",
    "WriteMode",
    "ENTRYPOINT_GROUPS",
    "tier_satisfies",
    "PluginManifest",
    "DATA_PACK_CONTENTS",
    "DataPackManifest",
    "AccessSpec",
    "SourceResult",
    "WriteSpec",
    "WriteReport",
    "RuleClause",
    "PluginRecord",
]
