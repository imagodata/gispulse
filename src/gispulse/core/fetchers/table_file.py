"""Tabular file fetcher for ``AccessProtocol.TABLE_FILE``.

This adapter is intentionally non-spatial: it materializes CSV/XLSX/ZIP table
files as files and never invents geometries. Plain CSV files can also be exposed
as DuckDB lazy scans with ``read_csv_auto``.
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

__all__ = ["TableFileFetcher"]


def _vsicurl(endpoint: str) -> str:
    if endpoint.startswith(("http://", "https://")):
        return f"/vsicurl/{endpoint}"
    return endpoint


def _metadata(access: AccessSpec) -> dict[str, str]:
    keys = ("archive_format", "table_format")
    return {key: str(access.params[key]) for key in keys if key in access.params}


class TableFileFetcher(LazyFetcher):
    """Remote/local non-spatial table files.

    ``access.params`` recognised keys:

    * ``archive_format`` — e.g. ``"zip"`` for a compressed CSV archive.
    * ``table_format`` — e.g. ``"csv"`` for the contained table format.
    * ``local_path`` — materialise destination (default: a temp file).
    """

    protocol: ClassVar[AccessProtocol] = AccessProtocol.TABLE_FILE
    payload: ClassVar[Payload] = Payload.TABLE

    @staticmethod
    def _is_zip(access: AccessSpec) -> bool:
        endpoint = access.endpoint.split("?")[0].lower()
        return endpoint.endswith(".zip") or access.params.get("archive_format") == "zip"

    @staticmethod
    def _is_csv(access: AccessSpec) -> bool:
        return access.endpoint.split("?")[0].lower().endswith(".csv")

    def _reference_scan(self, access: AccessSpec, extent: Any | None) -> str:
        """Lazy scan for plain CSV tables.

        Zipped INSEE CSV archives must be materialized first because DuckDB's
        simple ``read_csv_auto`` path cannot address the intended member file
        inside the archive without extra extraction policy.
        """
        if self._is_zip(access):
            raise NotImplementedError(
                "zipped table files must be materialized before scanning"
            )
        if not self._is_csv(access):
            raise NotImplementedError(
                "TABLE_FILE reference mode currently supports plain CSV files only"
            )
        uri = _vsicurl(access.endpoint).replace("'", "''")
        return f"read_csv_auto('{uri}')"

    def _materialize(self, access: AccessSpec, extent: Any | None) -> SourceResult:
        """Return a local table file path, downloading remote endpoints if needed."""
        if not access.endpoint.startswith(("http://", "https://")):
            return SourceResult(
                payload=self.payload,
                mode=FetchMode.MATERIALIZE,
                data=access.endpoint,
                metadata=_metadata(access),
            )

        import tempfile

        import httpx

        local_path = access.params.get("local_path")
        if not local_path:
            suffix = "." + access.endpoint.split("?")[0].rsplit(".", 1)[-1]
            handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            handle.close()
            local_path = handle.name

        with httpx.stream("GET", access.endpoint, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        log.info("table_file_materialized", path=local_path)
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.MATERIALIZE,
            data=str(local_path),
            metadata=_metadata(access),
        )
