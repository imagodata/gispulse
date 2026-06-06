"""Local vector-file fetcher for ``AccessProtocol.LOCAL_FILE``.

Reads any local vector file supported by :func:`~gispulse.persistence.io.read_vector`
(KML, KMZ, GeoPackage, GeoJSON, Shapefile, FlatGeobuf, …) and returns the
contents as a :class:`~geopandas.GeoDataFrame` in ``SourceResult.data``.

This fetcher is *materialize-only*: local files have no DuckDB lazy-scan
path (no ``/vsicurl/`` integration for local KML/KMZ), so ``REFERENCE`` mode
raises :class:`NotImplementedError` with a clear message.

``access.params`` recognised keys:

* ``layer`` — layer name for multi-layer formats (GPKG, GDB, SQLite).
* ``bbox``  — spatial filter as ``(minx, miny, maxx, maxy)``.
* ``rows``  — maximum number of rows to read.
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

__all__ = ["VectorFileFetcher"]


class VectorFileFetcher(LazyFetcher):
    """Local vector-file adapter — delegates to ``persistence.io.read_vector``.

    Returns a :class:`~geopandas.GeoDataFrame` in ``SourceResult.data``.
    REFERENCE mode is not supported (local files are not DuckDB-scannable
    via ``/vsicurl/``); it raises :class:`NotImplementedError`.
    """

    protocol: ClassVar[AccessProtocol] = AccessProtocol.LOCAL_FILE
    payload: ClassVar[Payload] = Payload.VECTOR

    # -- LazyFetcher hooks ---------------------------------------------------

    def _reference_scan(self, access: AccessSpec, extent: Any | None) -> str:
        """Not supported for local files — raises NotImplementedError."""
        raise NotImplementedError(
            "VectorFileFetcher does not support REFERENCE mode. "
            "Local KML/KMZ/vector files cannot be exposed as a DuckDB lazy scan. "
            "Use FetchMode.MATERIALIZE instead."
        )

    def _materialize(self, access: AccessSpec, extent: Any | None) -> SourceResult:
        """Read the local file and return a GeoDataFrame in SourceResult.data.

        Delegates entirely to :func:`~gispulse.persistence.io.read_vector`,
        which handles KMZ decompression, KML, GPKG, GeoJSON, and all other
        supported vector formats.

        Args:
            access: Must have ``endpoint`` set to an existing local file path.
            extent: Optional bounding box ``(minx, miny, maxx, maxy)`` pushed
                    down to ``read_vector`` as ``bbox``.

        Raises:
            FileNotFoundError: if ``access.endpoint`` does not exist.
            ValueError: if the file extension is not a recognised vector format.
        """
        from gispulse.persistence.io import read_vector

        layer = access.params.get("layer")
        rows = access.params.get("rows")
        bbox: tuple[float, float, float, float] | None = None
        if extent:
            bbox = (
                float(extent[0]),
                float(extent[1]),
                float(extent[2]),
                float(extent[3]),
            )

        gdf = read_vector(
            access.endpoint,
            layer=layer,
            bbox=bbox,
            rows=rows,
        )
        log.info(
            "vector_file_materialized",
            path=access.endpoint,
            rows=len(gdf),
        )
        return SourceResult(
            payload=self.payload,
            mode=FetchMode.MATERIALIZE,
            data=gdf,
        )
