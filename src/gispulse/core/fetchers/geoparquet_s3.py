"""A3 (issue #229) — remote GeoParquet fetcher for the worldwide aggregator.

``GeoParquetS3Fetcher`` is the core transport adapter for
:attr:`~gispulse.core.plugin_model.AccessProtocol.REMOTE_TABLE`: a Hive-
partitioned GeoParquet lake reachable over ``s3://`` / ``https://`` and
read zero-copy by DuckDB ``httpfs`` (loaded by ``DuckDBSession.open()``,
A7).

The lazy path emits a ``read_parquet(...)`` scan; when a bounding box is
supplied it is pushed down against the Overture ``bbox`` struct column
(``bbox.xmin/.ymin/.xmax/.ymax``) so DuckDB prunes row groups before any
bytes leave the lake. The materialise path runs a ``COPY ... TO`` into a
local ``.parquet`` file.
"""

from __future__ import annotations

from typing import Any, ClassVar

from gispulse.core.fetchers.base import LazyFetcher
from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
)

log = get_logger(__name__)

__all__ = ["GeoParquetS3Fetcher"]


def _bbox_predicate(extent: Any | None, column: str) -> str | None:
    """Build a DuckDB ``WHERE`` predicate against an Overture ``bbox`` struct.

    Returns ``None`` when ``extent`` is falsy. The predicate keeps every
    row whose ``bbox`` struct *intersects* ``extent`` — a standard
    separating-axis test on ``(xmin, ymin, xmax, ymax)``.
    """
    if not extent:
        return None
    minx, miny, maxx, maxy = (float(c) for c in extent)
    return (
        f"{column}.xmin <= {maxx} AND {column}.xmax >= {minx} "
        f"AND {column}.ymin <= {maxy} AND {column}.ymax >= {miny}"
    )


class GeoParquetS3Fetcher(LazyFetcher):
    """Remote GeoParquet lake adapter — lazy ``read_parquet`` + ``COPY``.

    ``access.params`` recognised keys:

    * ``bbox_column`` — name of the Overture-style struct column used for
      bbox pushdown (default ``"bbox"``).
    * ``hive_partitioning`` — set ``False`` to disable Hive partition
      inference (default ``True``).
    * ``glob`` — scan suffix appended to the endpoint (default ``"/**"``);
      pass ``""`` when the endpoint is a single ``.parquet`` file.
    """

    protocol: ClassVar[AccessProtocol] = AccessProtocol.REMOTE_TABLE
    payload: ClassVar[Payload] = Payload.VECTOR

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _scan_glob(access: AccessSpec) -> str:
        """Endpoint + glob suffix — a single URI DuckDB can ``read_parquet``."""
        glob = access.params.get("glob", "/**")
        endpoint = access.endpoint.rstrip("/") if glob else access.endpoint
        return f"{endpoint}{glob}"

    @classmethod
    def _read_parquet(cls, access: AccessSpec) -> str:
        """``read_parquet('<uri>', hive_partitioning=true)`` table function."""
        uri = cls._scan_glob(access).replace("'", "''")
        hive = access.params.get("hive_partitioning", True)
        hive_sql = "true" if hive else "false"
        return f"read_parquet('{uri}', hive_partitioning={hive_sql})"

    # -- LazyFetcher hooks -------------------------------------------------

    def _reference_scan(self, access: AccessSpec, extent: Any | None) -> str:
        """Lazy scan: ``read_parquet`` wrapped in a bbox sub-query if needed."""
        scan = self._read_parquet(access)
        column = access.params.get("bbox_column", "bbox")
        predicate = _bbox_predicate(extent, column)
        if predicate is None:
            return scan
        # Wrap in a sub-query so the pushed-down WHERE survives whatever
        # the VirtualDatasetRegistry (A9 #235) wraps the scan with.
        return f"(SELECT * FROM {scan} WHERE {predicate})"

    def _materialize(self, access: AccessSpec, extent: Any | None) -> SourceResult:
        """Copy the (optionally bbox-clipped) lake into a local parquet file.

        DuckDB / :class:`DuckDBSession` are imported lazily — ``import
        gispulse`` must stay free of the engine. The destination path is
        carried in ``access.params['local_path']`` (default: a temp file).
        """
        import tempfile

        from gispulse.persistence.duckdb_engine import DuckDBSession

        local_path = access.params.get("local_path")
        if not local_path:
            handle = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
            handle.close()
            local_path = handle.name

        select = self._reference_scan(access, extent)
        # _reference_scan already yields either a table function or a
        # sub-query; both are valid in a FROM clause.
        dest = str(local_path).replace("'", "''")
        copy_sql = (
            f"COPY (SELECT * FROM {select}) TO '{dest}' (FORMAT PARQUET)"
        )
        # DuckDBSession.open() loads httpfs + spatial (A7).
        with DuckDBSession() as session:
            session.conn.execute(copy_sql)
        log.info("geoparquet_materialized", path=local_path)
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.MATERIALIZE,
            data=local_path,
            extent=tuple(extent) if extent else None,
            metadata={"copy_sql": copy_sql},
        )
