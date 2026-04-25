"""
DuckDB-PostGIS hybrid bridge for GISPulse.

Combines DuckDB's analytical speed with PostGIS's persistence:
- Reads go through DuckDB via ``postgres_scanner`` (federated queries)
- Writes go directly to PostGIS (reliable, transactional)
- SQL can reference both ``pg.schema.table`` and local DuckDB tables

Implements :class:`SpatialEngine` as the ``"hybrid"`` backend.

Usage::

    from persistence.bridge import DuckDBPostGISBridge, HybridEngine

    bridge = DuckDBPostGISBridge(pg_dsn="postgresql://user:pass@host/db")
    with bridge.session() as session:
        gdf = bridge.query(session, "SELECT * FROM pg.public.parcels")

    engine = HybridEngine(pg_dsn="postgresql://user:pass@host/db")
    with engine:
        gdf = engine.load_layer("parcels", schema="public")
"""

from __future__ import annotations

from typing import Any

import duckdb
import geopandas as gpd
from shapely import wkb

from core.logging import get_logger
from persistence.engine import SpatialEngine

log = get_logger(__name__)


class DuckDBPostGISBridge:
    """DuckDB reads PostGIS tables via postgres_scanner extension.

    The bridge normalises the DSN (strips ``+psycopg2`` that SQLAlchemy
    requires but DuckDB rejects) and pre-configures the DuckDB session
    with a read-only ATTACH to the PostgreSQL database.
    """

    PG_ATTACH_NAME = "pg"

    def __init__(self, pg_dsn: str) -> None:
        # Normalize DSN: strip +psycopg2 suffix that SQLAlchemy uses
        self._pg_dsn = pg_dsn.replace("postgresql+psycopg2://", "postgresql://")

    def session(self) -> duckdb.DuckDBPyConnection:
        """Return a DuckDB connection with postgres_scanner pre-configured.

        The returned connection has:
        - ``spatial`` extension loaded
        - ``postgres_scanner`` extension loaded
        - PostgreSQL database ATTACHed as ``pg`` (READ_ONLY)

        Returns:
            Ready-to-use DuckDB connection.
        """
        conn = duckdb.connect(":memory:")

        # Load spatial extension
        try:
            conn.load_extension("spatial")
        except Exception:
            conn.install_extension("spatial")
            conn.load_extension("spatial")

        # Load postgres_scanner extension
        try:
            conn.load_extension("postgres_scanner")
        except Exception:
            conn.install_extension("postgres_scanner")
            conn.load_extension("postgres_scanner")

        # Attach PostGIS database as read-only federated source
        attach_sql = (
            f"ATTACH '{self._pg_dsn}' AS {self.PG_ATTACH_NAME} "
            f"(TYPE postgres, READ_ONLY)"
        )
        conn.execute(attach_sql)
        log.debug("duckdb_postgis_bridge_attached", dsn=self._pg_dsn[:40])

        return conn

    def query(
        self,
        session: duckdb.DuckDBPyConnection,
        sql: str,
        geom_col: str = "geom",
    ) -> gpd.GeoDataFrame:
        """Execute a federated query and return a GeoDataFrame.

        Wraps the query to convert the geometry column from PostGIS
        binary to WKB that Shapely can parse.

        Args:
            session:  DuckDB connection from :meth:`session`.
            sql:      SQL query (can reference ``pg.schema.table``).
            geom_col: Name of the geometry column to convert.

        Returns:
            GeoDataFrame with parsed geometries.
        """
        # Wrap query to extract geometry as WKB
        wrapped_sql = (
            f"SELECT *, ST_AsWKB({geom_col}) AS __wkb "
            f"FROM ({sql.rstrip().rstrip(';')}) AS __bridge_q"
        )

        try:
            df = session.execute(wrapped_sql).fetchdf()
            geometries = [
                wkb.loads(b) if b is not None else None
                for b in df["__wkb"]
            ]
            df = df.drop(columns=["__wkb", geom_col], errors="ignore")
            gdf = gpd.GeoDataFrame(df, geometry=geometries)
            log.debug("bridge_query_ok", features=len(gdf))
            return gdf
        except Exception:
            # Fallback: return without geometry parsing (non-spatial query)
            log.debug("bridge_query_fallback_no_geom", sql=sql[:80])
            df = session.execute(sql).fetchdf()
            return gpd.GeoDataFrame(df)


class HybridEngine(SpatialEngine):
    """Hybrid engine: DuckDB for reads/analytics, PostGIS for writes.

    This engine leverages DuckDB's ``postgres_scanner`` to read PostGIS
    tables with zero-copy efficiency, while writes go directly through
    PostGIS (via SQLAlchemy / geopandas ``to_postgis``) for transactional
    safety.

    SQL queries can reference both ``pg.public.table_name`` (PostGIS via
    federated scan) and locally registered DuckDB tables.

    Usage::

        engine = HybridEngine(pg_dsn="postgresql://user:pass@host/db")
        with engine:
            gdf = engine.load_layer("parcels", schema="public")
            engine.write_layer(gdf, "parcels_out", schema="public")
            result = engine.sql_to_gdf("SELECT * FROM pg.public.parcels WHERE area > 100")
    """

    def __init__(self, pg_dsn: str) -> None:
        self._pg_dsn = pg_dsn
        self._bridge = DuckDBPostGISBridge(pg_dsn)
        self._session: duckdb.DuckDBPyConnection | None = None
        self._postgis: Any = None  # Lazy PostGISConnection

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        self._session = self._bridge.session()
        # Lazy-import PostGISConnection for write path
        from persistence.postgis import PostGISConnection

        self._postgis = PostGISConnection(dsn=self._pg_dsn)
        self._postgis.open()
        log.debug("hybrid_engine_opened")

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
        if self._postgis is not None:
            self._postgis.close()
            self._postgis = None
        log.debug("hybrid_engine_closed")

    def __enter__(self) -> HybridEngine:
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def _conn(self) -> duckdb.DuckDBPyConnection:
        if self._session is None:
            raise RuntimeError(
                "HybridEngine is not open. Call open() or use as context manager."
            )
        return self._session

    # ------------------------------------------------------------------
    # Layer I/O
    # ------------------------------------------------------------------

    def load_layer(
        self, source: str, *, layer: str | None = None, schema: str = "public"
    ) -> gpd.GeoDataFrame:
        """Read a PostGIS table via DuckDB/postgres_scanner.

        Args:
            source: Table name in PostGIS.
            layer:  Ignored (PostGIS tables are flat).
            schema: Database schema (default ``"public"``).

        Returns:
            GeoDataFrame read through the DuckDB federated bridge.
        """
        # Validate identifiers to prevent SQL injection
        import re
        _ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,126}$")
        if not _ident_re.match(source):
            raise ValueError(f"Unsafe table name: {source!r}")
        if not _ident_re.match(schema):
            raise ValueError(f"Unsafe schema name: {schema!r}")
        qualified = f"{DuckDBPostGISBridge.PG_ATTACH_NAME}.{schema}.{source}"
        sql = f"SELECT * FROM {qualified}"
        return self._bridge.query(self._conn, sql)

    def write_layer(
        self,
        gdf: gpd.GeoDataFrame,
        target: str,
        *,
        layer: str = "result",
        schema: str = "public",
        if_exists: str = "replace",
    ) -> str:
        """Write a GeoDataFrame directly to PostGIS.

        Uses the PostGIS connection (not DuckDB) for reliable,
        transactional writes.

        Returns:
            Reference string ``"schema.table"``.
        """
        return self._postgis.write_layer(
            gdf, target, layer=layer, schema=schema, if_exists=if_exists
        )

    def list_layers(
        self, source: str | None = None, schema: str = "public"
    ) -> list[str]:
        """List tables in the PostGIS schema via DuckDB catalog query."""
        try:
            # Validate schema identifier to prevent SQL injection
            import re
            _ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,126}$")
            if not _ident_re.match(schema):
                raise ValueError(f"Unsafe schema name: {schema!r}")
            sql = (
                f"SELECT table_name FROM "
                f"{DuckDBPostGISBridge.PG_ATTACH_NAME}.information_schema.tables "
                f"WHERE table_schema = ? "
                f"ORDER BY table_name"
            )
            df = self._conn.execute(sql, [schema]).fetchdf()
            return df["table_name"].tolist()
        except Exception:
            # Fallback to PostGIS direct query
            return self._postgis.list_layers(source=source, schema=schema)

    # ------------------------------------------------------------------
    # SQL
    # ------------------------------------------------------------------

    def execute_sql(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[dict]:
        """Execute SQL in DuckDB (can reference pg.schema.table)."""
        if params:
            import re
            sql = re.sub(r":([A-Za-z_]\w*)", r"$\1", sql)
            df = self._conn.execute(sql, params).fetchdf()
        else:
            df = self._conn.execute(sql).fetchdf()
        return df.to_dict(orient="records")

    def sql_to_gdf(self, sql: str) -> gpd.GeoDataFrame:
        """Execute a spatial SQL query in DuckDB and return a GeoDataFrame.

        The query can reference both ``pg.schema.table`` (federated PostGIS
        tables) and locally registered DuckDB tables.
        """
        return self._bridge.query(self._conn, sql)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, gdf: gpd.GeoDataFrame) -> None:
        """Register a GeoDataFrame as a local DuckDB table.

        Once registered, the table can be referenced in SQL alongside
        ``pg.schema.table`` federated tables.
        """
        self._conn.register(name, gdf)
        log.debug("hybrid_gdf_registered", table=name, features=len(gdf))

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def backend_name(self) -> str:
        return "hybrid"

    @property
    def is_persistent(self) -> bool:
        return True
