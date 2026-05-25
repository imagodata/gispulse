"""A6 (issue #232) — remote-file fetcher for the worldwide aggregator.

``HttpFileFetcher`` is the core transport adapter for
:attr:`~gispulse.core.plugin_model.AccessProtocol.DOWNLOAD`: a plain
remote file (CSV with lat/lon columns, GeoJSON, GeoPackage, …) reached
over HTTP.

The lazy path leans on DuckDB's ``/vsicurl/`` GDAL streaming so the file
is read in place by ``ST_Read`` (spatial formats) or ``read_csv_auto``
(point CSVs). The materialise path streams the raw file to local disk by
default, or copies the parsed table directly to S3/Garage as Parquet when
``s3_uri`` / ``s3_key`` is supplied.
"""

from __future__ import annotations

from typing import Any, ClassVar

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

__all__ = ["HttpFileFetcher"]

#: File extensions DuckDB's ``spatial`` extension reads through ``ST_Read``
#: (the GDAL/OGR driver). A CSV is handled separately — it needs explicit
#: lat/lon → geometry construction.
_SPATIAL_SUFFIXES = (".geojson", ".json", ".gpkg", ".fgb", ".shp", ".gml")


def _vsicurl(endpoint: str) -> str:
    """Wrap an HTTP(S) endpoint in a GDAL ``/vsicurl/`` virtual path.

    Local paths and already-wrapped paths are returned untouched, so the
    same fetcher serves offline fixtures.
    """
    if endpoint.startswith(("http://", "https://")):
        return f"/vsicurl/{endpoint}"
    return endpoint


class HttpFileFetcher(LazyFetcher):
    """Remote single-file adapter — ``/vsicurl/`` lazy scan + streamed download.

    ``access.params`` recognised keys:

    * ``lat`` / ``lon`` — column names for a point CSV (default
      ``"latitude"`` / ``"longitude"``); only consulted for ``.csv``.
    * ``layer`` — layer name for a multi-layer source (GPKG).
    * ``local_path`` — materialise destination (default: a temp file).
    * ``s3_uri`` / ``s3_key`` — opt-in materialisation destination. When
      present, DuckDB writes the parsed scan to S3/Garage as Parquet.

    The payload is declared :attr:`~gispulse.core.plugin_model.Payload.VECTOR`
    — the v1.9.0 download protocol targets vector files.
    """

    protocol: ClassVar[AccessProtocol] = AccessProtocol.DOWNLOAD
    payload: ClassVar[Payload] = Payload.VECTOR

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _is_csv(endpoint: str) -> bool:
        return endpoint.split("?")[0].lower().endswith(".csv")

    @staticmethod
    def _bbox_clause(extent: Any | None, geom_expr: str) -> str:
        """``WHERE`` clause keeping geometries inside ``extent``, or ``""``."""
        if not extent:
            return ""
        minx, miny, maxx, maxy = (float(c) for c in extent)
        envelope = (
            f"ST_MakeEnvelope({minx}, {miny}, {maxx}, {maxy})"
        )
        return f" WHERE ST_Intersects({geom_expr}, {envelope})"

    def _csv_scan(self, access: AccessSpec, extent: Any | None) -> str:
        """Point-CSV scan: ``read_csv_auto`` + ``ST_Point`` geometry build."""
        uri = _vsicurl(access.endpoint).replace("'", "''")
        lat = access.params.get("lat", "latitude")
        lon = access.params.get("lon", "longitude")
        geom = f'ST_Point("{lon}", "{lat}")'
        where = self._bbox_clause(extent, geom)
        return (
            f"(SELECT *, {geom} AS geometry "
            f"FROM read_csv_auto('{uri}'){where})"
        )

    def _spatial_scan(self, access: AccessSpec, extent: Any | None) -> str:
        """Spatial-file scan via ``ST_Read`` (GeoJSON / GPKG / FGB / SHP)."""
        uri = _vsicurl(access.endpoint).replace("'", "''")
        layer = access.params.get("layer")
        st_read = f"ST_Read('{uri}'"
        if layer:
            st_read += f", layer='{str(layer).replace(chr(39), chr(39) * 2)}'"
        st_read += ")"
        where = self._bbox_clause(extent, "geom")
        if not where:
            return st_read
        return f"(SELECT * FROM {st_read}{where})"

    # -- LazyFetcher hooks -------------------------------------------------

    def _reference_scan(self, access: AccessSpec, extent: Any | None) -> str:
        """Lazy scan — CSV gets ``read_csv_auto``, everything else ``ST_Read``.

        An unrecognised suffix falls through to ``ST_Read``; GDAL probes
        the driver from the file content.
        """
        endpoint_l = access.endpoint.split("?")[0].lower()
        if self._is_csv(access.endpoint):
            return self._csv_scan(access, extent)
        if not endpoint_l.endswith(_SPATIAL_SUFFIXES):
            log.debug("http_file_unknown_suffix", endpoint=access.endpoint)
        return self._spatial_scan(access, extent)

    def _materialize(self, access: AccessSpec, extent: Any | None) -> SourceResult:
        """Materialise the remote file.

        By default this streams the raw file to local disk with ``httpx``.
        When ``s3_uri`` / ``s3_key`` is set, it writes the parsed DuckDB scan
        directly to S3/Garage as Parquet; in that path ``extent`` is pushed
        into the scan before ``COPY``.
        """
        import tempfile

        import httpx

        s3_destination = resolve_s3_materialize_uri(access)
        if s3_destination:
            from gispulse.persistence.duckdb_engine import DuckDBSession

            select = self._reference_scan(access, extent)
            dest = s3_destination.replace("'", "''")
            copy_sql = (
                f"COPY (SELECT * FROM {select}) TO '{dest}' (FORMAT PARQUET)"
            )
            with DuckDBSession() as session:
                session.conn.execute(copy_sql)
            log.info("http_file_materialized", path=s3_destination)
            return SourceResult(
                payload=self.payload,
                mode=FetchMode.MATERIALIZE,
                data=s3_destination,
                reference=s3_destination,
                extent=tuple(extent) if extent else None,
                metadata={"copy_sql": copy_sql, "s3_uri": s3_destination},
            )

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
        log.info("http_file_materialized", path=local_path)
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.MATERIALIZE,
            data=local_path,
        )
