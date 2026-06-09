"""Tabular file fetcher for ``AccessProtocol.TABLE_FILE``.

This adapter is intentionally non-spatial: it materializes CSV/XLSX/ZIP table
files as files and never invents geometries. CSV files, including CSV members
inside ZIP archives, can also be exposed as DuckDB lazy scans with
``read_csv_auto``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from gispulse.core.fetchers._http_download import stream_http_to_file
from gispulse.core.fetchers.base import LazyFetcher, resolve_s3_materialize_uri
from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import (
    AccessProtocol,
    AccessSpec,
    FetchMode,
    Payload,
    SourceResult,
)

log = get_logger(__name__)

__all__ = ["TableFileFetcher"]


def _vsicurl(endpoint: str) -> str:
    if endpoint.startswith(("http://", "https://")):
        return f"/vsicurl/{endpoint}"
    return endpoint


def _metadata(access: AccessSpec) -> dict[str, str]:
    keys = ("archive_format", "archive_member", "table_format")
    return {key: str(access.params[key]) for key in keys if key in access.params}


def _sql_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _archive_member(access: AccessSpec) -> str:
    raw_member = access.params.get("archive_member")
    if not raw_member:
        raise ValueError(
            "TABLE_FILE zip archives require access.params.archive_member"
        )
    member = str(raw_member).lstrip("/")
    if not member or "*" in member:
        raise ValueError(
            "TABLE_FILE zip archive_member must name one concrete member"
        )
    return member


class TableFileFetcher(LazyFetcher):
    """Remote/local non-spatial table files.

    ``access.params`` recognised keys:

    * ``archive_format`` — e.g. ``"zip"`` for a compressed CSV archive.
    * ``table_format`` — e.g. ``"csv"`` for the contained table format.
    * ``local_path`` — materialise destination (default: a temp file).
    * ``s3_uri`` / ``s3_key`` — opt-in materialisation destination. When
      present, DuckDB writes the parsed table scan to S3/Garage as Parquet.
    """

    protocol: ClassVar[AccessProtocol] = AccessProtocol.TABLE_FILE
    payload: ClassVar[Payload] = Payload.TABLE

    @staticmethod
    def _is_zip(access: AccessSpec) -> bool:
        endpoint = access.endpoint.split("?")[0].lower()
        return endpoint.endswith(".zip") or access.params.get("archive_format") == "zip"

    @staticmethod
    def _is_csv(access: AccessSpec) -> bool:
        endpoint = access.endpoint.split("?")[0].lower()
        return endpoint.endswith(".csv") or access.params.get("table_format") == "csv"

    @staticmethod
    def _table_uri(access: AccessSpec) -> str:
        uri = _vsicurl(access.endpoint)
        if TableFileFetcher._is_zip(access):
            member = _archive_member(access)
            return f"/vsizip/{uri}/{member}"
        return uri

    def _reference_scan(self, access: AccessSpec, extent: Any | None) -> str:
        """Lazy scan for CSV tables.

        ZIP archives use GDAL's ``/vsizip/`` virtual path and require
        ``archive_member`` so multiple CSV members are never read silently.
        """
        if not self._is_csv(access):
            raise NotImplementedError(
                "TABLE_FILE reference mode currently supports plain CSV files only"
            )
        uri = self._table_uri(access)
        return f"read_csv_auto({_sql_literal(uri)})"

    def _materialize(self, access: AccessSpec, extent: Any | None) -> SourceResult:
        """Return a table file path, or write a parsed Parquet copy to S3."""
        s3_destination = resolve_s3_materialize_uri(access)
        if s3_destination:
            from gispulse.persistence.duckdb_engine import DuckDBSession

            select = self._reference_scan(access, extent)
            dest = s3_destination.replace("'", "''")
            copy_sql = f"COPY (SELECT * FROM {select}) TO '{dest}' (FORMAT PARQUET)"
            with DuckDBSession() as session:
                session.conn.execute(copy_sql)
            log.info("table_file_materialized", path=s3_destination)
            metadata = _metadata(access)
            metadata.update({"copy_sql": copy_sql, "s3_uri": s3_destination})
            return SourceResult(
                payload=self.payload,
                mode=FetchMode.MATERIALIZE,
                data=s3_destination,
                reference=s3_destination,
                extent=tuple(extent) if extent else None,
                metadata=metadata,
            )

        if not access.endpoint.startswith(("http://", "https://")):
            return SourceResult(
                payload=self.payload,
                mode=FetchMode.MATERIALIZE,
                data=access.endpoint,
                metadata=_metadata(access),
            )

        import tempfile

        local_path = access.params.get("local_path")
        if not local_path:
            suffix = "." + access.endpoint.split("?")[0].rsplit(".", 1)[-1]
            handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            handle.close()
            local_path = handle.name

        stream_http_to_file(
            access.endpoint,
            local_path,
            retry_log_event="table_file_download_retry",
        )
        log.info("table_file_materialized", path=local_path)
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.MATERIALIZE,
            data=str(local_path),
            metadata=_metadata(access),
        )
