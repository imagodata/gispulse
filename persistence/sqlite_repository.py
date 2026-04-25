"""
SQLite-backed repository for GISPulse domain objects.

Persists rules, jobs, datasets, and other core types to a local SQLite
database so that state survives server restarts.

Default location: ``~/.gispulse/gispulse.db``
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, fields as dc_fields
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

from core.models import (
    Artifact,
    Dataset,
    Job,
    JobStatus,
    Layer,
    Project,
    RefLayerDef,
    Rule,
    Scenario,
    TableRelation,
    Trigger,
)
from persistence.repository import Repository
from persistence.schema import (
    BOOL_COLUMNS as _BOOL_COLUMNS,
    DATETIME_COLUMNS as _DATETIME_COLUMNS,
    JSON_COLUMNS as _JSON_COLUMNS,
    SCHEMA_VERSION,
    UUID_COLUMNS as _UUID_COLUMNS,
    build_table_schemas,
)

T = TypeVar(
    "T",
    Dataset,
    Layer,
    Job,
    Artifact,
    Rule,
    Trigger,
    Scenario,
    Project,
    TableRelation,
    RefLayerDef,
)

# Default database path
DEFAULT_DB_DIR = Path.home() / ".gispulse"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "gispulse.db"

# Table schemas generated from unified definitions (no prefix for SQLite)
_TABLE_SCHEMAS = build_table_schemas(prefix="")


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _serialise_value(key: str, value: Any) -> Any:
    """Convert a Python value to a SQLite-friendly representation."""
    if value is None:
        return None
    # Handle enums first (JobStatus, TriggerEvent, etc.)
    if hasattr(value, "value"):
        value = value.value
    if key in _JSON_COLUMNS:
        return json.dumps(value, default=str)
    if key in _DATETIME_COLUMNS:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)
    if key in _UUID_COLUMNS:
        return str(value)
    if key in _BOOL_COLUMNS:
        return int(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return value


def _deserialise_value(key: str, value: Any, field_type: Any = None) -> Any:
    """Convert a SQLite value back to the Python domain type."""
    if value is None:
        return None
    if key in _JSON_COLUMNS:
        return json.loads(value) if isinstance(value, str) else value
    if key in _DATETIME_COLUMNS:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value
    if key in _UUID_COLUMNS:
        return UUID(value) if isinstance(value, str) else value
    if key in _BOOL_COLUMNS:
        return bool(value)
    return value


# ---------------------------------------------------------------------------
# Model factory registry
# ---------------------------------------------------------------------------

_MODEL_TABLE: dict[type, str] = {
    Rule: "rules",
    Job: "jobs",
    Dataset: "datasets",
    Scenario: "scenarios",
    Project: "projects",
    Trigger: "triggers",
    TableRelation: "table_relations",
    RefLayerDef: "ref_layers",
}


def _row_to_model(model_cls: type, row: dict[str, Any]) -> Any:
    """Reconstruct a dataclass instance from a SQLite row dict."""
    kwargs: dict[str, Any] = {}
    valid_fields = {f.name for f in dc_fields(model_cls)}

    for key, value in row.items():
        field_name = _COLUMN_TO_FIELD.get(key, key)
        if field_name not in valid_fields:
            continue
        kwargs[field_name] = _deserialise_value(key, value)

    # Special handling for Job.status enum
    if model_cls is Job and "status" in kwargs:
        raw = kwargs["status"]
        if isinstance(raw, str):
            kwargs["status"] = JobStatus(raw)

    return model_cls(**kwargs)


# Field name → column name mapping (when they differ)
_FIELD_TO_COLUMN = {"order": "order_idx"}
_COLUMN_TO_FIELD = {v: k for k, v in _FIELD_TO_COLUMN.items()}


def _model_to_row(obj: Any) -> dict[str, Any]:
    """Convert a dataclass instance to a dict suitable for SQLite INSERT."""
    raw = asdict(obj)
    result: dict[str, Any] = {}
    for k, v in raw.items():
        col = _FIELD_TO_COLUMN.get(k, k)
        result[col] = _serialise_value(col, v)
    return result


# ---------------------------------------------------------------------------
# SQLiteRepository
# ---------------------------------------------------------------------------


class SQLiteRepository(Repository[T]):
    """SQLite-backed repository for a single domain model type.

    Usage::

        repo = SQLiteRepository(Rule, db_path="~/.gispulse/gispulse.db")
        repo.save(rule)
        all_rules = repo.list_all()
    """

    def __init__(
        self,
        model_cls: type,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        self._model_cls = model_cls
        self._table = _MODEL_TABLE[model_cls]
        self._db_path = Path(db_path)
        self._lock = threading.Lock()

        # Ensure directory exists
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Persistent connection (thread-safe via lock + WAL mode)
        self._conn = self._create_connection()

        # Create table + run migrations
        self._execute(_TABLE_SCHEMAS[self._table])
        self._migrate()

    # ------------------------------------------------------------------
    # Schema migration
    # ------------------------------------------------------------------

    def _migrate(self) -> None:
        """Add missing columns to existing tables (non-destructive)."""
        # Get existing columns
        rows = self._execute(f"PRAGMA table_info({self._table})")
        existing_cols = {row["name"] for row in rows}

        table_migrations: dict[str, list[tuple[str, str]]] = {
            "triggers": [
                ("description", "TEXT DEFAULT ''"),
                ("category", "TEXT DEFAULT 'data'"),
                ("severity", "TEXT DEFAULT 'info'"),
                ("auto_eval", "INTEGER DEFAULT 0"),
            ],
            "jobs": [
                ("attempts", "INTEGER DEFAULT 0"),
                ("max_retries", "INTEGER DEFAULT 3"),
            ],
            "rules": [
                ("scope_target_id", "TEXT"),
                ("order_idx", "INTEGER DEFAULT 0"),
            ],
        }

        for col_name, col_def in table_migrations.get(self._table, []):
            if col_name not in existing_cols:
                try:
                    self._execute(f"ALTER TABLE {self._table} ADD COLUMN {col_name} {col_def}")
                except sqlite3.OperationalError as exc:
                    # Only ignore "duplicate column" errors from concurrent migrations
                    if "duplicate column" in str(exc).lower():
                        pass
                    else:
                        raise

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _execute(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                cur = self._conn.execute(sql, params)
                rows = cur.fetchall()
                self._conn.commit()
                return rows
            except sqlite3.ProgrammingError:
                # Connection was closed unexpectedly — recreate
                self._conn = self._create_connection()
                cur = self._conn.execute(sql, params)
                rows = cur.fetchall()
                self._conn.commit()
                return rows

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(self, obj: T) -> T:
        row = _model_to_row(obj)
        columns = list(row.keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        updates = ", ".join(f"{c} = ?" for c in columns)
        values = list(row.values())

        sql = (
            f"INSERT INTO {self._table} ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        self._execute(sql, tuple(values + values))
        return obj

    def get(self, obj_id: UUID) -> T | None:
        rows = self._execute(
            f"SELECT * FROM {self._table} WHERE id = ?",
            (str(obj_id),),
        )
        if not rows:
            return None
        return _row_to_model(self._model_cls, dict(rows[0]))  # type: ignore[return-value]

    def list_all(self, *, limit: int | None = None, offset: int = 0) -> list[T]:
        sql = f"SELECT * FROM {self._table}"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = (limit, offset)
        rows = self._execute(sql, params)
        return [
            _row_to_model(self._model_cls, dict(r))  # type: ignore[misc]
            for r in rows
        ]

    def count(self) -> int:
        rows = self._execute(f"SELECT COUNT(*) AS c FROM {self._table}")
        return rows[0]["c"] if rows else 0

    def delete(self, obj_id: UUID) -> bool:
        rows_before = self._execute(
            f"SELECT COUNT(*) as c FROM {self._table} WHERE id = ?",
            (str(obj_id),),
        )
        count = rows_before[0]["c"] if rows_before else 0
        if count == 0:
            return False
        self._execute(
            f"DELETE FROM {self._table} WHERE id = ?",
            (str(obj_id),),
        )
        return True

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def clear(self) -> None:
        self._execute(f"DELETE FROM {self._table}")

    def __iter__(self):
        return iter(self.list_all())

    def __len__(self) -> int:
        return self.count()
