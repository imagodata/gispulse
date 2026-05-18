"""
GeoPackageEngine — SpatialEngine backed by a single .gpkg file.

One file = complete project: spatial layers + metadata + rules + triggers +
change history.  Compatible with QGIS, GDAL, any OGC GeoPackage reader.

Architecture:
- Geometry stored as standard GeoPackage Binary (GPB) — no mod_spatialite
- Spatial queries via RTree prefilter + Shapely refinement
- Internal tables prefixed ``_gispulse_`` registered in ``gpkg_extensions``
- WAL mode for concurrent read access (QGIS + GISPulse simultaneously)
- Optional DuckDB acceleration for heavy analytics (if available)

Connection strategy:
    pyogrio/GDAL opens its own SQLite connection internally when writing
    spatial layers.  To avoid WAL/journal conflicts, we close our connection
    before any pyogrio write and reopen it afterward.  For metadata-only
    operations (repository CRUD, kv_set, etc.) we keep the connection open.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import geopandas as gpd
import pyogrio
from shapely.geometry.base import BaseGeometry

from gispulse.persistence.engine import SpatialEngine
from gispulse.persistence.gpkg_schema import (
    INTERNAL_TABLES,
    bootstrap_gpkg_project,
    install_change_tracking,
    uninstall_change_tracking,
)
from gispulse.persistence.gpkg_spatial import (
    SpatialPredicate,
    spatial_query as _spatial_query,
    bbox_filter_gdf,
)
from gispulse.persistence.sql_guardrails import SecurityError, enforce as _enforce_guardrails

logger = logging.getLogger(__name__)
exec_logger = logging.getLogger("gispulse.engine.exec")

# Match psycopg-style %s placeholders ONLY when not inside a string. We
# do a coarse pre-scan for %s — when none is present we skip the
# rewrite entirely. The translation itself walks the string char by
# char to skip quoted regions (so '%s' inside a literal stays intact).
_PSYCOPG_PLACEHOLDER_RE = re.compile(r"%s")

# DuckDB is an optional accelerator
try:
    import duckdb

    _DUCKDB_AVAILABLE = True
except ImportError:
    _DUCKDB_AVAILABLE = False


class GeoPackageEngine(SpatialEngine):
    """GPKG-backed spatial engine — one file = complete project.

    Usage::

        engine = GeoPackageEngine("project.gpkg")
        with engine:
            gdf = engine.load_layer("parcelles")
            engine.write_layer(result, layer="buffer_result")
            layers = engine.list_layers()

    The engine manages:
    - Spatial layers (standard GPKG, visible in QGIS)
    - Internal metadata tables (invisible to QGIS)
    - Change tracking via SQLite triggers
    - Spatial queries via RTree + Shapely
    """

    def __init__(
        self,
        path: str | Path,
        *,
        use_duckdb_accel: bool = True,
    ) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._registered: dict[str, gpd.GeoDataFrame] = {}
        self._use_duckdb_accel = use_duckdb_accel and _DUCKDB_AVAILABLE
        self._duckdb_conn: Any = None
        self._opened = False

    @property
    def path(self) -> Path:
        """Absolute path to the GPKG file."""
        return self._path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the GPKG file (creates it if absent) and bootstrap schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._open_conn()

        # Bootstrap internal tables (idempotent)
        bootstrap_gpkg_project(self._conn)

        # Optional DuckDB accelerator
        if self._use_duckdb_accel:
            try:
                self._duckdb_conn = duckdb.connect(":memory:")
                self._duckdb_conn.install_extension("spatial")
                self._duckdb_conn.load_extension("spatial")
            except Exception as exc:
                logger.warning("duckdb_accel_unavailable: %s", exc)
                self._duckdb_conn = None
                self._use_duckdb_accel = False

        self._opened = True
        logger.info("gpkg_engine_opened: %s", self._path)

    def close(self) -> None:
        """Release all resources."""
        if self._duckdb_conn is not None:
            try:
                self._duckdb_conn.close()
            except Exception:
                pass
            self._duckdb_conn = None

        self._close_conn()
        self._registered.clear()
        self._opened = False
        logger.info("gpkg_engine_closed: %s", self._path)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _open_conn(self) -> sqlite3.Connection:
        """Open (or reopen) the SQLite connection with proper pragmas."""
        if self._conn is not None:
            return self._conn
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA cache_size=-64000")  # 64 MB
        return self._conn

    def _close_conn(self) -> None:
        """Close the SQLite connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get the current connection, reopening if needed."""
        if self._conn is None:
            if not self._opened:
                raise RuntimeError(
                    "GeoPackageEngine is not open. Call .open() first."
                )
            return self._open_conn()
        return self._conn

    # ------------------------------------------------------------------
    # Layer I/O
    # ------------------------------------------------------------------

    def load_layer(
        self,
        source: str | None = None,
        *,
        layer: str | None = None,
        schema: str = "public",
        bbox: tuple[float, float, float, float] | None = None,
        max_rows: int | None = None,
    ) -> gpd.GeoDataFrame:
        """Read a spatial layer from the GPKG into a GeoDataFrame.

        Args:
            source: Ignored (reads from the engine's own GPKG). Pass a
                    layer name here as shortcut if *layer* is None.
            layer:  Layer name within the GPKG.
            schema: Ignored.
            bbox:   Optional bounding box filter (uses pyogrio RTree).
            max_rows: Limit number of rows read.
        """
        layer_name = layer or source
        if layer_name is None:
            layers = self.list_layers()
            if not layers:
                raise ValueError(f"No spatial layers in {self._path}")
            layer_name = layers[0]

        # Check registered in-memory tables first
        if layer_name in self._registered:
            gdf = self._registered[layer_name]
            if max_rows:
                gdf = gdf.head(max_rows)
            return gdf.copy()

        read_kwargs: dict[str, Any] = {"layer": layer_name}
        if bbox is not None:
            read_kwargs["bbox"] = bbox
        if max_rows is not None:
            read_kwargs["max_features"] = max_rows

        return gpd.read_file(str(self._path), **read_kwargs)

    def write_layer(
        self,
        gdf: gpd.GeoDataFrame,
        target: str | None = None,
        *,
        layer: str = "result",
        schema: str = "public",
        if_exists: str = "replace",
    ) -> str:
        """Write a GeoDataFrame as a spatial layer in the GPKG.

        Closes the internal SQLite connection before writing (pyogrio/GDAL
        opens its own) and reopens it afterward.
        """
        if not self._path.exists():
            # New file — close our conn so pyogrio creates a clean GPKG
            self._close_conn()
            gdf.to_file(str(self._path), layer=layer, driver="GPKG", mode="w")
            # Reopen and re-bootstrap our internal tables
            self._open_conn()
            bootstrap_gpkg_project(self._conn)
        elif if_exists == "append":
            self._close_conn()
            gdf.to_file(str(self._path), layer=layer, driver="GPKG", mode="a")
            self._open_conn()
        else:
            # Replace: drop existing layer first
            existing = self.list_layers()
            if layer in existing:
                conn = self._get_conn()
                with self._lock:
                    conn.execute(f'DROP TABLE IF EXISTS "{layer}"')
                    conn.execute(
                        "DELETE FROM gpkg_contents WHERE table_name = ?",
                        (layer,),
                    )
                    conn.execute(
                        "DELETE FROM gpkg_geometry_columns WHERE table_name = ?",
                        (layer,),
                    )
                    # Clean up RTree artifacts
                    for suffix in ("", "_node", "_parent", "_rowid"):
                        conn.execute(
                            f'DROP TABLE IF EXISTS "rtree_{layer}_geom{suffix}"'
                        )
                    conn.commit()
            self._close_conn()
            gdf.to_file(str(self._path), layer=layer, driver="GPKG", mode="a")
            self._open_conn()

        logger.info("gpkg_layer_written: %s → %s", layer, self._path)
        return layer

    def list_layers(self, source: str | None = None, schema: str = "public") -> list[str]:
        """List spatial layers (excludes _gispulse_* and layer_styles)."""
        if not self._path.exists():
            return list(self._registered.keys())

        try:
            info = pyogrio.list_layers(str(self._path))
            spatial_layers = [
                name for name, _ in info
                if not name.startswith("_gispulse_")
                and name != "layer_styles"
            ]
        except Exception:
            spatial_layers = []

        for name in self._registered:
            if name not in spatial_layers:
                spatial_layers.append(name)

        return spatial_layers

    # ------------------------------------------------------------------
    # SQL execution
    # ------------------------------------------------------------------

    def execute_sql(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Execute raw SQL against the GPKG's SQLite database.

        For attribute queries and aggregations.  Spatial SQL functions are
        NOT available (no mod_spatialite).  Use :meth:`spatial_query` instead.
        """
        conn = self._get_conn()
        with self._lock:
            if params:
                cur = conn.execute(sql, params)
            else:
                cur = conn.execute(sql)
            rows = cur.fetchall()
            conn.commit()
            return [dict(row) for row in rows]

    @staticmethod
    def _translate_placeholders(sql: str) -> str:
        """Translate ``%s`` placeholders to SQLite ``?`` placeholders.

        The ESB :class:`ActionDispatcher` builds SQL with psycopg-style
        ``%s`` placeholders (the API server runs against PostGIS). For
        the GPKG path we rewrite to ``?`` so :mod:`sqlite3` accepts the
        bound parameters.

        We walk char by char to skip ``%s`` inside string literals
        (``'... %s ...'``) and quoted identifiers (``"%s"``). A regex
        sub would also rewrite those, which would silently corrupt
        legitimate literals containing the substring.
        """
        if "%s" not in sql:
            return sql

        out: list[str] = []
        in_single = False
        in_double = False
        i = 0
        n = len(sql)
        while i < n:
            ch = sql[i]
            nxt = sql[i + 1] if i + 1 < n else ""
            if in_single:
                # Handle SQL '' escape inside a single-quoted literal.
                if ch == "'" and nxt == "'":
                    out.append("''")
                    i += 2
                    continue
                if ch == "'":
                    in_single = False
                out.append(ch)
                i += 1
                continue
            if in_double:
                if ch == '"' and nxt == '"':
                    out.append('""')
                    i += 2
                    continue
                if ch == '"':
                    in_double = False
                out.append(ch)
                i += 1
                continue
            if ch == "'":
                in_single = True
                out.append(ch)
                i += 1
                continue
            if ch == '"':
                in_double = True
                out.append(ch)
                i += 1
                continue
            if ch == "%" and nxt == "s":
                out.append("?")
                i += 2
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    def execute(
        self,
        sql: str,
        params: Mapping[str, Any] | Sequence[Any] | None = None,
        *,
        allow_ddl: bool = False,
    ) -> int:
        """Execute a single DML statement against the GPKG, with guardrails.

        This is the engine's write path used by the trigger runtime
        (``set_field`` / ``run_sql`` actions, both via CLI and HTTP).
        It enforces a strict SQL whitelist before any statement reaches
        SQLite — see :mod:`persistence.sql_guardrails` for the policy.

        Behaviour:

        * Allowed by default: ``INSERT``, ``UPDATE``, ``DELETE``, ``SELECT``
        * Hard-blocked: ``ATTACH``, ``DETACH``, ``PRAGMA``, ``VACUUM``,
          ``BEGIN``/``COMMIT`` (transaction is owned by ``execute()``).
        * DDL (``CREATE`` / ``DROP`` / ``ALTER``) refused unless
          ``allow_ddl=True`` (internal migration use only).
        * Writes to ``gpkg_*``, ``rtree_*``, ``sqlite_*`` and
          ``_gispulse_*`` tables raise :class:`SecurityError`.
        * Multiple statements (``;`` between two real statements) are
          rejected as a chained-SQL injection attempt.

        Placeholder style: psycopg ``%s`` placeholders are translated
        to SQLite ``?`` placeholders so the same dispatcher code works
        across PostGIS and GPKG backends.

        Transaction: each call is wrapped in ``BEGIN IMMEDIATE`` /
        ``COMMIT``. On any exception we ``ROLLBACK``. We do **not**
        retry — that is the job of
        :class:`gispulse.runtime.sqlite_retry.RetryingSqlExecutor`,
        which sits one layer up.

        Logging: every call emits a DEBUG record with the parsed
        statement type, parameter count, rowcount, and duration on the
        ``gispulse.engine.exec`` logger. Guardrail violations log at
        WARNING with the leading keyword (parameters are never logged
        — they may contain PII).

        Args:
            sql:        A single SQL statement.
            params:     Bound parameters (sequence for ``?`` / ``%s``,
                        mapping for ``:name``). ``None`` means no
                        binding.
            allow_ddl:  Internal flag — set to True only by the engine
                        itself (migrations / bootstrap). YAML actions
                        must never be able to flip this.

        Returns:
            ``cursor.rowcount`` — the number of rows affected (``-1``
            for a SELECT under SQLite).

        Raises:
            SecurityError:    Guardrail violation (logged at WARNING).
            sqlite3.OperationalError: Real SQL error (no such table,
                                      busy lock, syntax). The retry
                                      wrapper handles ``BUSY`` itself;
                                      everything else propagates.
            RuntimeError: When the engine is not open.
        """
        if not self._opened:
            raise RuntimeError(
                "GeoPackageEngine is not open. Call .open() first."
            )

        try:
            parsed = _enforce_guardrails(sql, allow_ddl=allow_ddl)
        except SecurityError as exc:
            # Log the leading keyword + reason; never log raw SQL params.
            exec_logger.warning(
                "engine_execute_blocked sql_template=%r reason=%s",
                sql[:120],
                exc,
            )
            raise

        translated = self._translate_placeholders(sql)

        # Normalise params to a tuple/list for sqlite3 (it accepts both
        # sequence and mapping; we keep mappings for ``:name`` style).
        bound: Any
        if params is None:
            bound = ()
        elif isinstance(params, Mapping):
            bound = dict(params)
        else:
            bound = list(params)

        param_count = len(bound) if bound else 0
        conn = self._get_conn()
        start = time.perf_counter()
        with self._lock:
            try:
                # BEGIN IMMEDIATE acquires a RESERVED lock right away so
                # we surface contention as SQLITE_BUSY now instead of at
                # COMMIT time (and the retry wrapper can do its job).
                # We avoid double-BEGIN if the connection already has a
                # transaction open (sqlite3's autocommit semantics).
                in_transaction = conn.in_transaction
                if not in_transaction:
                    conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(translated, bound)
                rowcount = cur.rowcount
                if not in_transaction:
                    conn.commit()
            except BaseException:
                # ROLLBACK is best-effort — under SQLITE_BUSY it might
                # also raise, and we want the original exception.
                try:
                    if conn.in_transaction:
                        conn.rollback()
                except sqlite3.Error:
                    pass
                raise
        duration_ms = (time.perf_counter() - start) * 1000.0
        exec_logger.debug(
            "engine_execute statement=%s params=%d rowcount=%d duration_ms=%.2f",
            parsed.statement_type,
            param_count,
            rowcount,
            duration_ms,
        )
        return int(rowcount)

    def sql_to_gdf(self, sql: str) -> gpd.GeoDataFrame:
        """Execute SQL and return a GeoDataFrame.

        Delegates to DuckDB if available (supports spatial functions),
        otherwise falls back to attribute-only SQL.
        """
        if self._duckdb_conn is not None:
            return self._duckdb_sql_to_gdf(sql)
        return self._python_sql_to_gdf(sql)

    def _duckdb_sql_to_gdf(self, sql: str) -> gpd.GeoDataFrame:
        """Execute spatial SQL via DuckDB (reads GPKG natively)."""
        rewritten = self._rewrite_sql_for_duckdb(sql)
        result = self._duckdb_conn.execute(rewritten).fetchdf()

        geom_cols = [c for c in result.columns if c.lower() in ("geom", "geometry")]
        if geom_cols:
            from shapely import wkb
            geom_col = geom_cols[0]
            result[geom_col] = result[geom_col].apply(
                lambda g: wkb.loads(g) if g is not None else None
            )
            return gpd.GeoDataFrame(result, geometry=geom_col)
        return gpd.GeoDataFrame(result)

    def _rewrite_sql_for_duckdb(self, sql: str) -> str:
        """Rewrite FROM <table> to FROM st_read('<path>', layer='<table>')."""
        layers = self.list_layers()
        path_str = str(self._path).replace("'", "''")
        for layer_name in sorted(layers, key=len, reverse=True):
            pattern = rf'\bFROM\s+["\']?{re.escape(layer_name)}["\']?'
            replacement = f"FROM st_read('{path_str}', layer='{layer_name}')"
            sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)
        return sql

    def _python_sql_to_gdf(self, sql: str) -> gpd.GeoDataFrame:
        """Fallback: execute attribute SQL, return as GeoDataFrame."""
        rows = self.execute_sql(sql)
        if not rows:
            return gpd.GeoDataFrame()
        import pandas as pd
        return gpd.GeoDataFrame(pd.DataFrame(rows))

    # ------------------------------------------------------------------
    # Registration (in-memory tables)
    # ------------------------------------------------------------------

    def register(self, name: str, gdf: gpd.GeoDataFrame) -> None:
        """Register a GeoDataFrame as a named in-memory table."""
        self._registered[name] = gdf
        logger.info("gpkg_registered: %s (%d features)", name, len(gdf))

    def persist(self, name: str, *, if_exists: str = "replace") -> str:
        """Persist a registered in-memory table as a GPKG spatial layer."""
        if name not in self._registered:
            raise KeyError(f"No registered table named {name!r}")
        gdf = self._registered[name]
        ref = self.write_layer(gdf, layer=name, if_exists=if_exists)
        del self._registered[name]
        return ref

    def persist_all(self) -> list[str]:
        """Persist all registered in-memory tables to the GPKG."""
        return [self.persist(n) for n in list(self._registered.keys())]

    # ------------------------------------------------------------------
    # Spatial queries (RTree + Shapely)
    # ------------------------------------------------------------------

    def spatial_query(
        self,
        layer_name: str,
        query_geom: BaseGeometry,
        predicate: SpatialPredicate = "intersects",
    ) -> gpd.GeoDataFrame:
        """Two-phase spatial query: RTree prefilter + Shapely refinement."""
        gdf = self.load_layer(layer=layer_name)
        conn = self._get_conn()
        return _spatial_query(conn, gdf, layer_name, query_geom, predicate)

    def bbox_filter(
        self,
        layer_name: str,
        bbox: tuple[float, float, float, float],
    ) -> gpd.GeoDataFrame:
        """Fast bounding-box filter using RTree index."""
        gdf = self.load_layer(layer=layer_name)
        conn = self._get_conn()
        return bbox_filter_gdf(conn, gdf, layer_name, bbox)

    # ------------------------------------------------------------------
    # Change tracking
    # ------------------------------------------------------------------

    def enable_change_tracking(self, layer_name: str, pk_col: str = "fid") -> None:
        """Install INSERT/UPDATE/DELETE triggers on a spatial layer."""
        conn = self._get_conn()
        install_change_tracking(conn, layer_name, pk_col)

    def disable_change_tracking(self, layer_name: str) -> None:
        """Remove change tracking triggers for a spatial layer."""
        conn = self._get_conn()
        uninstall_change_tracking(conn, layer_name)

    def get_pending_changes(self, limit: int = 100) -> list[dict]:
        """Read unprocessed change log entries.

        Lot 2 v2 (Beta E2E multi-GPKG fix): we ``commit()`` BEFORE the SELECT
        to discard any stale read-transaction snapshot the connection may
        still hold. Under WAL with ``check_same_thread=False`` and a
        long-lived connection shared by the polling daemon, SQLite can
        keep a reader pinned to an older WAL frame even after another
        connection (raw ``sqlite3.connect`` from outside) has committed
        new rows to ``_gispulse_change_log``. The no-op commit forces the
        next ``execute`` to start a fresh read transaction, guaranteeing
        we see the latest committed state. Cheap (no pending writes ⇒
        end-of-tx fast path) and idempotent.
        """
        conn = self._get_conn()
        with self._lock:
            try:
                # Discard any cached read snapshot. With no in-flight write
                # this is a no-op for SQLite but resets the wal-index view.
                conn.commit()
            except sqlite3.Error:
                # Defensive — never let a commit error block polling.
                pass
            rows = conn.execute(
                "SELECT * FROM _gispulse_change_log "
                "WHERE processed = 0 ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_changes_processed(self, up_to_id: int) -> int:
        """Mark change log entries as processed."""
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "UPDATE _gispulse_change_log SET processed = 1 WHERE id <= ?",
                (up_to_id,),
            )
            conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------
    # Key-value store (engine state)
    # ------------------------------------------------------------------

    def kv_get(self, key: str) -> str | None:
        """Get a value from the internal key-value store."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM _gispulse_kv WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        """Set a value in the internal key-value store."""
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO _gispulse_kv (key, value, updated_at) "
                "VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')",
                (key, value, value),
            )
            conn.commit()

    def kv_delete(self, key: str) -> bool:
        """Delete a key from the internal key-value store."""
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute("DELETE FROM _gispulse_kv WHERE key = ?", (key,))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def backend_name(self) -> str:
        return "gpkg"

    @property
    def is_persistent(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def info(self) -> dict[str, Any]:
        """Return a summary of the GPKG project."""
        layers = self.list_layers()
        internal = []
        conn = self._get_conn()
        for tbl in INTERNAL_TABLES:
            try:
                row = conn.execute(f"SELECT COUNT(*) as c FROM {tbl}").fetchone()
                internal.append({"table": tbl, "rows": row["c"] if row else 0})
            except sqlite3.OperationalError:
                pass

        return {
            "path": str(self._path),
            "size_mb": round(self._path.stat().st_size / 1024 / 1024, 2)
            if self._path.exists()
            else 0,
            "spatial_layers": layers,
            "layer_count": len(layers),
            "internal_tables": internal,
            "registered": list(self._registered.keys()),
            "duckdb_accel": self._duckdb_conn is not None,
            "backend": self.backend_name,
        }
