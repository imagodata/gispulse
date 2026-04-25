"""
DuckDB session engine for GISPulse.

Provides an in-memory spatial database session backed by DuckDB + spatial
extension. GPKG files are loaded natively (DuckDB reads them directly).
GeoDataFrames can be round-tripped through DuckDB for SQL-based operations.

Implements :class:`SpatialEngine` (Phase 3) so the rest of the codebase
can use DuckDB and PostGIS interchangeably.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import geopandas as gpd
from shapely import wkb

from core.logging import get_logger
from persistence.engine import SpatialEngine

log = get_logger(__name__)


class DuckDBSession(SpatialEngine):
    """Ephemeral DuckDB spatial session.

    Usage::

        with DuckDBSession() as session:
            gdf = session.load_gpkg("data.gpkg", layer="parcelles")
            result = session.sql("SELECT * FROM parcelles WHERE area > 100")
            session.to_gpkg(result, "output.gpkg", layer="filtered")
    """

    def __init__(self, database: str = ":memory:") -> None:
        self.database = database
        self._conn: duckdb.DuckDBPyConnection | None = None

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> "DuckDBSession":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        self._conn = duckdb.connect(self.database)
        try:
            self._conn.load_extension("spatial")
        except Exception:
            try:
                self._conn.install_extension("spatial")
                self._conn.load_extension("spatial")
            except Exception as exc:
                log.warning("duckdb_spatial_extension_failed", error=str(exc))
                # Continue without spatial extension — GPKG I/O uses GeoPandas/Fiona
        log.debug("duckdb_session_opened", database=self.database)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            log.debug("duckdb_session_closed")

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            raise RuntimeError("DuckDB session is not open. Call open() or use as context manager.")
        return self._conn

    # -- GPKG I/O ----------------------------------------------------------

    def load_gpkg(self, path: str | Path, layer: str | None = None) -> gpd.GeoDataFrame:
        """Load a layer from a GPKG file into a GeoDataFrame via DuckDB.

        Args:
            path:  Path to the .gpkg file.
            layer: Layer name. If None, reads the first layer.

        Returns:
            GeoDataFrame with geometries parsed from WKB.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"GPKG file not found: {path}")

        if layer is None:
            layers = self.list_gpkg_layers(path)
            if not layers:
                raise ValueError(f"No layers found in {path}")
            layer = layers[0]

        # Sanitize path and layer to prevent injection in DuckDB SQL
        safe_path = str(path).replace("'", "''")
        safe_layer = str(layer).replace("'", "''")

        # Single read with WKB extraction (avoids double-reading the file)
        wkb_query = (
            f"SELECT *, ST_AsWKB(geom) as __wkb FROM "
            f"st_read('{safe_path}', layer='{safe_layer}')"
        )
        try:
            df = self.conn.execute(wkb_query).fetchdf()
            geom_col = _find_geom_column(df)
            geometries = [wkb.loads(bytes(b)) if b is not None else None for b in df["__wkb"]]
            drop_cols = ["__wkb"]
            if geom_col is not None:
                drop_cols.append(geom_col)
            df = df.drop(columns=drop_cols, errors="ignore")
            gdf = gpd.GeoDataFrame(df, geometry=geometries)
        except Exception as exc:
            log.warning("duckdb_load_gpkg_fallback", error=str(exc), path=str(path), layer=layer)
            gdf = gpd.read_file(str(path), layer=layer)

        log.debug("gpkg_loaded", path=str(path), layer=layer, features=len(gdf))
        return gdf

    def list_gpkg_layers(self, path: str | Path) -> list[str]:
        """List layer names in a GPKG file using pyogrio."""
        import pyogrio

        path = Path(path)
        info = pyogrio.list_layers(str(path))
        return [row[0] for row in info]

    def to_gpkg(
        self,
        gdf: gpd.GeoDataFrame,
        path: str | Path,
        layer: str = "result",
    ) -> Path:
        """Write a GeoDataFrame to a GPKG file.

        Uses geopandas for reliable GPKG output (DuckDB COPY TO GPKG
        has limited geometry type support).

        Args:
            gdf:   GeoDataFrame to write.
            path:  Output .gpkg file path.
            layer: Layer name in the output GPKG.

        Returns:
            Path to the written file.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_file(str(path), layer=layer, driver="GPKG")
        log.debug("gpkg_written", path=str(path), layer=layer, features=len(gdf))
        return path

    # -- SQL ---------------------------------------------------------------

    def register_gdf(self, name: str, gdf: gpd.GeoDataFrame) -> None:
        """Register a GeoDataFrame as a named table in the DuckDB session.

        DuckDB cannot natively handle Shapely geometry objects, so we
        serialise the geometry column to WKB bytes before registration.
        The resulting table contains a ``__wkb`` binary column in place
        of the geometry column, which DuckDB's spatial extension can
        then parse with ``ST_GeomFromWKB()``.
        """
        import pandas as pd
        from shapely import to_wkb

        geom_col = gdf.geometry.name if gdf.geometry is not None else "geometry"
        df = pd.DataFrame(gdf.drop(columns=[geom_col]))
        df["__wkb"] = to_wkb(gdf.geometry.values, include_srid=False)
        self.conn.register(name, df)
        log.debug("gdf_registered", table=name, features=len(gdf))

    def sql(self, query: str) -> gpd.GeoDataFrame:
        """Execute a SQL query and return result as GeoDataFrame."""
        df = self.conn.execute(query).fetchdf()
        geom_col = _find_geom_column(df)
        if geom_col is not None:
            if geom_col == "__wkb":
                # Direct WKB decode (raw bytes/bytearray from register_gdf)
                # Note: shapely requires bytes, not bytearray
                geometries = [
                    wkb.loads(bytes(b)) if b is not None else None
                    for b in df["__wkb"]
                ]
                df = df.drop(columns=["__wkb"], errors="ignore")
                return gpd.GeoDataFrame(df, geometry=geometries)

            wkb_query = query.rstrip().rstrip(";")
            wrapped = (
                f"SELECT *, ST_AsWKB({geom_col}) AS __wkb "
                f"FROM ({wkb_query}) AS __sq"
            )
            try:
                df2 = self.conn.execute(wrapped).fetchdf()
                geometries = [
                    wkb.loads(bytes(b)) if b is not None else None
                    for b in df2["__wkb"]
                ]
                df2 = df2.drop(columns=["__wkb", geom_col], errors="ignore")
                return gpd.GeoDataFrame(df2, geometry=geometries)
            except Exception as exc:
                log.warning("duckdb_sql_wkb_fallback", error=str(exc), query=query[:200])
                return gpd.GeoDataFrame(df)
        return gpd.GeoDataFrame(df)

    # ------------------------------------------------------------------
    # SpatialEngine interface
    # ------------------------------------------------------------------

    def load_layer(
        self, source: str, *, layer: str | None = None, schema: str = "public"
    ) -> gpd.GeoDataFrame:
        return self.load_gpkg(source, layer=layer)

    def write_layer(
        self,
        gdf: gpd.GeoDataFrame,
        target: str,
        *,
        layer: str = "result",
        schema: str = "public",
        if_exists: str = "replace",
    ) -> str:
        path = self.to_gpkg(gdf, target, layer=layer)
        return str(path)

    def list_layers(self, source: str | None = None, schema: str = "public") -> list[str]:
        if source is None:
            return []
        return self.list_gpkg_layers(source)

    def execute_sql(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
        import re

        if params:
            # Convert :key named params to DuckDB $key syntax,
            # but skip content inside single-quoted string literals
            def _replace_outside_strings(sql_text: str) -> str:
                parts = sql_text.split("'")
                for i in range(0, len(parts), 2):  # even indices = outside quotes
                    parts[i] = re.sub(r":([A-Za-z_]\w*)", r"$\1", parts[i])
                return "'".join(parts)

            sql = _replace_outside_strings(sql)
            df = self.conn.execute(sql, params).fetchdf()
        else:
            df = self.conn.execute(sql).fetchdf()
        return df.to_dict(orient="records")

    def sql_to_gdf(self, sql: str) -> gpd.GeoDataFrame:
        return self.sql(sql)

    def register(self, name: str, gdf: gpd.GeoDataFrame) -> None:
        self.register_gdf(name, gdf)

    @property
    def backend_name(self) -> str:
        return "duckdb"

    @property
    def is_persistent(self) -> bool:
        return False


def _find_geom_column(df: Any) -> str | None:
    """Find the geometry column name in a DataFrame.

    Checks well-known names first, then falls back to detecting
    binary/object columns that may contain WKB geometry data.
    """
    _KNOWN_NAMES = {"geom", "geometry", "wkb_geometry", "the_geom", "__wkb", "shape"}
    for col in df.columns:
        if col.lower() in _KNOWN_NAMES:
            return col
    # Fallback: look for binary columns that might be geometry
    for col in df.columns:
        if len(df) > 0:
            sample = df[col].iloc[0]
            if isinstance(sample, (bytes, bytearray)) and len(sample) > 4:
                return col
    return None
