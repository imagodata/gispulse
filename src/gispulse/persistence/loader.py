"""Unified data loader — one entry point for every source GISPulse can read.

DP0 of the *generic data provider* track. A single :func:`load` reconciles
the three reading paths that already exist in the codebase but were never
wired behind one verb:

* **local / file** — any format handled by
  :func:`gispulse.persistence.io.read_vector` (GeoPackage, GeoJSON,
  Shapefile, FlatGeobuf, **GeoParquet** with bbox push-down, CSV, XLSX,
  KMZ, …);
* **remote / protocol** — anything reachable through a registered
  transport adapter in :data:`gispulse.core.sources.PROTOCOLS`
  (GeoParquet on S3/HTTP via ``remote-table``, OGC API Features, WFS,
  STAC, plain HTTP download, …), via the ``s3://`` / ``https://`` URI
  forms or an explicit access descriptor;
* **lazy / referenced** — the same remote sources as a DuckDB scan handle
  (:class:`LazyDataset`) that materialises to a GeoDataFrame on demand,
  so a project only ever reads the rows a bbox selects.

Whatever the source, :func:`load` returns a plain
:class:`geopandas.GeoDataFrame` (or a :class:`LazyDataset` when
``lazy=True``), so **every capability runs on the result unchanged** —
``gispulse.apply(verb, gispulse.load(src), ...)`` just works.

This module owns no transport code of its own: it resolves a source to a
:class:`~gispulse.core.plugin_model.AccessSpec` and delegates to the
protocol registry, then normalises the heterogeneous
:class:`~gispulse.core.plugin_model.SourceResult` (already-a-GeoDataFrame,
a materialised file path, or a DuckDB scan) into one GeoDataFrame.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import geopandas as gpd

from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    SourceResult,
)
from gispulse.core.sources import PROTOCOLS, ProtocolRegistry

log = get_logger(__name__)

#: Bounding box as ``(minx, miny, maxx, maxy)`` in the source CRS.
BBox = tuple[float, float, float, float]

#: URI schemes that are *transports* (resolved by extension), as opposed to
#: a named :class:`AccessProtocol`. A Windows drive letter (``c:``) is one
#: char long and never matches, so ``C:\path`` stays a local file.
_TRANSPORT_SCHEMES = {"http", "https", "s3", "ftp", "ftps"}

#: File extensions DuckDB can scan zero-copy as a remote table.
_REMOTE_TABLE_EXTS = {".parquet", ".pq", ".fgb"}

_fetchers_lock = threading.Lock()
_fetchers_registered = False


def _ensure_fetchers_registered(registry: ProtocolRegistry) -> None:
    """Register the core protocol fetchers into *registry* exactly once.

    The worldwide-aggregator fetchers (``remote-table``, ``ogc-features``,
    ``wfs``, ``stac``, ``download``, ``table-file``) self-register through
    :func:`~gispulse.core.fetchers.register_core_fetchers`, kept lazy so a
    bare ``import gispulse`` never pulls DuckDB/httpx. We only trigger it
    for the process-wide :data:`PROTOCOLS`; a caller-supplied registry is
    assumed to be populated as the caller sees fit.
    """
    global _fetchers_registered
    if registry is not PROTOCOLS or _fetchers_registered:
        return
    with _fetchers_lock:
        if _fetchers_registered:
            return
        from gispulse.core.fetchers import register_core_fetchers

        register_core_fetchers(registry, override=False)
        _fetchers_registered = True


# ---------------------------------------------------------------------------
# Lazy handle
# ---------------------------------------------------------------------------


@dataclass
class LazyDataset:
    """A referenced (not-yet-read) remote source backed by a DuckDB scan.

    Returned by :func:`load` with ``lazy=True``. Holds the DuckDB scan
    expression a fetcher produced in :attr:`FetchMode.REFERENCE` mode
    (``read_parquet(…)``, ``ST_Read(…)``, …) without moving any bytes.
    Call :meth:`to_geodataframe` to materialise — optionally narrowed to a
    bbox so only the needed rows are read.
    """

    scan_sql: str
    crs: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_geodataframe(self, bbox: BBox | None = None) -> gpd.GeoDataFrame:
        """Run the scan through an ephemeral DuckDB session into a GeoDataFrame.

        Args:
            bbox: Optional ``(minx, miny, maxx, maxy)`` filter applied in
                SQL via ``ST_Intersects`` against the scanned geometry.
        """
        return _scan_to_gdf(self.scan_sql, crs=self.crs, bbox=bbox)


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def resolve_source(source: "str | Path | AccessSpec | dict[str, Any]") -> "Path | AccessSpec":
    """Resolve a user-supplied source into a local path or an access spec.

    Accepted forms:

    * :class:`~gispulse.core.plugin_model.AccessSpec` — returned as-is.
    * ``dict`` — built into an ``AccessSpec`` (keys: ``protocol``,
      ``endpoint``, ``params``, ``format``, ``auth``). ``protocol`` may be
      an :class:`AccessProtocol` or its string value.
    * ``str`` / :class:`~pathlib.Path` with a transport scheme
      (``s3://``, ``https://``, …) — mapped to ``remote-table`` when the
      path ends in a DuckDB-scannable extension (``.parquet`` / ``.fgb``),
      otherwise ``download``.
    * ``str`` with a named-protocol scheme (``wfs://``, ``stac://``,
      ``ogc-features://``) — that protocol, the remainder taken as an
      ``https`` endpoint. ``proto+https://host`` forces the transport.
    * anything else — a local filesystem :class:`~pathlib.Path`.

    Raises:
        ValueError: on an unknown named protocol scheme.
    """
    if isinstance(source, AccessSpec):
        return source

    if isinstance(source, dict):
        return _access_from_dict(source)

    raw = str(source)

    # Split on "://" directly rather than via urlparse.scheme — the latter
    # rejects schemes with underscores (``ogc_features``), and a Windows
    # drive (``C:\path``) has no "://" so it stays a local path.
    head, sep, _ = raw.partition("://")
    if not sep:
        return Path(raw)
    scheme = head.lower()

    # "<proto>+<transport>://…" — explicit transport override.
    proto_name, _, _ = scheme.partition("+")

    if scheme in _TRANSPORT_SCHEMES:
        return _transport_access(raw, scheme)

    # Named AccessProtocol (wfs, stac, ogc-features, remote-table, …).
    try:
        protocol = AccessProtocol(proto_name.replace("_", "-"))
    except ValueError:
        # Unknown two-plus-char scheme that is not a transport — most
        # likely a local path on an exotic FS; let read_vector decide.
        return Path(raw)

    endpoint = _strip_scheme_to_https(raw)
    return AccessSpec(protocol=protocol, endpoint=endpoint)


def _access_from_dict(spec: dict[str, Any]) -> AccessSpec:
    """Build an :class:`AccessSpec` from a plain descriptor dict."""
    try:
        protocol = spec["protocol"]
        endpoint = spec["endpoint"]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(
            "source descriptor requires 'protocol' and 'endpoint' keys"
        ) from exc
    if not isinstance(protocol, AccessProtocol):
        protocol = AccessProtocol(str(protocol).replace("_", "-"))
    return AccessSpec(
        protocol=protocol,
        endpoint=str(endpoint),
        params=dict(spec.get("params") or {}),
        format=spec.get("format"),
        auth=spec.get("auth"),
    )


def _transport_access(uri: str, scheme: str) -> AccessSpec:
    """Map an ``s3``/``http(s)`` URI to a transport ``AccessSpec`` by extension."""
    ext = Path(urlparse(uri).path).suffix.lower()
    if ext in _REMOTE_TABLE_EXTS or (scheme == "s3" and not ext):
        return AccessSpec(protocol=AccessProtocol.REMOTE_TABLE, endpoint=uri)
    return AccessSpec(protocol=AccessProtocol.DOWNLOAD, endpoint=uri)


def _strip_scheme_to_https(uri: str) -> str:
    """Rewrite ``proto://rest`` (or ``proto+transport://rest``) as an https URL."""
    scheme, sep, rest = uri.partition("://")
    if not sep:
        return uri
    _, plus, transport = scheme.partition("+")
    proto = transport if plus else "https"
    if proto not in {"http", "https", "ftp", "ftps", "s3"}:
        proto = "https"
    return f"{proto}://{rest}"


# ---------------------------------------------------------------------------
# Result normalisation
# ---------------------------------------------------------------------------


def _result_to_gdf(
    result: SourceResult,
    *,
    bbox: BBox | None,
    layer: str | None,
) -> gpd.GeoDataFrame:
    """Normalise a heterogeneous :class:`SourceResult` into a GeoDataFrame.

    ``SourceResult.data`` is intentionally untyped — depending on the
    fetcher it is already a GeoDataFrame (OGC Features), a materialised
    file path (GeoParquet/S3, HTTP download), or ``None`` in
    :attr:`FetchMode.REFERENCE` mode with a DuckDB scan in ``metadata``.
    """
    from gispulse.core.fetchers import DUCKDB_SCAN_KEY

    data = result.data
    scan = result.metadata.get(DUCKDB_SCAN_KEY)

    if isinstance(data, gpd.GeoDataFrame):
        gdf = data
    elif result.mode is FetchMode.REFERENCE or (data is None and scan):
        if not scan:
            if result.reference:
                gdf = _read_path(result.reference, bbox=bbox, layer=layer)
            else:  # pragma: no cover - defensive
                raise ValueError(
                    "referenced SourceResult carries neither a DuckDB scan "
                    "nor a reference URL to read"
                )
        else:
            gdf = _scan_to_gdf(scan, crs=result.crs, bbox=bbox)
    elif isinstance(data, (str, Path)):
        gdf = _read_path(str(data), bbox=bbox, layer=layer)
    else:  # pragma: no cover - defensive
        raise TypeError(
            f"cannot turn SourceResult.data of type {type(data).__name__} "
            "into a GeoDataFrame"
        )

    if result.crs and gdf.crs is None:
        gdf = gdf.set_crs(result.crs)
    return gdf


def _read_path(
    path: str, *, bbox: BBox | None, layer: str | None
) -> gpd.GeoDataFrame:
    """Read a local/materialised file through the unified file reader."""
    from gispulse.persistence.io import read_vector

    kwargs: dict[str, Any] = {}
    if bbox is not None:
        kwargs["bbox"] = bbox
    if layer is not None:
        kwargs["layer"] = layer
    return read_vector(path, **kwargs)


def _scan_to_gdf(
    scan_sql: str, *, crs: str | None, bbox: BBox | None
) -> gpd.GeoDataFrame:
    """Materialise a DuckDB scan expression into a GeoDataFrame.

    *scan_sql* is a relation expression such as ``read_parquet('s3://…')``
    or ``ST_Read('…')`` (the form fetchers emit under
    :data:`~gispulse.core.fetchers.DUCKDB_SCAN_KEY`). When *bbox* is given,
    an ``ST_Intersects`` envelope predicate is pushed into the query so
    only intersecting rows are read.
    """
    from gispulse.persistence.duckdb_engine import DuckDBSession

    query = f"SELECT * FROM {scan_sql}"
    if bbox is not None:
        minx, miny, maxx, maxy = bbox
        query = (
            f"SELECT * FROM ({query}) AS _src "
            f"WHERE ST_Intersects(geom, ST_MakeEnvelope("
            f"{minx}, {miny}, {maxx}, {maxy}))"
        )

    session = DuckDBSession()
    try:
        gdf = session.sql(query)
    finally:
        session.close()

    if crs and gdf.crs is None:
        gdf = gdf.set_crs(crs)
    return gdf


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def load(
    source: "str | Path | AccessSpec | dict[str, Any] | SourceResult",
    *,
    bbox: BBox | None = None,
    layer: str | None = None,
    lazy: bool = False,
    crs: str | None = None,
    registry: ProtocolRegistry | None = None,
    **read_opts: Any,
) -> "gpd.GeoDataFrame | LazyDataset":
    """Load any supported source into a GeoDataFrame (or a lazy handle).

    Args:
        source: A local path, a ``s3://`` / ``https://`` URI, a
            named-protocol URI (``wfs://…``, ``stac://…``), an explicit
            :class:`AccessSpec`, an access-descriptor ``dict``, or a
            ready :class:`SourceResult`.
        bbox: Spatial filter ``(minx, miny, maxx, maxy)`` in the source
            CRS, pushed down to the reader/fetcher when supported.
        layer: Layer name for multi-layer file formats (GPKG/GDB/SQLite).
        lazy: When True, remote sources return a :class:`LazyDataset`
            handle instead of being materialised. Ignored (with a debug
            log) for local files, which are read eagerly.
        crs: Force this CRS on the result when the source declares none.
        registry: Protocol registry to dispatch through. Defaults to the
            process-wide :data:`PROTOCOLS`.
        **read_opts: Extra options forwarded to
            :func:`~gispulse.persistence.io.read_vector` for file sources.

    Returns:
        A :class:`geopandas.GeoDataFrame`, or a :class:`LazyDataset` when
        ``lazy=True`` and the source is remote.

    Raises:
        FileNotFoundError: If a local file source does not exist.
        ValueError: On an unresolvable source or an unreadable result.
    """
    if isinstance(source, SourceResult):
        gdf = _result_to_gdf(source, bbox=bbox, layer=layer)
        if crs and gdf.crs is None:
            gdf = gdf.set_crs(crs)
        return gdf

    resolved = resolve_source(source)

    # --- Local file path ---------------------------------------------------
    if isinstance(resolved, Path):
        if lazy:
            log.debug("loader_lazy_ignored_for_file", path=str(resolved))
        from gispulse.persistence.io import read_vector

        opts = dict(read_opts)
        if bbox is not None:
            opts["bbox"] = bbox
        if layer is not None:
            opts["layer"] = layer
        if crs is not None:
            opts["crs"] = crs
        return read_vector(str(resolved), **opts)

    # --- Remote / protocol source -----------------------------------------
    target = registry if registry is not None else PROTOCOLS
    _ensure_fetchers_registered(target)

    mode = FetchMode.REFERENCE if lazy else FetchMode.MATERIALIZE
    result = target.dispatch_fetch(resolved, extent=bbox, mode=mode)

    if lazy:
        from gispulse.core.fetchers import DUCKDB_SCAN_KEY

        scan = result.metadata.get(DUCKDB_SCAN_KEY)
        if not scan:
            # The fetcher could not produce a lazy scan (e.g. a pure
            # download protocol) — fall back to an eager read so the
            # caller still gets data rather than an empty handle.
            log.debug(
                "loader_lazy_unsupported_falling_back",
                protocol=resolved.protocol.value,
            )
            gdf = _result_to_gdf(result, bbox=bbox, layer=layer)
            if crs and gdf.crs is None:
                gdf = gdf.set_crs(crs)
            return gdf
        return LazyDataset(
            scan_sql=scan,
            crs=crs or result.crs,
            metadata=dict(result.metadata),
        )

    gdf = _result_to_gdf(result, bbox=bbox, layer=layer)
    if crs and gdf.crs is None:
        gdf = gdf.set_crs(crs)
    return gdf


__all__ = ["BBox", "LazyDataset", "load", "resolve_source"]
