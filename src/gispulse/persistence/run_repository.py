"""SQLite-backed repository for PipelineRun objects.

Follows the exact same pattern as ScheduleRepository: one table,
WAL mode, threading.Lock, idempotent CREATE TABLE, JSON steps column.

Default DB path: same as other repos (~/.gispulse/gispulse.db).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import UUID

from gispulse.core.enums import JobStatus
from gispulse.core.logging import get_logger
from gispulse.core.run_models import PipelineRun, PipelineRunStep
from gispulse.persistence.sqlite_repository import DEFAULT_DB_PATH

log = get_logger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT '',
    spec_ref TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    error TEXT NOT NULL DEFAULT '',
    steps TEXT NOT NULL DEFAULT '[]'
)
"""


class RunRepository:
    """SQLite-backed CRUD for PipelineRun objects.

    Thread-safe via threading.Lock (same approach as ScheduleRepository).
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
    def _steps_to_json(steps: list[PipelineRunStep]) -> str:
        return json.dumps(
            [
                {
                    "step_id": s.step_id,
                    "status": s.status.value,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                    "attempt": s.attempt,
                    "error": s.error,
                }
                for s in steps
            ]
        )

    @staticmethod
    def _steps_from_json(raw: str) -> list[PipelineRunStep]:
        data = json.loads(raw or "[]")
        steps = []
        for d in data:
            steps.append(
                PipelineRunStep(
                    step_id=d["step_id"],
                    status=JobStatus(d["status"]),
                    started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else datetime.now(),
                    ended_at=datetime.fromisoformat(d["ended_at"]) if d.get("ended_at") else None,
                    attempt=d.get("attempt", 1),
                    error=d.get("error", ""),
                )
            )
        return steps

    @staticmethod
    def _to_row(run: PipelineRun) -> dict:
        return {
            "run_id": str(run.run_id),
            "source": run.source,
            "spec_ref": run.spec_ref,
            "scope": run.scope,
            "status": run.status.value,
            "started_at": run.started_at.isoformat(),
            "ended_at": run.ended_at.isoformat() if run.ended_at else None,
            "error": run.error,
            "steps": RunRepository._steps_to_json(run.steps),
        }

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> PipelineRun:
        d = dict(row)
        run = PipelineRun(
            source=d["source"],
            spec_ref=d["spec_ref"],
            scope=d.get("scope", ""),
            status=JobStatus(d["status"]),
        )
        run.run_id = UUID(d["run_id"])
        run.started_at = datetime.fromisoformat(d["started_at"])
        run.ended_at = datetime.fromisoformat(d["ended_at"]) if d.get("ended_at") else None
        run.error = d.get("error", "")
        run.steps = cls._steps_from_json(d.get("steps", "[]"))
        return run

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(self, run: PipelineRun) -> PipelineRun:
        """Insert or update (upsert) a pipeline run."""
        row = self._to_row(run)
        columns = list(row.keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        updates = ", ".join(f"{c} = ?" for c in columns)
        values = list(row.values())
        sql = (
            f"INSERT INTO pipeline_runs ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(run_id) DO UPDATE SET {updates}"
        )
        self._execute(sql, tuple(values + values))
        return run

    def get(self, run_id: UUID) -> PipelineRun | None:
        """Return a run by UUID, or None."""
        rows = self._execute(
            "SELECT * FROM pipeline_runs WHERE run_id = ?",
            (str(run_id),),
        )
        if not rows:
            return None
        return self._from_row(rows[0])

    def list_all(self, limit: int = 100, offset: int = 0) -> list[PipelineRun]:
        """Return runs ordered by started_at DESC."""
        rows = self._execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._from_row(r) for r in rows]

    def count(self) -> int:
        rows = self._execute("SELECT COUNT(*) as c FROM pipeline_runs")
        return rows[0]["c"] if rows else 0

    def clear(self) -> None:
        self._execute("DELETE FROM pipeline_runs")

    def recover_stale_runs(self, event_sink=None) -> int:
        """Mark RUNNING runs as FAILED with error="orphaned by worker restart".

        Called at startup to close any run left in RUNNING state by a prior
        crash. Returns the number of runs updated.

        Args:
            event_sink: Optional RunEventSink to emit ``run.failed`` for each
                        recovered run (so WebSocket clients are notified on reconnect).
        """
        from datetime import timezone

        rows = self._execute(
            "SELECT * FROM pipeline_runs WHERE status = ?",
            (JobStatus.RUNNING.value,),
        )
        if not rows:
            return 0

        recovered = 0
        for row in rows:
            run = self._from_row(row)
            run.status = JobStatus.FAILED
            run.error = "orphaned by worker restart"
            run.ended_at = datetime.now(timezone.utc)
            self.save(run)
            if event_sink is not None:
                event_sink.emit("run.failed", {
                    "run_id": str(run.run_id),
                    "job_id": "",
                    "status": "failed",
                    "ended_at": run.ended_at.isoformat(),
                    "error": "orphaned by worker restart",
                })
            recovered += 1
            log.info("run_orphan_recovered", run_id=str(run.run_id))

        return recovered
