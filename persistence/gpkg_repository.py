"""
GPKG-backed repository — stores domain objects in _gispulse_* tables.

Drop-in replacement for SQLiteRepository that writes to a GPKG project file
instead of a separate ``~/.gispulse/gispulse.db``.  Reuses the same
serialisation logic.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, fields as dc_fields
from datetime import datetime
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
from persistence.gpkg_schema import MODEL_TABLE_MAPPING
from persistence.repository import Repository
from persistence.sqlite_repository import (
    _BOOL_COLUMNS,
    _DATETIME_COLUMNS,
    _JSON_COLUMNS,
    _UUID_COLUMNS,
    _deserialise_value,
    _model_to_row,
    _row_to_model,
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

# Domain model → GPKG table name
_MODEL_TABLE: dict[type, str] = {
    Rule: "_gispulse_rules",
    Job: "_gispulse_jobs",
    Dataset: "_gispulse_datasets",
    Layer: "_gispulse_layers",
    Scenario: "_gispulse_scenarios",
    Project: "_gispulse_projects",
    Trigger: "_gispulse_triggers",
    TableRelation: "_gispulse_table_relations",
    RefLayerDef: "_gispulse_ref_layers",
}


class GpkgRepository(Repository[T]):
    """Repository backed by _gispulse_* tables inside a GPKG project file.

    Uses the same SQLite connection as the GeoPackageEngine, sharing the
    WAL journal and threading lock.

    Usage::

        engine = GeoPackageEngine("project.gpkg")
        engine.open()
        repo = GpkgRepository(Rule, engine)
        repo.save(rule)
        all_rules = repo.list_all()
    """

    def __init__(
        self,
        model_cls: type,
        conn: sqlite3.Connection,
        lock: threading.Lock | None = None,
    ) -> None:
        self._model_cls = model_cls
        self._table = _MODEL_TABLE[model_cls]
        self._conn = conn
        self._lock = lock or threading.Lock()

    @classmethod
    def from_engine(cls, model_cls: type, engine: Any) -> "GpkgRepository":
        """Create a repository from a GeoPackageEngine instance."""
        return cls(
            model_cls,
            conn=engine._get_conn(),
            lock=engine._lock,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
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
        return _row_to_model(self._model_cls, dict(rows[0]))

    def list_all(self) -> list[T]:
        rows = self._execute(f"SELECT * FROM {self._table}")
        return [_row_to_model(self._model_cls, dict(r)) for r in rows]

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

    def count(self) -> int:
        rows = self._execute(f"SELECT COUNT(*) as c FROM {self._table}")
        return rows[0]["c"] if rows else 0

    def clear(self) -> None:
        self._execute(f"DELETE FROM {self._table}")

    def __iter__(self):
        return iter(self.list_all())

    def __len__(self) -> int:
        return self.count()
