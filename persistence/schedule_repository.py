"""
SQLite-backed repository for scheduled pipelines.

Stores cron schedules in a dedicated ``scheduled_pipelines`` table within
the GISPulse SQLite database.  Follows the same patterns as
:class:`SQLiteRepository` but is specialised for the
:class:`ScheduledPipeline` dataclass (not a core domain model, so it
doesn't use the generic model registry).

Default location: ``~/.gispulse/gispulse.db`` (same DB as other repos).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from core.logging import get_logger
from orchestration.scheduler import ScheduledPipeline
from persistence.sqlite_repository import DEFAULT_DB_PATH

log = get_logger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_pipelines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    cron_expression TEXT NOT NULL DEFAULT '0 * * * *',
    pipeline_config TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    next_run TEXT,
    created_by TEXT
)
"""


class ScheduleRepository:
    """SQLite-backed CRUD repository for ScheduledPipeline objects.

    Thread-safe via a threading.Lock (same approach as SQLiteRepository).
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._execute(_CREATE_TABLE_SQL)

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _execute(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(sql, params)
                rows = cur.fetchall()
                conn.commit()
                return rows
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    @staticmethod
    def _to_row(sp: ScheduledPipeline) -> dict[str, Any]:
        return {
            "id": str(sp.id),
            "name": sp.name,
            "cron_expression": sp.cron_expression,
            "pipeline_config": json.dumps(sp.pipeline_config, default=str),
            "enabled": int(sp.enabled),
            "last_run": sp.last_run.isoformat() if sp.last_run else None,
            "next_run": sp.next_run.isoformat() if sp.next_run else None,
            "created_by": sp.created_by,
        }

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ScheduledPipeline:
        d = dict(row)
        return ScheduledPipeline(
            id=UUID(d["id"]),
            name=d.get("name", ""),
            cron_expression=d.get("cron_expression", "0 * * * *"),
            pipeline_config=json.loads(d.get("pipeline_config", "{}") or "{}"),
            enabled=bool(d.get("enabled", 1)),
            last_run=(
                datetime.fromisoformat(d["last_run"]) if d.get("last_run") else None
            ),
            next_run=(
                datetime.fromisoformat(d["next_run"]) if d.get("next_run") else None
            ),
            created_by=d.get("created_by"),
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(self, sp: ScheduledPipeline) -> ScheduledPipeline:
        """Insert or update a scheduled pipeline (upsert)."""
        row = self._to_row(sp)
        columns = list(row.keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        updates = ", ".join(f"{c} = ?" for c in columns)
        values = list(row.values())

        sql = (
            f"INSERT INTO scheduled_pipelines ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        self._execute(sql, tuple(values + values))
        return sp

    def get(self, schedule_id: UUID) -> ScheduledPipeline | None:
        """Return a single schedule by UUID, or None."""
        rows = self._execute(
            "SELECT * FROM scheduled_pipelines WHERE id = ?",
            (str(schedule_id),),
        )
        if not rows:
            return None
        return self._from_row(rows[0])

    def list_all(self) -> list[ScheduledPipeline]:
        """Return all scheduled pipelines."""
        rows = self._execute("SELECT * FROM scheduled_pipelines")
        return [self._from_row(r) for r in rows]

    def list_enabled(self) -> list[ScheduledPipeline]:
        """Return only enabled schedules."""
        rows = self._execute(
            "SELECT * FROM scheduled_pipelines WHERE enabled = 1"
        )
        return [self._from_row(r) for r in rows]

    def delete(self, schedule_id: UUID) -> bool:
        """Delete a schedule by UUID. Returns True if a row was deleted."""
        rows_before = self._execute(
            "SELECT COUNT(*) as c FROM scheduled_pipelines WHERE id = ?",
            (str(schedule_id),),
        )
        count = rows_before[0]["c"] if rows_before else 0
        if count == 0:
            return False
        self._execute(
            "DELETE FROM scheduled_pipelines WHERE id = ?",
            (str(schedule_id),),
        )
        return True

    def count(self) -> int:
        """Return the total number of schedules."""
        rows = self._execute("SELECT COUNT(*) as c FROM scheduled_pipelines")
        return rows[0]["c"] if rows else 0

    def clear(self) -> None:
        """Delete all schedules (test helper)."""
        self._execute("DELETE FROM scheduled_pipelines")
