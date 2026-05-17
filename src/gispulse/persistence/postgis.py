"""
PostGIS persistence engine for GISPulse.

Implements :class:`SpatialEngine` (Phase 3) so the rest of the codebase
can use PostGIS and DuckDB interchangeably.  Also retains the legacy
:meth:`read_layer` / :meth:`write_layer` / :meth:`execute` signatures
for backward compatibility.
"""

from __future__ import annotations

from typing import Any, Optional

import geopandas as gpd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from gispulse.core.logging import get_logger
from gispulse.persistence.engine import SpatialEngine

log = get_logger(__name__)


_TMP_PREFIX = "_gispulse_tmp_"


class PostGISConnection(SpatialEngine):
    """
    Connexion encapsulee vers une base PostGIS.

    Usage::

        conn = PostGISConnection(dsn="postgresql://user:pass@host:5432/db")
        gdf = conn.read_layer("public", "my_table")
        conn.write_layer(gdf, "public", "my_output", if_exists="replace")
        results = conn.execute("SELECT count(*) AS n FROM public.my_table")

    As a :class:`SpatialEngine`::

        with PostGISConnection(dsn=...) as engine:
            gdf = engine.load_layer("my_table", schema="public")
            engine.write_layer(gdf, "my_output", schema="public")

    Args:
        dsn:  SQLAlchemy DSN, e.g. ``postgresql+psycopg2://user:pass@host/db``.
              Plain ``postgresql://`` is also accepted and rewritten automatically.
    """

    def __init__(self, dsn: str) -> None:
        if dsn.startswith("postgresql://") and "+psycopg2" not in dsn:
            dsn = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
        self._dsn = dsn
        self.engine: Engine = create_engine(
            dsn,
            future=True,
            pool_size=20,
            max_overflow=30,
            pool_pre_ping=True,
        )
        self._registered_tables: list[str] = []

    # ------------------------------------------------------------------
    # SpatialEngine lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        # Engine is created in __init__; verify connectivity.
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        # Mask password in DSN for logging
        from sqlalchemy.engine.url import make_url
        try:
            safe_dsn = str(make_url(self._dsn).set(password="***"))
        except Exception:
            safe_dsn = self._dsn.split("@")[-1] if "@" in self._dsn else "***"
        log.debug("postgis_engine_opened", dsn=safe_dsn)

    def close(self) -> None:
        self._cleanup_registered_tables()
        self.engine.dispose()
        log.debug("postgis_engine_closed")

    def __enter__(self) -> PostGISConnection:
        self.open()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # SpatialEngine — Layer I/O
    # ------------------------------------------------------------------

    def load_layer(
        self, source: str, *, layer: str | None = None, schema: str = "public"
    ) -> gpd.GeoDataFrame:
        return self.read_layer(schema, source)

    def write_layer(  # type: ignore[override]
        self,
        gdf: gpd.GeoDataFrame,
        target: str,
        *,
        layer: str = "result",
        schema: str = "public",
        if_exists: str = "replace",
    ) -> str:
        self._write_postgis(gdf, schema, target, if_exists=if_exists)
        self._ensure_spatial_index(schema, target)
        ref = f"{schema}.{target}"
        log.debug("postgis_layer_written", ref=ref, features=len(gdf))
        return ref

    def _ensure_spatial_index(self, schema: str, table: str) -> None:
        """Create a GIST spatial index on the geometry column if not exists."""
        try:
            geom_col = self._detect_geom_column(schema, table)
            idx_name = f"idx_{table}_{geom_col}_gist"
            sql = (
                f'CREATE INDEX IF NOT EXISTS "{idx_name}" '
                f'ON "{schema}"."{table}" USING GIST ("{geom_col}")'
            )
            with self.engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
            log.debug("postgis_spatial_index_created", table=f"{schema}.{table}", index=idx_name)
        except Exception as exc:
            log.warning("postgis_spatial_index_failed", table=f"{schema}.{table}", error=str(exc))

    def list_layers(self, source: str | None = None, schema: str = "public") -> list[str]:
        sql = (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
        rows = self.execute(sql, {"schema": schema})
        return [r["table_name"] for r in rows]

    # ------------------------------------------------------------------
    # SpatialEngine — SQL
    # ------------------------------------------------------------------

    def execute_sql(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
        return self.execute(sql, params)

    def sql_to_gdf(
        self, sql: str, geom_col: Optional[str] = None
    ) -> gpd.GeoDataFrame:
        if geom_col is None:
            geom_col = self._detect_geom_column_from_query(sql)
        return gpd.read_postgis(sql, con=self.engine, geom_col=geom_col)

    def register(self, name: str, gdf: gpd.GeoDataFrame) -> None:
        # Sanitize name to prevent SQL injection in DROP TABLE
        import re
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
            raise ValueError(f"Invalid table name for register: {name!r}")
        tmp_name = f"{_TMP_PREFIX}{name}"
        self._write_postgis(gdf, "public", tmp_name, if_exists="replace")
        self._registered_tables.append(tmp_name)
        log.debug("postgis_gdf_registered", table=tmp_name, features=len(gdf))

    @property
    def backend_name(self) -> str:
        return "postgis"

    @property
    def is_persistent(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Legacy API (kept for backward compatibility)
    # ------------------------------------------------------------------

    def read_layer(self, schema: str, table: str) -> gpd.GeoDataFrame:
        geom_col = self._detect_geom_column(schema, table)
        sql = f'SELECT * FROM "{schema}"."{table}"'
        return gpd.read_postgis(sql, con=self.engine, geom_col=geom_col)

    def _write_postgis(
        self,
        gdf: gpd.GeoDataFrame,
        schema: str,
        table: str,
        if_exists: str = "replace",
    ) -> None:
        gdf.to_postgis(
            name=table,
            con=self.engine,
            schema=schema,
            if_exists=if_exists,
            index=False,
        )

    def execute(self, sql: str, params: Optional[dict[str, Any]] = None) -> list[dict]:
        params = params or {}
        with self.engine.connect() as conn:
            result = conn.execute(text(sql), params)
            return [dict(row) for row in result.mappings()]

    # ------------------------------------------------------------------
    # Geometry column detection (Fix #23)
    # ------------------------------------------------------------------

    def _detect_geom_column(self, schema: str, table: str) -> str:
        """Detect the geometry column name for a given table.

        Resolution order:
        1. PostGIS ``geometry_columns`` catalog
        2. ``information_schema.columns`` for geometry/geography types
        3. Fallback to ``"geom"`` (GISPulse convention)
        """
        # 1. PostGIS catalog
        try:
            rows = self.execute(
                "SELECT f_geometry_column FROM geometry_columns "
                "WHERE f_table_schema = :schema AND f_table_name = :table LIMIT 1",
                {"schema": schema, "table": table},
            )
            if rows:
                return rows[0]["f_geometry_column"]
        except Exception:
            log.debug("geom_detect_catalog_miss", schema=schema, table=table)

        # 2. information_schema fallback
        try:
            rows = self.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table "
                "AND udt_name IN ('geometry', 'geography') LIMIT 1",
                {"schema": schema, "table": table},
            )
            if rows:
                return rows[0]["column_name"]
        except Exception:
            log.debug("geom_detect_info_schema_miss", schema=schema, table=table)

        # 3. Convention fallback
        log.debug("geom_detect_fallback", schema=schema, table=table, col="geom")
        return "geom"

    def _detect_geom_column_from_query(self, sql: str) -> str:
        """Detect geometry column from an arbitrary SQL query via LIMIT 0 probe.

        Falls back to ``"geom"`` if detection fails.
        """
        probe_sql = f"SELECT * FROM ({sql}) AS _probe LIMIT 0"
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(probe_sql))
                for col_name, col_type in zip(
                    result.keys(), result.cursor.description
                ):
                    # psycopg2 type_code for geometry is not reliable,
                    # so match by conventional column names.
                    if col_name in ("geom", "geometry", "the_geom", "wkb_geometry", "geog"):
                        return col_name
        except Exception:
            log.debug("geom_detect_query_probe_failed")
        return "geom"

    # ------------------------------------------------------------------
    # Temporary table cleanup (Fix #24)
    # ------------------------------------------------------------------

    def _cleanup_registered_tables(self) -> None:
        """Drop all tables created by :meth:`register` during this session."""
        if not self._registered_tables:
            return
        try:
            with self.engine.connect() as conn:
                for table in self._registered_tables:
                    conn.execute(
                        text(f'DROP TABLE IF EXISTS "public"."{table}" CASCADE')
                    )
                conn.commit()
            log.debug(
                "postgis_tmp_tables_cleaned",
                count=len(self._registered_tables),
            )
        except Exception:
            log.warning(
                "postgis_tmp_tables_cleanup_failed",
                tables=self._registered_tables,
            )
        self._registered_tables.clear()

    def dispose(self) -> None:
        self.engine.dispose()
