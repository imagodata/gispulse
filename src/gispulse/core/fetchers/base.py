"""Base class for the worldwide-aggregator protocol fetchers (issue #228).

EPIC #226 (v1.9.0) needs a small set of *generic transport adapters* — one
per :class:`~gispulse.core.plugin_model.AccessProtocol` family — that each
support two fetch modes:

* :attr:`~gispulse.core.plugin_model.FetchMode.REFERENCE` — a **lazy** view:
  the adapter returns a DuckDB scan expression instead of bytes, so DuckDB
  reads the remote source zero-copy (``httpfs`` / ``spatial``);
* :attr:`~gispulse.core.plugin_model.FetchMode.MATERIALIZE` — a full local
  copy of the data.

:class:`LazyFetcher` factors out everything that is *not* protocol-specific:
the SSRF guard (issue #199) and the ``FetchMode`` dispatch. A concrete
adapter (issues A3-A6) subclasses it, sets ``protocol`` / ``payload`` and
implements only :meth:`_reference_scan` and :meth:`_materialize`.

A :class:`LazyFetcher` instance structurally satisfies the
:class:`~gispulse.core.sources.Fetcher` protocol, so it registers straight
into :data:`~gispulse.core.sources.PROTOCOLS`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from gispulse.core.config import settings
from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
    resolve_access_endpoint,
)
from gispulse.core.ssrf import guard_outbound_url

log = get_logger(__name__)

#: Key under :attr:`SourceResult.metadata` carrying the DuckDB scan SQL of
#: a ``REFERENCE`` result. The ``VirtualDatasetRegistry`` (issue A9 #235)
#: turns it into a ``CREATE VIEW … AS SELECT * FROM <scan>``.
DUCKDB_SCAN_KEY = "duckdb_scan"

__all__ = ["DUCKDB_SCAN_KEY", "LazyFetcher", "resolve_s3_materialize_uri"]


def resolve_s3_materialize_uri(access: AccessSpec) -> str | None:
    """Resolve an opt-in S3 materialization destination from access params.

    ``s3_uri`` is used as-is. ``s3_key`` is resolved under ``s3_bucket`` or
    the configured ``GISPULSE_S3_BUCKET``. Absence of both keeps the fetcher
    on its local-materialization path.
    """
    s3_uri = str(access.params.get("s3_uri", "") or "").strip()
    if s3_uri:
        return s3_uri

    s3_key = str(access.params.get("s3_key", "") or "").strip().lstrip("/")
    if not s3_key:
        return None

    bucket = str(access.params.get("s3_bucket", "") or "").strip()
    if not bucket:
        bucket = settings.s3.bucket
    return f"s3://{bucket}/{s3_key}"


class LazyFetcher(ABC):
    """A transport adapter supporting both lazy and materialised fetches.

    Subclass contract::

        class GeoParquetS3Fetcher(LazyFetcher):
            protocol = AccessProtocol.REMOTE_TABLE
            payload = Payload.VECTOR

            def _reference_scan(self, access, extent): ...
            def _materialize(self, access, extent): ...

    The base supplies :meth:`virtual_table`, :meth:`materialize` and the
    :meth:`fetch` mode dispatch, and SSRF-guards every endpoint before any
    network access — a third-party catalogue entry must not steer a fetch
    at an internal address.
    """

    #: Protocol slot this fetcher is registered under in ``PROTOCOLS``.
    #: Concrete subclasses must set it.
    protocol: ClassVar[AccessProtocol]

    #: Shape of the data this fetcher yields. Defaults to vector.
    payload: ClassVar[Payload] = Payload.VECTOR

    # -- SSRF guard --------------------------------------------------------

    @staticmethod
    def _guard(url: str | None) -> None:
        """Reject an endpoint resolving to a private/internal address.

        Thin pass-through to the shared issue #199 guard so every
        fetcher — core or third-party — shares one SSRF policy. Non-HTTP
        endpoints (local file paths) are left alone by the guard.
        """
        guard_outbound_url(url)

    # -- endpoint templating ----------------------------------------------

    @staticmethod
    def _resolve_endpoint(access: AccessSpec) -> AccessSpec:
        """Thin wrapper over :func:`resolve_access_endpoint`.

        Kept on :class:`LazyFetcher` for two reasons:

        * direct fetcher invocations (tests, advanced consumers calling
          ``HttpFileFetcher().fetch(access)`` outside the registry) still
          go through it, so a templated access is resolved before the
          SSRF guard fires;
        * ``ProtocolRegistry.dispatch_fetch`` already resolves up-front,
          so the call here is idempotent — an already-resolved endpoint
          has no ``{`` and short-circuits in
          :func:`resolve_access_endpoint`'s hot path.
        """
        return resolve_access_endpoint(access)

    # -- subclass hooks ----------------------------------------------------

    @abstractmethod
    def _reference_scan(self, access: AccessSpec, extent: Any | None) -> str:
        """Return a DuckDB scan expression that reads ``access`` zero-copy.

        Example: ``read_parquet('s3://bucket/**', hive_partitioning=true)``.

        Args:
            access: The declarative access block of the catalog entry.
            extent: An optional bounding box the subclass may push down
                    into the scan (``None`` = no spatial filter).
        """

    @abstractmethod
    def _materialize(self, access: AccessSpec, extent: Any | None) -> SourceResult:
        """Download ``access`` into a local dataset and return the result.

        The returned :class:`SourceResult` must carry
        ``mode = FetchMode.MATERIALIZE``.
        """

    # -- public API --------------------------------------------------------

    def virtual_table(
        self, access: AccessSpec, *, extent: Any | None = None
    ) -> SourceResult:
        """Build a lazy ``REFERENCE`` result — no bytes are moved.

        The DuckDB scan SQL is carried under
        ``metadata[DUCKDB_SCAN_KEY]``; the endpoint is echoed back in
        ``reference``.
        """
        access = self._resolve_endpoint(access)
        self._guard(access.endpoint)
        scan = self._reference_scan(access, extent)
        log.debug("lazy_fetch_reference", protocol=self.protocol.value)
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.REFERENCE,
            reference=access.endpoint,
            metadata={DUCKDB_SCAN_KEY: scan},
        )

    def materialize(
        self, access: AccessSpec, *, extent: Any | None = None
    ) -> SourceResult:
        """Run a full ``MATERIALIZE`` fetch — a local copy of the data."""
        access = self._resolve_endpoint(access)
        self._guard(access.endpoint)
        log.debug("lazy_fetch_materialize", protocol=self.protocol.value)
        return self._materialize(access, extent)

    def fetch(
        self,
        access: AccessSpec,
        *,
        extent: Any | None = None,
        mode: FetchMode = FetchMode.MATERIALIZE,
    ) -> SourceResult:
        """:class:`~gispulse.core.sources.Fetcher` entry point.

        Dispatches on ``mode``: ``REFERENCE`` builds a lazy view,
        ``MATERIALIZE`` copies the data.
        """
        if mode is FetchMode.REFERENCE:
            return self.virtual_table(access, extent=extent)
        return self.materialize(access, extent=extent)
