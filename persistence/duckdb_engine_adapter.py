"""
DuckDBSpatialEngine — change-log adapter on top of :class:`DuckDBSession`.

Lot 3 of the live-sync stack. The GPKG backend (Lot 2 v2) uses native
SQLite triggers writing to ``_gispulse_change_log``; the
:class:`ChangeLogWatcher` polls that table and broadcasts ``dml.changed``
events. DuckDB has **no native triggers**, so we emulate the same
contract at application level via :class:`DuckDBChangeDetector` (DML
proxy + ``_change_log`` table + polling thread).

This adapter glues the existing :class:`DuckDBChangeDetector` into the
GPKG-shaped surface that :class:`WatcherRegistry` and
:class:`ChangeLogWatcher` already consume::

    enable_change_tracking(layer)   → no-op (detection is global)
    disable_change_tracking(layer)  → no-op
    get_pending_changes(limit)      → SELECT FROM _change_log
    mark_changes_processed(id)      → UPDATE _change_log SET processed=1

Architecture (composition via inheritance)
------------------------------------------

::

    DuckDBSpatialEngine (this class)
        ├── DuckDBSession (base, owns ``self._conn``)
        └── DuckDBChangeDetector (owned, attached on ``open()``)

Why inheritance over wrapping? Every :class:`SpatialEngine` method
(load_layer, write_layer, execute_sql, …) is a pass-through to
:class:`DuckDBSession`. Subclassing keeps identity (``isinstance(engine,
DuckDBSession) is True``) and avoids 12 trivial delegate stubs.

Limitations
-----------

DuckDB backend live-sync limitations:

- Change detection is **application-level** (no native DB triggers).
- Only INSERTs/UPDATEs/DELETEs that go through GISPulse's HTTP API,
  CLI, or pipeline runtime (anything wired to this engine instance)
  are captured. Direct ``duckdb.connect()`` from external tools
  bypasses the detector — those writes will not appear on
  ``/ws/events``.
- For full external-write capture, use the ``gpkg`` backend (native
  SQLite triggers) or PostGIS Pro (``pg_notify``).
- When ``database=":memory:"`` (default for the lifespan), the
  ``_change_log`` is lost on engine close. Configure a persistent
  DuckDB path via ``GISPULSE_DUCKDB_PATH`` / ``database.duckdb_path``
  in ``gispulse.toml`` to survive restarts.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from persistence.duckdb_change_detector import DuckDBChangeDetector
from persistence.duckdb_engine import DuckDBSession

logger = logging.getLogger(__name__)


class DuckDBSpatialEngine(DuckDBSession):
    """:class:`DuckDBSession` augmented with a GPKG-shaped change-log API.

    Drop-in replacement for :class:`DuckDBSession` consumed by
    :class:`WatcherRegistry` / :class:`ChangeLogWatcher`. All
    :class:`SpatialEngine` behaviours are inherited unchanged; the
    adapter only adds:

    - :attr:`path`                          — disk identity (or
      ``":memory:"`` literal) used by the registry's idempotency check.
    - :meth:`enable_change_tracking`        — validates layer presence,
      otherwise no-op (detection is global per connection).
    - :meth:`disable_change_tracking`       — no-op (kept for symmetry).
    - :meth:`get_pending_changes`           — pulls unprocessed rows
      from :class:`DuckDBChangeDetector`'s ``_change_log``.
    - :meth:`mark_changes_processed`        — flips the ``processed``
      flag on rows up to a given id.
    - :meth:`execute`                       — proxy that routes
      DML through the detector so the change is logged.

    The :class:`DuckDBChangeDetector` is created lazily on
    :meth:`open` and torn down on :meth:`close`. Detector polling is
    **not started** here — the :class:`ChangeLogWatcher` does the
    polling externally via :meth:`get_pending_changes`. Starting the
    detector's own thread would create a second poller racing on the
    same change_log.
    """

    def __init__(self, database: str = ":memory:") -> None:
        super().__init__(database=database)
        self._detector: DuckDBChangeDetector | None = None
        # DuckDB's :class:`DuckDBPyConnection` is not thread-safe for
        # concurrent statements without explicit cursors. The Lot 3
        # watcher polls from a daemon thread while pipeline code may
        # call :meth:`execute` from the main thread; serialise both
        # behind a single lock to avoid corrupting the connection state.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the underlying DuckDB session and bootstrap the change log.

        Idempotent: re-opening a closed engine recreates the detector;
        re-opening an already-open one is a no-op.
        """
        if self._conn is not None:
            return
        super().open()
        # Spawn the change detector — it creates ``_change_log`` and
        # ``_change_log_seq`` on the underlying connection, idempotent.
        self._detector = DuckDBChangeDetector(self.conn)
        logger.info("duckdb_spatial_engine_opened database=%s", self.database)

    def close(self) -> None:
        """Tear down the detector and close the underlying session."""
        # Detector references the conn — drop the reference before the
        # connection is closed so any pending poll thread (if a caller
        # started one externally) doesn't grab a dead handle.
        self._detector = None
        super().close()

    # ------------------------------------------------------------------
    # Identity (used by WatcherRegistry.register)
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """Filesystem path of the DuckDB database (or ``:memory:`` sentinel).

        :class:`WatcherRegistry` compares this against the gpkg_path
        passed to :meth:`register` to detect double-registration with a
        different file. For ``:memory:`` we still return a Path-like so
        equality semantics match.
        """
        return Path(self.database)

    # ------------------------------------------------------------------
    # Change-log surface (mirrors GeoPackageEngine)
    # ------------------------------------------------------------------

    def enable_change_tracking(self, layer_name: str, pk_col: str = "fid") -> None:
        """Mark a layer as tracked.

        DuckDB detection is **global** (every DML routed through
        :meth:`execute` is captured), so this is a logical no-op. We
        still validate that the layer exists in the current DuckDB
        catalog and emit a warning otherwise — the GPKG path does the
        same so callers get a consistent signal when they reference a
        non-existent layer.
        """
        if self._detector is None:
            raise RuntimeError(
                "DuckDBSpatialEngine is not open. Call .open() first."
            )
        try:
            rows = self.conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = ? LIMIT 1",
                [layer_name],
            ).fetchall()
        except Exception as exc:
            # ``information_schema`` should always exist; if it doesn't,
            # we can't validate and log instead of failing — emulating
            # the GPKG behaviour for missing-layer (warning, not raise).
            logger.warning(
                "duckdb_enable_change_tracking_validate_failed "
                "layer=%s err=%s",
                layer_name,
                exc,
            )
            return
        if not rows:
            logger.warning(
                "duckdb_enable_change_tracking_unknown_layer layer=%s "
                "(detection is global; tracking has no per-layer effect)",
                layer_name,
            )
            return
        logger.debug(
            "duckdb_enable_change_tracking_noop layer=%s "
            "(detection is global)",
            layer_name,
        )

    def disable_change_tracking(self, layer_name: str) -> None:
        """No-op: DuckDB detection cannot be turned off per layer.

        Kept for interface symmetry with :class:`GeoPackageEngine`.
        """
        logger.debug(
            "duckdb_disable_change_tracking_noop layer=%s "
            "(detection is global)",
            layer_name,
        )

    def get_pending_changes(self, limit: int = 100) -> list[dict]:
        """Read unprocessed rows from ``_change_log``.

        Returns dicts with the same shape as
        :meth:`GeoPackageEngine.get_pending_changes` so the watcher
        consumes them transparently:
        ``{"id", "table_name", "operation", "row_pk", "changed_at",
        "processed"}``.

        Thread-safety: the watcher polls this method from a daemon
        thread while the main thread (or HTTP workers) call
        :meth:`execute`. ``DuckDBPyConnection`` is *not* thread-safe for
        concurrent ``execute`` calls even when serialised behind a lock —
        DuckDB binds the result set / arrow stream to the connection
        object and a second ``execute`` on the same conn from another
        thread silently discards or corrupts the prior result.

        We therefore call ``cursor()`` here so the read runs on a
        thread-local cursor that shares the underlying database state
        but has its own result handle. The lock is still held to
        serialise concurrent cursor creation under the same conn.
        """
        if self._detector is None:
            raise RuntimeError(
                "DuckDBSpatialEngine is not open. Call .open() first."
            )
        with self._lock:
            cur = self.conn.cursor()
            rows = cur.execute(
                "SELECT id, table_name, operation, row_pk, changed_at, processed "
                "FROM _change_log WHERE processed = 0 ORDER BY id LIMIT ?",
                [int(limit)],
            ).fetchall()
        return [
            {
                "id": int(r[0]),
                "table_name": r[1],
                "operation": r[2],
                "row_pk": r[3],
                # DuckDB returns TIMESTAMP as datetime.datetime; SQLite/GPKG
                # returns TEXT. Normalize to ISO-8601 string so the broadcast
                # payload (json.dumps) doesn't trip on a non-serializable type.
                "changed_at": r[4].isoformat(sep=" ") if hasattr(r[4], "isoformat") else r[4],
                "processed": int(r[5]) if r[5] is not None else 0,
            }
            for r in rows
        ]

    def mark_changes_processed(self, up_to_id: int) -> int:
        """Flip ``processed=1`` on rows with id <= ``up_to_id``.

        Returns the number of rows updated. Mirrors the GPKG semantics.
        """
        if self._detector is None:
            raise RuntimeError(
                "DuckDBSpatialEngine is not open. Call .open() first."
            )
        # DuckDB doesn't expose a rowcount on ``execute()`` for UPDATE
        # in all client versions; use a SELECT-then-UPDATE so we can
        # report the row count deterministically.
        # Cursor-per-call: same rationale as get_pending_changes —
        # called from the watcher's daemon thread, must not collide
        # with main-thread ``execute()`` proxies on the shared conn.
        with self._lock:
            cur = self.conn.cursor()
            count_rows = cur.execute(
                "SELECT COUNT(*) FROM _change_log "
                "WHERE id <= ? AND processed = 0",
                [int(up_to_id)],
            ).fetchall()
            n = int(count_rows[0][0]) if count_rows else 0
            if n > 0:
                cur.execute(
                    "UPDATE _change_log SET processed = 1 WHERE id <= ?",
                    [int(up_to_id)],
                )
        return n

    # ------------------------------------------------------------------
    # DML proxy
    # ------------------------------------------------------------------

    def execute(self, sql: str, params: Any = None) -> Any:
        """Execute SQL through the change detector.

        This is the **only** entry point that captures writes for the
        change log. Code calling ``engine.conn.execute(...)`` directly
        bypasses the detector — see the limitations section in the
        module docstring.
        """
        if self._detector is None:
            raise RuntimeError(
                "DuckDBSpatialEngine is not open. Call .open() first."
            )
        with self._lock:
            return self._detector.execute(sql, params)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def backend_name(self) -> str:
        # Keep "duckdb" so downstream consumers that branch on the
        # backend name (e.g. tier gating, /health) continue to work.
        return "duckdb"


__all__ = ["DuckDBSpatialEngine"]
