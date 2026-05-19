"""Worldwide geo-data aggregator — first-party ``DataSource`` (A8, #234).

EPIC #226 (v1.9.0). A single curated :class:`DataSource` over
``core/data/worldwide_catalog.yml`` — not thirty marketplace plugins.
Published under the ``gispulse.data_sources`` entry-point group in the
``gispulse`` distribution itself, so the ``ExtensionHub`` resolves it as
a first-party source and the community tier gate passes with no gating
code (EPIC #226 design decision #2 — the worldwide catalogue is free).

The catalogue is *declarative*: each YAML entry becomes a
:class:`~gispulse.core.sources.SourceEntryRef` carrying the four filter
axes — ``domain`` / ``payload`` / ``jurisdiction`` and
``access.protocol`` (issue #227) — plus a ``family`` grouping kept in
``metadata``. ``fetch()`` is inherited from :class:`DeclarativeSource`:
it delegates to the protocol fetchers registered by
``core/fetchers/`` (A3-A6, #229-#232).

Endpoints are SSRF-checked *structurally* at load — an allow-listed
scheme and a non-private host — without any DNS lookup, so importing /
constructing the source stays network-free. The full DNS-resolving SSRF
guard (#199) runs per fetch inside :class:`LazyFetcher`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from gispulse.core.logging import get_logger
from gispulse.core.ssrf import is_blocked_address
from gispulse.plugins.api import (
    AccessProtocol,
    AccessSpec,
    DeclarativeSource,
    Payload,
    SourceDomain,
    SourceEntryRef,
)

log = get_logger(__name__)

#: Curated catalogue shipped inside the ``gispulse`` package.
DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "data" / "worldwide_catalog.yml"
)

#: Schemes a catalogue endpoint may use. ``s3`` covers the GeoParquet
#: remote-table family read by DuckDB ``httpfs``; ``http(s)`` covers OGC
#: Features, STAC and direct file download.
_ALLOWED_SCHEMES = frozenset({"http", "https", "s3"})

#: Hostnames that are loopback by name — rejected without a DNS lookup.
_LOOPBACK_NAMES = frozenset({"localhost", "ip6-localhost"})

#: Sentinel for an unknown enum value passed to :meth:`WorldwideCatalogSource.catalog`
#: — it equals no entry, so the filter yields an empty result.
_UNMATCHABLE = object()


class WorldwideCatalogError(ValueError):
    """Raised when ``worldwide_catalog.yml`` is malformed or unsafe."""


def _check_endpoint_safe(endpoint: str, entry_id: str) -> None:
    """Structural SSRF check of a catalogue endpoint — no DNS lookup.

    Rejects a non-allow-listed scheme, a missing host, a literal
    private/loopback/reserved IP, and a loopback hostname. A public
    *hostname* is accepted here; the full DNS-resolving guard (#199)
    runs later, per fetch, inside the protocol fetcher.

    Raises:
        WorldwideCatalogError: the endpoint is unsafe or malformed.
    """
    parsed = urlparse(endpoint)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise WorldwideCatalogError(
            f"entry {entry_id!r}: endpoint scheme {scheme or '(none)'!r} not "
            f"allowed (expected one of {sorted(_ALLOWED_SCHEMES)})"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise WorldwideCatalogError(
            f"entry {entry_id!r}: endpoint {endpoint!r} has no host"
        )
    if host in _LOOPBACK_NAMES:
        raise WorldwideCatalogError(
            f"entry {entry_id!r}: endpoint host {host!r} is loopback"
        )
    # is_blocked_address judges an IP *literal* offline; a hostname is
    # not an IP so it falls through to the per-fetch guard.
    try:
        import ipaddress

        ipaddress.ip_address(host)
    except ValueError:
        return  # a hostname — public/private decided at fetch time
    if is_blocked_address(host):
        raise WorldwideCatalogError(
            f"entry {entry_id!r}: endpoint host {host!r} is a private / "
            f"loopback / reserved address"
        )


def _entry_from_dict(raw: dict[str, Any]) -> SourceEntryRef:
    """Build one validated :class:`SourceEntryRef` from a YAML entry.

    Raises:
        WorldwideCatalogError: a required field is missing, an axis
            value is unknown, or the endpoint fails the SSRF check.
    """
    entry_id = raw.get("id")
    if not entry_id or not isinstance(entry_id, str):
        raise WorldwideCatalogError(f"catalogue entry missing a string 'id': {raw!r}")

    access_raw = raw.get("access")
    if not isinstance(access_raw, dict):
        raise WorldwideCatalogError(f"entry {entry_id!r}: missing 'access' block")

    def _enum(enum: type, value: Any, field: str) -> Any:
        try:
            return enum(value)
        except ValueError:
            valid = sorted(m.value for m in enum)
            raise WorldwideCatalogError(
                f"entry {entry_id!r}: unknown {field} {value!r} "
                f"(expected one of {valid})"
            ) from None

    endpoint = access_raw.get("endpoint")
    if not endpoint or not isinstance(endpoint, str):
        raise WorldwideCatalogError(f"entry {entry_id!r}: 'access.endpoint' missing")
    _check_endpoint_safe(endpoint, entry_id)

    access = AccessSpec(
        protocol=_enum(AccessProtocol, access_raw.get("protocol"), "access.protocol"),
        endpoint=endpoint,
        params=dict(access_raw.get("params") or {}),
        format=access_raw.get("format"),
    )

    metadata = dict(raw.get("metadata") or {})
    family = raw.get("family")
    if family:
        metadata["family"] = family

    return SourceEntryRef(
        id=entry_id,
        name=str(raw.get("name") or entry_id),
        access=access,
        revision_token=raw.get("revision_token"),
        metadata=metadata,
        domain=_enum(SourceDomain, raw.get("domain"), "domain"),
        payload=_enum(Payload, raw.get("payload"), "payload"),
        jurisdiction=raw.get("jurisdiction"),
    )


def load_worldwide_catalog(path: Path | None = None) -> list[SourceEntryRef]:
    """Parse ``worldwide_catalog.yml`` into validated entries.

    Args:
        path: Catalogue file. Defaults to the curated one shipped in the
            ``gispulse`` package (:data:`DEFAULT_CATALOG_PATH`).

    Raises:
        WorldwideCatalogError: the file is missing, unreadable, not a
            mapping, or any entry fails validation.
    """
    import yaml  # local import — keeps module import cheap

    catalog_path = path or DEFAULT_CATALOG_PATH
    try:
        text = catalog_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorldwideCatalogError(
            f"cannot read worldwide catalogue {catalog_path}: {exc}"
        ) from exc

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise WorldwideCatalogError(
            f"worldwide catalogue {catalog_path} must be a YAML mapping"
        )

    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise WorldwideCatalogError(
            f"worldwide catalogue {catalog_path}: 'entries' must be a list"
        )

    entries = [_entry_from_dict(raw) for raw in raw_entries]
    seen: set[str] = set()
    for entry in entries:
        if entry.id in seen:
            raise WorldwideCatalogError(f"duplicate catalogue entry id {entry.id!r}")
        seen.add(entry.id)
    return entries


class WorldwideCatalogSource(DeclarativeSource):
    """Curated worldwide geo-data catalogue as a GISPulse data source.

    A :class:`DeclarativeSource` — it only *declares* entries; the fetch
    round-trip runs through the protocol fetchers (A3-A6). The catalogue
    spans many themes, so the source-level ``domain`` / ``payload`` are
    nominal: the authoritative classification lives on each
    :class:`SourceEntryRef` and :meth:`catalog` filters on it.
    """

    name = "worldwide"
    # Nominal source-level axes — the worldwide catalogue is multi-domain;
    # per-entry SourceEntryRef axes are the ones :meth:`catalog` filters on.
    domain = SourceDomain.BASE
    payload = Payload.VECTOR
    jurisdiction = "*"

    def __init__(
        self,
        catalog_path: Path | None = None,
        registry: Any | None = None,
    ) -> None:
        super().__init__(registry)
        self._catalog_path = catalog_path
        self._entries = load_worldwide_catalog(catalog_path)
        log.info("worldwide_catalog_loaded", entries=len(self._entries))

    def entries(self) -> list[SourceEntryRef]:
        """Return every catalogue entry (already validated at load)."""
        return list(self._entries)

    def families(self) -> list[str]:
        """Return the distinct ``family`` groupings, sorted."""
        return sorted(
            {
                str(e.metadata["family"])
                for e in self._entries
                if e.metadata.get("family")
            }
        )

    def catalog(
        self,
        search: str | None = None,
        *,
        domain: SourceDomain | str | None = None,
        payload: Payload | str | None = None,
        jurisdiction: str | None = None,
        protocol: AccessProtocol | str | None = None,
        family: str | None = None,
    ) -> list[SourceEntryRef]:
        """Filter the catalogue on the four classification axes.

        ``search`` matches the entry id or name (case-insensitive). The
        four axes — ``domain`` / ``payload`` / ``jurisdiction`` and the
        transport ``protocol`` — and the ``family`` grouping each narrow
        the result when supplied. Enum axes accept either the enum
        member or its string value. An unknown enum value yields an
        empty result rather than raising.
        """

        def _coerce(enum: type, value: Any) -> Any:
            if value is None or isinstance(value, enum):
                return value
            try:
                return enum(value)
            except ValueError:
                return _UNMATCHABLE

        domain_f = _coerce(SourceDomain, domain)
        payload_f = _coerce(Payload, payload)
        protocol_f = _coerce(AccessProtocol, protocol)

        q = search.lower() if search else None
        result: list[SourceEntryRef] = []
        for entry in self._entries:
            if q and q not in entry.id.lower() and q not in entry.name.lower():
                continue
            if domain_f is not None and entry.domain != domain_f:
                continue
            if payload_f is not None and entry.payload != payload_f:
                continue
            if jurisdiction is not None and entry.jurisdiction != jurisdiction:
                continue
            if protocol_f is not None and entry.access.protocol != protocol_f:
                continue
            if family is not None and entry.metadata.get("family") != family:
                continue
            result.append(entry)
        return result

    def revision(self, entry_id: str) -> str | None:
        """Freshness token for ``entry_id`` (A14, #240).

        For a *versioned* entry the declared ``revision_token`` (a release
        millésime) is authoritative — it only changes when the catalogue
        YAML is edited, so it is returned as-is. For a *live* entry
        (``revision_token: null``) a cheap HTTP ``HEAD`` probes the remote
        endpoint and derives a token from its ``ETag`` / ``Last-Modified``
        / ``Content-Length`` headers. The probe is best-effort: any
        failure (non-HTTP scheme, network error, headerless response)
        yields ``None`` so the :class:`SourceWatcherRegistry` simply sees
        no change rather than crashing.
        """
        entry = self._entry(entry_id)
        if entry.revision_token:
            return entry.revision_token
        return _probe_revision(entry.access.endpoint)


def _probe_revision(endpoint: str) -> str | None:
    """Best-effort live freshness token from an HTTP ``HEAD`` (A14, #240).

    Only ``http(s)`` endpoints are probed; the request is SSRF-guarded
    (#199) and short-timeout. Returns a token built from the validator
    headers, or ``None`` when the endpoint is non-HTTP, unreachable, or
    exposes no usable header.
    """
    scheme = urlparse(endpoint).scheme.lower()
    if scheme not in ("http", "https"):
        return None  # s3:// and friends are not HEAD-probeable here
    try:
        from gispulse.core.ssrf import guard_outbound_url

        guard_outbound_url(endpoint)
    except Exception as exc:  # noqa: BLE001 — a blocked URL is never probed
        log.warning("worldwide_revision_probe_blocked", endpoint=endpoint, error=str(exc))
        return None
    try:
        import httpx

        resp = httpx.head(endpoint, follow_redirects=True, timeout=10.0)
    except Exception as exc:  # noqa: BLE001 — an unreachable source ⇒ no change
        log.warning("worldwide_revision_probe_failed", endpoint=endpoint, error=str(exc))
        return None
    headers = resp.headers
    for header in ("etag", "last-modified", "content-length"):
        value = headers.get(header)
        if value:
            return f"{header}:{value}"
    return None


def register_worldwide_watches(
    watcher: Any,
    source: WorldwideCatalogSource | None = None,
    *,
    entry_ids: list[str] | None = None,
) -> list[str]:
    """Register worldwide catalogue entries on a :class:`SourceWatcherRegistry`.

    Wires :meth:`WorldwideCatalogSource.revision` into the freshness
    watcher (A14, #240): on the next poll an entry whose remote source has
    moved emits a ``source.changed`` event. Each entry's poll interval is
    taken from its ``metadata.update_frequency`` label (``quotidien`` /
    ``hebdomadaire`` / …), defaulting to the watcher's 6 h baseline.

    Args:
        watcher:   The :class:`SourceWatcherRegistry` to register against.
        source:    Catalogue source. A fresh :class:`WorldwideCatalogSource`
                   is built when omitted.
        entry_ids: Optional allow-list — only these entry ids are watched.

    Returns:
        The registration keys, one per watched entry.
    """
    src = source or WorldwideCatalogSource()
    keys: list[str] = []
    for entry in src.entries():
        if entry_ids is not None and entry.id not in entry_ids:
            continue
        frequency = entry.metadata.get("update_frequency")
        keys.append(watcher.register(src, entry.id, frequency=frequency))
    log.info("worldwide_watches_registered", count=len(keys))
    return keys


def register() -> None:
    """Entry-point hook for the ``gispulse.data_sources`` group.

    Registers a :class:`WorldwideCatalogSource` in the process-wide
    ``core.sources.SOURCES`` registry so the source watcher (#197) can
    resolve ``worldwide://<entry>`` URIs declared in ``triggers.yaml``.
    """
    from gispulse.core.sources import SOURCES

    SOURCES.register(WorldwideCatalogSource())


__all__ = [
    "DEFAULT_CATALOG_PATH",
    "WorldwideCatalogError",
    "WorldwideCatalogSource",
    "load_worldwide_catalog",
    "register",
    "register_worldwide_watches",
]
