"""Lazy virtual datasets — a worldwide-catalogue entry as a DuckDB view (A9, #235).

EPIC #226 (v1.9.0). A *virtual dataset* turns one curated catalogue
entry (a :class:`~gispulse.core.sources.SourceEntryRef` from
:class:`WorldwideCatalogSource`, A8) into a DuckDB **view** —
``CREATE OR REPLACE VIEW … AS SELECT * FROM <scan>`` — without moving any
bytes. The scan SQL is produced by the protocol fetchers of
``core/fetchers/`` (A3-A6): ``read_parquet(…)`` for remote GeoParquet,
``ST_Read(…)`` for OGC Features, and so on.

Nothing here touches the network until :func:`materialize_virtual_view`
asks a fetcher for the scan. The fetcher is also where a bounding box is
pushed down — on a *global* source a project bbox is mandatory — so the
view created for a project only ever scans the rows it needs.

Materialising a virtual dataset into a real project dataset is a
separate step: re-run the same fetcher in
:attr:`~gispulse.core.plugin_model.FetchMode.MATERIALIZE` mode.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from gispulse.core.fetchers import DUCKDB_SCAN_KEY
from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import FetchMode
from gispulse.core.sources import PROTOCOLS, ProtocolRegistry, SourceEntryRef

log = get_logger(__name__)

#: Scheme prefixing every synthetic virtual-dataset id.
VIRTUAL_ID_SCHEME = "virtual:"

#: Bounding box as ``(minx, miny, maxx, maxy)``.
BBox = tuple[float, float, float, float]


class VirtualDatasetError(RuntimeError):
    """Raised when a virtual dataset cannot be built or materialised."""


# ---------------------------------------------------------------------------
# Synthetic id helpers
# ---------------------------------------------------------------------------


def make_virtual_id(source_name: str, entry_id: str) -> str:
    """Build the synthetic id ``virtual:<source>/<entry>``.

    Raises:
        VirtualDatasetError: ``source_name`` or ``entry_id`` is empty.
    """
    if not source_name or not entry_id:
        raise VirtualDatasetError("source_name and entry_id must both be non-empty")
    return f"{VIRTUAL_ID_SCHEME}{source_name}/{entry_id}"


def parse_virtual_id(virtual_id: str) -> tuple[str, str]:
    """Split a synthetic id back into ``(source_name, entry_id)``.

    Raises:
        VirtualDatasetError: the id is not a well-formed virtual id.
    """
    if not virtual_id.startswith(VIRTUAL_ID_SCHEME):
        raise VirtualDatasetError(
            f"{virtual_id!r} is not a virtual id (expected {VIRTUAL_ID_SCHEME!r} prefix)"
        )
    body = virtual_id[len(VIRTUAL_ID_SCHEME) :]
    source_name, sep, entry_id = body.partition("/")
    if not sep or not source_name or not entry_id:
        raise VirtualDatasetError(
            f"malformed virtual id {virtual_id!r} (expected 'virtual:<source>/<entry>')"
        )
    return source_name, entry_id


def _safe_view_name(entry_id: str) -> str:
    """A DuckDB-safe view identifier derived from an entry id."""
    cleaned = "".join(c if c.isalnum() else "_" for c in entry_id.lower())
    return f"v_{cleaned}" if cleaned else "v_unnamed"


# ---------------------------------------------------------------------------
# Virtual dataset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VirtualDataset:
    """A catalogue entry exposed as a lazy DuckDB view.

    Identity only — it stores no SQL and touches no network. The scan is
    resolved (and a bbox pushed down) on demand by
    :func:`materialize_virtual_view`.
    """

    id: str
    source_name: str
    entry: SourceEntryRef

    @property
    def entry_id(self) -> str:
        return self.entry.id

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def view_name(self) -> str:
        """The DuckDB view identifier this dataset materialises into."""
        return _safe_view_name(self.entry.id)

    @property
    def source_uri(self) -> str:
        """The watcher-resolvable URI ``<source>://<entry>`` (#197)."""
        return f"{self.source_name}://{self.entry_id}"

    @property
    def payload(self) -> str:
        """Data category — ``vector`` / ``raster`` / … (defaults to vector)."""
        return self.entry.payload.value if self.entry.payload else "vector"

    @property
    def crs(self) -> str:
        """Declared CRS, or the WGS84 default of the worldwide catalogue."""
        return str(self.entry.metadata.get("crs", "EPSG:4326"))


def to_dataset_meta(
    vds: VirtualDataset,
    *,
    feature_count: int | None = None,
    bbox: BBox | None = None,
) -> dict[str, Any]:
    """Project a :class:`VirtualDataset` to a portal ``DatasetMeta`` dict.

    ``source_type`` is ``"virtual"`` and ``file_size`` is ``0`` — a
    virtual dataset occupies no disk. ``feature_count`` / ``bbox`` are
    left ``None`` until a preview computes them lazily, bbox-scoped (A10).
    """
    metadata = dict(vds.entry.metadata)
    if vds.entry.domain is not None:
        metadata.setdefault("domain", vds.entry.domain.value)
    if vds.entry.jurisdiction is not None:
        metadata.setdefault("jurisdiction", vds.entry.jurisdiction)
    metadata.setdefault("protocol", vds.entry.access.protocol.value)
    return {
        "id": vds.id,
        "name": vds.name,
        "source_type": "virtual",
        "virtual_source_uri": vds.source_uri,
        "data_category": vds.payload,
        "crs": vds.crs,
        "format": "virtual",
        "file_size": 0,
        "feature_count": feature_count,
        "virtual_bbox": list(bbox) if bbox else None,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# View materialisation — the only network-touching path
# ---------------------------------------------------------------------------


def materialize_virtual_view(
    session: Any,
    vds: VirtualDataset,
    *,
    bbox: BBox | None = None,
    protocols: ProtocolRegistry | None = None,
) -> str:
    """Create (or replace) the DuckDB view backing ``vds`` on ``session``.

    The protocol fetcher resolves the lazy scan; passing ``bbox`` lets it
    push the spatial predicate down into the scan (e.g. against the
    Overture ``bbox`` struct) so DuckDB prunes before reading. The view
    is then ``SELECT * FROM <scan>`` — querying it streams only the
    bbox-scoped rows.

    Args:
        session:   An open ``DuckDBSession`` (``httpfs`` loaded by A7).
        vds:       The virtual dataset to materialise.
        bbox:      Optional ``(minx, miny, maxx, maxy)`` pushed into the scan.
        protocols: Fetcher registry. Defaults to the process-wide
                   :data:`~gispulse.core.sources.PROTOCOLS`.

    Returns:
        The name of the created view.

    Raises:
        VirtualDatasetError: the fetcher returned a non-lazy result with
            no DuckDB scan expression.
    """
    registry = protocols if protocols is not None else PROTOCOLS
    result = registry.dispatch_fetch(
        vds.entry.access, extent=bbox, mode=FetchMode.REFERENCE
    )
    scan = result.metadata.get(DUCKDB_SCAN_KEY)
    if not scan:
        raise VirtualDatasetError(
            f"fetcher for {vds.id} ({vds.entry.access.protocol.value}) returned "
            f"no '{DUCKDB_SCAN_KEY}' scan — cannot build a lazy view"
        )
    view = vds.view_name
    session.conn.execute(f'CREATE OR REPLACE VIEW "{view}" AS SELECT * FROM {scan}')
    log.info(
        "virtual_view_materialized",
        virtual_id=vds.id,
        view=view,
        bbox_scoped=bbox is not None,
    )
    return view


def count_features(session: Any, view_name: str) -> int:
    """Return the row count of a materialised virtual view.

    Cheap lazy stat for a portal preview — on a bbox-scoped view it
    counts only the clipped rows.
    """
    row = session.conn.execute(f'SELECT count(*) FROM "{view_name}"').fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class VirtualDatasetRegistry:
    """Process-wide registry of :class:`VirtualDataset`, keyed by synthetic id.

    Thread-safe. :meth:`create` is the normal entry point — it validates
    that a fetcher exists for the entry's protocol, then files the
    dataset; construction touches no network.
    """

    def __init__(self) -> None:
        self._items: dict[str, VirtualDataset] = {}
        self._lock = threading.Lock()

    def create(
        self,
        entry: SourceEntryRef,
        *,
        source_name: str = "worldwide",
        protocols: ProtocolRegistry | None = None,
    ) -> VirtualDataset:
        """Build, register and return a virtual dataset for ``entry``.

        Raises:
            ProtocolNotSupported: no fetcher is registered for the
                entry's ``access.protocol``.
        """
        registry = protocols if protocols is not None else PROTOCOLS
        # Fail fast: a virtual dataset with no fetcher can never be viewed.
        registry.get_fetcher(entry.access.protocol)
        vds = VirtualDataset(
            id=make_virtual_id(source_name, entry.id),
            source_name=source_name,
            entry=entry,
        )
        self.register(vds)
        return vds

    def register(self, vds: VirtualDataset) -> None:
        """File ``vds`` under its id — last registration wins."""
        with self._lock:
            self._items[vds.id] = vds
        log.debug("virtual_dataset_registered", virtual_id=vds.id)

    def get(self, virtual_id: str) -> VirtualDataset:
        """Return the dataset registered under ``virtual_id``.

        Raises:
            KeyError: no dataset is registered under that id.
        """
        try:
            return self._items[virtual_id]
        except KeyError:
            raise KeyError(
                f"no virtual dataset {virtual_id!r} "
                f"(registered: {', '.join(sorted(self._items)) or 'none'})"
            ) from None

    def list(self) -> list[VirtualDataset]:
        """Every registered dataset, ordered by id."""
        with self._lock:
            return [self._items[k] for k in sorted(self._items)]

    def remove(self, virtual_id: str) -> bool:
        """Drop ``virtual_id``; return ``True`` if it was present."""
        with self._lock:
            return self._items.pop(virtual_id, None) is not None

    def clear(self) -> None:
        """Drop every registration — used by tests for isolation."""
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, virtual_id: object) -> bool:
        return virtual_id in self._items


#: Process-wide virtual-dataset registry. The worldwide catalogue HTTP
#: endpoints (A10 #236) and the ETL pipeline-prepare hook (A11 #237)
#: share this instance.
VIRTUAL_DATASETS = VirtualDatasetRegistry()


__all__ = [
    "BBox",
    "VIRTUAL_ID_SCHEME",
    "VIRTUAL_DATASETS",
    "VirtualDataset",
    "VirtualDatasetError",
    "VirtualDatasetRegistry",
    "count_features",
    "make_virtual_id",
    "materialize_virtual_view",
    "parse_virtual_id",
    "to_dataset_meta",
]
