"""Contracts and registry for the GISPulse ETL plugin families.

Issue #178 (epic #175). Defines the runtime Protocols for the three
data-facing plugin kinds and the registry that keeps sources and sinks
*declarative*:

- :class:`Fetcher` / :class:`Writer` — transport adapters, one per
  :class:`~core.plugin_model.AccessProtocol` (the ``protocol`` kind;
  absorbs the former ``Connector`` contract).
- :class:`DataSource` / :class:`RegulatorySource` — the ``source`` kind
  (Extract).
- :class:`DataSink` — the ``sink`` kind (Load).

A plugin author declares *what* entries exist and *how* they are reached
(an :class:`~core.plugin_model.AccessSpec` each); :class:`DeclarativeSource`
and :class:`DeclarativeSink` implement ``fetch`` / ``write`` once, by
delegating to :data:`PROTOCOLS`. No geopandas import here — the payload
travels untyped inside :class:`~core.plugin_model.SourceResult`.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.logging import get_logger
from core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    RuleClause,
    SourceDomain,
    SourceResult,
    WriteReport,
    WriteSpec,
)

log = get_logger(__name__)


class ProtocolNotSupported(LookupError):
    """Raised when no adapter is registered for a requested protocol/direction."""


# ---------------------------------------------------------------------------
# Transport adapters — the ``protocol`` plugin kind
# ---------------------------------------------------------------------------


@runtime_checkable
class Fetcher(Protocol):
    """Reads data for one :class:`~core.plugin_model.AccessProtocol`."""

    protocol: AccessProtocol

    def fetch(
        self,
        access: AccessSpec,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        ...


@runtime_checkable
class Writer(Protocol):
    """Writes data for one :class:`~core.plugin_model.AccessProtocol`."""

    protocol: AccessProtocol

    def write(self, result: SourceResult, spec: WriteSpec) -> WriteReport:
        ...


class ProtocolRegistry:
    """Thread-safe registry of transport adapters, keyed by protocol.

    An adapter may implement :class:`Fetcher`, :class:`Writer`, or both —
    it is filed under whichever role(s) it structurally satisfies. A
    single process shares one instance: :data:`PROTOCOLS`.
    """

    def __init__(self) -> None:
        self._fetchers: dict[AccessProtocol, Fetcher] = {}
        self._writers: dict[AccessProtocol, Writer] = {}
        self._lock = threading.Lock()

    def register(self, adapter: Fetcher | Writer, *, override: bool = False) -> None:
        """File ``adapter`` under its ``protocol`` for every role it satisfies.

        Raises:
            ValueError: if ``adapter`` declares no ``protocol``, or the
                slot is taken and ``override`` is False.
            TypeError: if ``adapter`` is neither a Fetcher nor a Writer.
        """
        protocol = getattr(adapter, "protocol", None)
        if not isinstance(protocol, AccessProtocol):
            raise ValueError(
                f"adapter {adapter!r} must declare a 'protocol: AccessProtocol'"
            )
        is_fetcher = isinstance(adapter, Fetcher)
        is_writer = isinstance(adapter, Writer)
        if not (is_fetcher or is_writer):
            raise TypeError(f"adapter {adapter!r} is neither a Fetcher nor a Writer")
        with self._lock:
            for filed, table in (
                (is_fetcher, self._fetchers),
                (is_writer, self._writers),
            ):
                if filed:
                    if protocol in table and not override:
                        raise ValueError(
                            f"protocol '{protocol.value}' already registered; "
                            f"pass override=True to replace"
                        )
                    table[protocol] = adapter  # type: ignore[assignment]
        log.debug(
            "protocol_adapter_registered",
            protocol=protocol.value,
            fetcher=is_fetcher,
            writer=is_writer,
        )

    def get_fetcher(self, protocol: AccessProtocol) -> Fetcher:
        try:
            return self._fetchers[protocol]
        except KeyError:
            raise ProtocolNotSupported(
                f"no fetcher for protocol '{protocol.value}'; "
                f"available: {sorted(p.value for p in self._fetchers)}"
            ) from None

    def get_writer(self, protocol: AccessProtocol) -> Writer:
        try:
            return self._writers[protocol]
        except KeyError:
            raise ProtocolNotSupported(
                f"no writer for protocol '{protocol.value}'; "
                f"available: {sorted(p.value for p in self._writers)}"
            ) from None

    def dispatch_fetch(
        self,
        access: AccessSpec,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        """Resolve ``access.protocol`` to a fetcher and run it.

        The endpoint is SSRF-checked first (issue #199): a declared
        source — or a third-party plugin — must not steer a fetch at an
        internal address. Non-HTTP endpoints (file paths) are left alone.
        """
        from core.ssrf import guard_outbound_url

        guard_outbound_url(getattr(access, "endpoint", None))
        return self.get_fetcher(access.protocol).fetch(access, extent=extent, mode=mode)

    def dispatch_write(self, result: SourceResult, spec: WriteSpec) -> WriteReport:
        """Resolve ``spec.protocol`` to a writer and run it."""
        return self.get_writer(spec.protocol).write(result, spec)


# Process-wide registry. Core fetchers (WFS / WMS / STAC / download / …)
# register here at import time; plugins of kind ``protocol`` add more
# through the ``gispulse.protocols`` entry-point group (issue #177).
PROTOCOLS = ProtocolRegistry()


# ---------------------------------------------------------------------------
# Sources — the ``source`` plugin kind (Extract)
# ---------------------------------------------------------------------------


@dataclass
class SourceEntryRef:
    """One catalog entry of a source, with its declarative access block.

    This is the *minimal* internal handle a :class:`DeclarativeSource`
    indexes. The richer ``catalog.models.CatalogEntry`` is mapped onto
    these by issue #179.
    """

    id: str
    name: str
    access: AccessSpec
    revision_token: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DataSource(Protocol):
    """A data source — answers *where data comes from* (Extract).

    ``catalog()`` returns entries (``catalog.models.CatalogEntry`` once
    issue #179 lands; typed ``Any`` here to keep ``core`` free of a
    ``catalog`` import). ``fetch()`` materializes or references one
    entry; ``revision()`` is the cheap freshness token consumed by the
    source watcher (issue #187).
    """

    name: str
    domain: SourceDomain
    payload: Payload
    jurisdiction: str

    def catalog(self, search: str | None = None) -> list[Any]:
        ...

    def fetch(
        self,
        entry_id: str,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        ...

    def schema(self, entry_id: str) -> dict[str, Any]:
        ...

    def revision(self, entry_id: str) -> str | None:
        ...


@runtime_checkable
class RegulatorySource(DataSource, Protocol):
    """A thematic source whose zones carry an applicable rule.

    Implemented by PLU/PLUi, SUP, PPR, building-code sources. ``ruleset``
    returns jurisdiction-agnostic :class:`~core.plugin_model.RuleClause`
    objects for the zone(s) intersecting ``at``.
    """

    def ruleset(self, entry_id: str, *, at: Any) -> list[RuleClause]:
        ...


@runtime_checkable
class DataSink(Protocol):
    """A data sink — answers *where results go* (Load)."""

    name: str

    def write(self, result: SourceResult, spec: WriteSpec) -> WriteReport:
        ...


# ---------------------------------------------------------------------------
# Declarative base classes — fetch()/write() implemented once
# ---------------------------------------------------------------------------


class DeclarativeSource(ABC):
    """Base for sources that are a pure declaration of entries.

    A subclass implements only :meth:`entries` (and the source-level
    ``domain`` / ``payload`` / ``jurisdiction`` attributes). ``fetch`` and
    ``revision`` are provided here by delegating to :data:`PROTOCOLS` —
    the plugin author writes no network code.
    """

    name: str
    domain: SourceDomain
    payload: Payload
    jurisdiction: str = "*"

    def __init__(self, registry: ProtocolRegistry | None = None) -> None:
        self._registry = registry or PROTOCOLS
        self._index: dict[str, SourceEntryRef] | None = None

    @abstractmethod
    def entries(self) -> list[SourceEntryRef]:
        """Return the declarative entries this source exposes."""

    def _entry(self, entry_id: str) -> SourceEntryRef:
        if self._index is None:
            self._index = {e.id: e for e in self.entries()}
        try:
            return self._index[entry_id]
        except KeyError:
            raise KeyError(
                f"{getattr(self, 'name', type(self).__name__)}: "
                f"unknown entry '{entry_id}'"
            ) from None

    def catalog(self, search: str | None = None) -> list[SourceEntryRef]:
        items = self.entries()
        if search:
            q = search.lower()
            items = [e for e in items if q in e.id.lower() or q in e.name.lower()]
        return items

    def fetch(
        self,
        entry_id: str,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        entry = self._entry(entry_id)
        return self._registry.dispatch_fetch(entry.access, extent=extent, mode=mode)

    def schema(self, entry_id: str) -> dict[str, Any]:
        """Default: no static schema. Subclasses override when known."""
        self._entry(entry_id)  # validates the id
        return {}

    def revision(self, entry_id: str) -> str | None:
        """Default: the declared token. Live sources override to poll."""
        return self._entry(entry_id).revision_token


class DeclarativeSink(ABC):
    """Base for sinks that delegate writing to a registered :class:`Writer`."""

    name: str

    def __init__(self, registry: ProtocolRegistry | None = None) -> None:
        self._registry = registry or PROTOCOLS

    def write(self, result: SourceResult, spec: WriteSpec) -> WriteReport:
        return self._registry.dispatch_write(result, spec)


__all__ = [
    "ProtocolNotSupported",
    "Fetcher",
    "Writer",
    "ProtocolRegistry",
    "PROTOCOLS",
    "SourceEntryRef",
    "DataSource",
    "RegulatorySource",
    "DataSink",
    "DeclarativeSource",
    "DeclarativeSink",
]
