"""
DuckDB Change Detector — application-level change detection for DuckDB.

DuckDB has no native triggers. This module emulates change detection by:
1. Wrapping DuckDB SQL execution to intercept DML statements
2. Recording changes in a _change_log table (same schema as SpatiaLite)
3. Polling the change log to produce ChangeRecord objects
4. Feeding them to TriggerEvaluator for evaluation

Architecture::

    DuckDBSession.execute_sql(sql)
        → DuckDBChangeDetector.execute_sql(sql)  # proxy
            → detect DML (INSERT/UPDATE/DELETE)
            → execute original SQL
            → INSERT INTO _change_log(table_name, operation, row_pk)
        → polling thread (configurable interval)
            → _process_pending_changes()
            → TriggerEvaluator.evaluate(record, triggers)
            → FiredTrigger accumulated

Constraints:
    - Only works if ALL writes go through the proxy
    - External DuckDB connections bypass detection
    - DuckDB in-memory mode: change log is lost on session close
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

from gispulse.core.logging import get_logger
from gispulse.core.models import ChangeOperation, ChangeRecord, FiredTrigger, Trigger

log = get_logger(__name__)

# Regex to detect DML statements and extract table name
_INSERT_RE = re.compile(
    r"^\s*INSERT\s+INTO\s+[\"']?(\w+)[\"']?", re.IGNORECASE
)
_UPDATE_RE = re.compile(
    r"^\s*UPDATE\s+[\"']?(\w+)[\"']?", re.IGNORECASE
)
_DELETE_RE = re.compile(
    r"^\s*DELETE\s+FROM\s+[\"']?(\w+)[\"']?", re.IGNORECASE
)

_SQL_CREATE_CHANGE_LOG = """
CREATE TABLE IF NOT EXISTS _change_log (
    id        INTEGER PRIMARY KEY,
    table_name TEXT    NOT NULL,
    operation  TEXT    NOT NULL,
    row_pk     TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed  INTEGER DEFAULT 0
)
"""

_SQL_CREATE_SEQUENCE = """
CREATE SEQUENCE IF NOT EXISTS _change_log_seq START 1
"""


class DuckDBChangeDetector:
    """Application-level change detection for DuckDB sessions.

    Wraps a DuckDB connection to intercept DML and log changes.

    Usage::

        from gispulse.persistence.duckdb_engine import DuckDBSession
        from gispulse.rules.trigger_evaluator import TriggerEvaluator

        session = DuckDBSession()
        session.open()

        detector = DuckDBChangeDetector(session.conn)
        detector.start_polling(triggers=[...])

        # Use detector.execute() instead of session.conn.execute() for writes
        detector.execute("INSERT INTO parcels VALUES (...)")

        # Changes are detected and triggers evaluated automatically
        fired = detector.fired_triggers

        detector.stop_polling()
    """

    def __init__(self, conn: Any) -> None:
        """
        Args:
            conn: An open DuckDB connection (duckdb.DuckDBPyConnection).
        """
        self._conn = conn
        self._polling = False
        self._poll_thread: threading.Thread | None = None
        self._poll_interval: float = 0.1
        self._triggers: list[Trigger] = []
        self._fired: list[FiredTrigger] = []
        self._change_records: list[ChangeRecord] = []
        self._evaluator: Any | None = None
        self._setup_change_log()

    def _setup_change_log(self) -> None:
        """Create the _change_log table and sequence in DuckDB."""
        self._conn.execute(_SQL_CREATE_SEQUENCE)
        self._conn.execute(_SQL_CREATE_CHANGE_LOG)

    # ------------------------------------------------------------------
    # Write proxy
    # ------------------------------------------------------------------

    def execute(self, sql: str, params: Any = None) -> Any:
        """Execute SQL, intercepting DML to log changes.

        Args:
            sql: SQL statement to execute.
            params: Optional parameters for the statement.

        Returns:
            The DuckDB result object.

        Thread-safety
        -------------
        ``DuckDBPyConnection.execute`` binds its result set to the
        connection object — a second ``execute`` on the same conn
        (even from the same thread) invalidates the prior result, and
        cross-thread access can corrupt the connection's internal
        state. The lifespan-bound watcher polls
        :meth:`DuckDBSpatialEngine.get_pending_changes` (which uses
        ``conn.cursor()``) from a daemon thread while
        :class:`DuckDBSpatialEngine.execute` (the proxy) is called
        from the main / HTTP-worker threads. We use a thread-local
        ``cursor()`` here too so the original statement and the
        ``_change_log`` insert share an isolated result handle that
        won't be clobbered by the watcher's polling cursor.
        """
        cur = self._conn.cursor()
        # Execute the original statement
        if params is not None:
            result = cur.execute(sql, params)
        else:
            result = cur.execute(sql)

        # Detect DML and log the change
        dml_info = self._detect_dml(sql)
        if dml_info:
            table_name, operation = dml_info
            # Re-use the same cursor — DuckDB handles sequential
            # ``execute`` calls on a cursor without invalidating the
            # prior result up to the next fetch (and we don't fetch
            # the original DML's result here).
            self._log_change(table_name, operation, cursor=cur)

        return result

    def _detect_dml(self, sql: str) -> tuple[str, str] | None:
        """Detect if a SQL statement is DML and extract table + operation.

        Returns:
            Tuple of (table_name, operation) or None if not DML.
        """
        m = _INSERT_RE.match(sql)
        if m:
            return m.group(1), "INSERT"

        m = _UPDATE_RE.match(sql)
        if m:
            return m.group(1), "UPDATE"

        m = _DELETE_RE.match(sql)
        if m:
            return m.group(1), "DELETE"

        return None

    def _log_change(
        self, table_name: str, operation: str, cursor: Any | None = None
    ) -> None:
        """Insert a change record into _change_log.

        Args:
            table_name: The DML target table.
            operation:  ``INSERT`` / ``UPDATE`` / ``DELETE``.
            cursor:     Optional thread-local cursor reused by
                ``execute()`` so the change-log insert is co-located
                with the DML on the same handle. When *None* (legacy
                callers / start_polling path), a fresh cursor is
                created so the write doesn't race with concurrent
                reads on the bare connection.
        """
        target = cursor if cursor is not None else self._conn.cursor()
        target.execute(
            "INSERT INTO _change_log(id, table_name, operation) "
            "VALUES (nextval('_change_log_seq'), ?, ?)",
            [table_name, operation],
        )

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def start_polling(
        self,
        triggers: list[Trigger],
        interval: float = 0.1,
        session_id: str = "",
        evaluator: Any | None = None,
    ) -> None:
        """Start the change detection polling thread.

        Args:
            triggers: Triggers to evaluate on each change.
            interval: Polling interval in seconds (default 100ms).
            session_id: Session ID injected into ChangeRecords.
            evaluator: TriggerEvaluator instance. Auto-created if None.
        """
        if self._polling:
            return
        self._triggers = triggers
        self._poll_interval = interval
        self._evaluator = evaluator
        self._polling = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            args=(session_id,),
            daemon=True,
        )
        self._poll_thread.start()

    def stop_polling(self) -> None:
        """Stop the polling thread."""
        self._polling = False
        if self._poll_thread:
            self._poll_thread.join(timeout=1.0)
            self._poll_thread = None

    def _poll_loop(self, session_id: str) -> None:
        """Internal polling loop — runs in a daemon thread."""
        while self._polling:
            self._process_pending_changes(session_id)
            time.sleep(self._poll_interval)

    def _process_pending_changes(self, session_id: str) -> list[FiredTrigger]:
        """Read _change_log, evaluate triggers, mark rows processed.

        Returns:
            List of FiredTrigger generated in this cycle.
        """
        # Thread-local cursor: this method runs on the polling daemon
        # thread spawned by ``start_polling``. See :meth:`execute` for
        # the cross-thread rationale.
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT id, table_name, operation, row_pk "
            "FROM _change_log WHERE processed = 0 ORDER BY id"
        ).fetchall()

        if not rows:
            return []

        fired: list[FiredTrigger] = []
        ids_to_mark: list[int] = []

        for row in rows:
            change_id, table_name, operation_str = row[0], row[1], row[2]
            row_pk = row[3] if len(row) > 3 else None

            try:
                operation = ChangeOperation(operation_str.upper())
            except ValueError:
                operation = ChangeOperation.INSERT

            record = ChangeRecord(
                session_id=session_id,
                table_name=table_name,
                operation=operation,
                feature_id=str(row_pk) if row_pk is not None else None,
            )

            self._change_records.append(record)

            if self._triggers:
                evaluator = self._evaluator
                if evaluator is None:
                    from gispulse.rules.trigger_evaluator import TriggerEvaluator
                    evaluator = TriggerEvaluator()
                    self._evaluator = evaluator
                fired.extend(evaluator.evaluate(record, self._triggers))

            ids_to_mark.append(change_id)

        if ids_to_mark:
            placeholders = ",".join(str(i) for i in ids_to_mark)
            cur.execute(
                f"UPDATE _change_log SET processed = 1 WHERE id IN ({placeholders})"
            )

        self._fired.extend(fired)
        return fired

    # ------------------------------------------------------------------
    # Result registries
    # ------------------------------------------------------------------

    @property
    def fired_triggers(self) -> list[FiredTrigger]:
        """All FiredTrigger accumulated since start."""
        return list(self._fired)

    @property
    def change_records(self) -> list[ChangeRecord]:
        """All ChangeRecords accumulated since start."""
        return list(self._change_records)

    def clear_fired(self) -> None:
        """Clear the fired triggers registry."""
        self._fired.clear()

    def clear_change_records(self) -> None:
        """Clear the change records registry."""
        self._change_records.clear()
