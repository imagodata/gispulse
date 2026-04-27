"""
SQLite-backed audit logger for GISPulse Pro.

Write-optimized audit trail using WAL mode. Logs mutating API actions
(POST, PUT, PATCH, DELETE) with user context, IP, and user-agent.

Enabled via ``GISPULSE_AUDIT=true``.  Tier-gated to Pro.

Retention is configurable via ``GISPULSE_AUDIT_RETENTION_DAYS`` (default: 90).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from persistence.sqlite_repository import DEFAULT_DB_PATH

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class AuditEntry:
    """Immutable record of a single auditable action."""

    action: str  # e.g. "dataset.upload", "job.run"
    resource_type: str  # e.g. "dataset", "job", "rule"
    ip_address: str
    user_agent: str
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: str | None = None
    resource_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    status_code: int = 0


@dataclass
class AuditQuery:
    """Filter criteria for querying audit logs."""

    user_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = 100
    offset: int = 0


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------

_AUDIT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    user_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    details TEXT DEFAULT '{}',
    ip_address TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    status_code INTEGER DEFAULT 0
)
"""

_AUDIT_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_log(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)",
    "CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id)",
]


class AuditLogger:
    """Write-optimized audit trail backed by SQLite WAL.

    Thread-safe via a threading.Lock.  Uses the same database file as
    the main repository by default, with a dedicated ``audit_log`` table.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._create_connection()
        self._ensure_table()

    # ------------------------------------------------------------------
    # Connection / low-level
    # ------------------------------------------------------------------

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _execute(
        self,
        sql: str,
        params: tuple = (),
    ) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            self._conn.commit()
            return rows

    def _ensure_table(self) -> None:
        with self._lock:
            self._conn.execute(_AUDIT_TABLE_SQL)
            for idx_sql in _AUDIT_INDEXES_SQL:
                self._conn.execute(idx_sql)
            self._conn.commit()

    def close(self) -> None:
        """Close the persistent connection."""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log(self, entry: AuditEntry) -> None:
        """Persist an audit entry.  Synchronous — suitable for post-response hooks."""
        self._execute(
            """INSERT INTO audit_log
               (id, timestamp, user_id, action, resource_type, resource_id,
                details, ip_address, user_agent, status_code)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.timestamp.isoformat(),
                entry.user_id,
                entry.action,
                entry.resource_type,
                entry.resource_id,
                json.dumps(entry.details, default=str),
                entry.ip_address,
                entry.user_agent,
                entry.status_code,
            ),
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def query(self, filters: AuditQuery) -> list[AuditEntry]:
        """Return audit entries matching *filters*, ordered by timestamp DESC."""
        sql, params = self._build_query("SELECT *", filters)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params += (filters.limit, filters.offset)

        rows = self._execute(sql, params)
        return [self._row_to_entry(dict(r)) for r in rows]

    def count(self, filters: AuditQuery) -> int:
        """Return the count of entries matching *filters*."""
        sql, params = self._build_query("SELECT COUNT(*) AS c", filters)
        rows = self._execute(sql, params)
        return rows[0]["c"] if rows else 0

    def _build_query(
        self,
        select_clause: str,
        filters: AuditQuery,
    ) -> tuple[str, tuple]:
        clauses: list[str] = []
        params: list[Any] = []

        if filters.user_id is not None:
            clauses.append("user_id = ?")
            params.append(filters.user_id)
        if filters.action is not None:
            clauses.append("action = ?")
            params.append(filters.action)
        if filters.resource_type is not None:
            clauses.append("resource_type = ?")
            params.append(filters.resource_type)
        if filters.resource_id is not None:
            clauses.append("resource_id = ?")
            params.append(filters.resource_id)
        if filters.date_from is not None:
            clauses.append("timestamp >= ?")
            params.append(filters.date_from.isoformat())
        if filters.date_to is not None:
            clauses.append("timestamp <= ?")
            params.append(filters.date_to.isoformat())

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return f"{select_clause} FROM audit_log{where}", tuple(params)

    # ------------------------------------------------------------------
    # Retention / cleanup
    # ------------------------------------------------------------------

    def cleanup(self, older_than: timedelta) -> int:
        """Delete entries older than *older_than*.  Returns deleted count."""
        cutoff = (datetime.now(timezone.utc) - older_than).isoformat()
        rows = self._execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE timestamp < ?",
            (cutoff,),
        )
        count = rows[0]["c"] if rows else 0
        if count > 0:
            self._execute("DELETE FROM audit_log WHERE timestamp < ?", (cutoff,))
        return count

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: dict) -> AuditEntry:
        details_raw = row.get("details", "{}")
        details = json.loads(details_raw) if isinstance(details_raw, str) else details_raw

        return AuditEntry(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            user_id=row.get("user_id"),
            action=row["action"],
            resource_type=row["resource_type"],
            resource_id=row.get("resource_id"),
            details=details,
            ip_address=row["ip_address"],
            user_agent=row["user_agent"],
            status_code=row.get("status_code", 0),
        )
