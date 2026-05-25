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
local ``.parquet`` file by default, or directly to S3 when ``s3_uri`` /
``s3_key`` is supplied.
"""

from __future__ import annotations

from typing import Any, ClassVar

from gispulse.core.config import settings
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


def _s3_destination(access: AccessSpec) -> str | None:
    """Resolve an opt-in S3 materialization destination, if configured."""
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


class GeoParquetS3Fetcher(LazyFetcher):
    """Remote GeoParquet lake adapter — lazy ``read_parquet`` + ``COPY``.

    ``access.params`` recognised keys:

    * ``bbox_column`` — name of the Overture-style struct column used for
      bbox pushdown (default ``"bbox"``).
    * ``hive_partitioning`` — set ``False`` to disable Hive partition
      inference (default ``True``).
    * ``glob`` — scan suffix appended to the endpoint (default ``"/**"``);
      pass ``""`` when the endpoint is a single ``.parquet`` file.
    * ``s3_uri`` / ``s3_key`` — opt-in materialisation destination. When
      present, ``COPY`` writes directly to S3/Garage instead of a local
      temp file. ``s3_key`` is resolved under ``settings.s3.bucket`` unless
      ``s3_bucket`` is supplied.
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
        """Copy the (optionally bbox-clipped) lake into a parquet file.

        DuckDB / :class:`DuckDBSession` are imported lazily — ``import
        gispulse`` must stay free of the engine. By default the destination
        path is carried in ``access.params['local_path']`` (or a temp file);
        ``s3_uri`` / ``s3_key`` opt into a direct S3/Garage ``COPY``.
        """
        import tempfile

        from gispulse.persistence.duckdb_engine import DuckDBSession

        destination = _s3_destination(access)
        is_s3_destination = destination is not None
        if destination is None:
            local_path = access.params.get("local_path")
            if local_path:
                destination = str(local_path)
            else:
                handle = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
                handle.close()
                destination = handle.name

        select = self._reference_scan(access, extent)
        # _reference_scan already yields either a table function or a
        # sub-query; both are valid in a FROM clause.
        dest = str(destination).replace("'", "''")
        copy_sql = (
            f"COPY (SELECT * FROM {select}) TO '{dest}' (FORMAT PARQUET)"
        )
        # DuckDBSession.open() loads httpfs + spatial (A7).
        with DuckDBSession() as session:
            session.conn.execute(copy_sql)
        log.info("geoparquet_materialized", path=destination)
        metadata = {"copy_sql": copy_sql}
        if is_s3_destination:
            metadata["s3_uri"] = destination
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.MATERIALIZE,
            data=destination,
            reference=destination if is_s3_destination else None,
            extent=tuple(extent) if extent else None,
            metadata=metadata,
        )
