"""Centralized SQLite/GPKG connection helper with WAL + busy_timeout pragmas.

Closes part of #141 (Q2 of EPIC #139): every code path that opens a
GeoPackage via raw ``sqlite3`` must apply the same concurrency-safety
pragmas, otherwise concurrent QGIS edits race the GISPulse runtime and
trigger ``SQLITE_BUSY`` (or the Py3.10 ``test_p02`` flake).

Use :func:`connect_gpkg` instead of bare ``sqlite3.connect`` when the
target is a GeoPackage.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_BUSY_TIMEOUT_MS = 5000


def connect_gpkg(
    path: str | Path,
    *,
    isolation_level: str | None = None,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    force_wal: bool = True,
    row_factory: Any = None,
    timeout: float = 5.0,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Open a SQLite connection on a GPKG with safe defaults.

    Defaults match :func:`gispulse.cli_track._open_gpkg`:

    * ``timeout=5.0`` so the driver waits for the file lock instead of
      raising ``OperationalError`` immediately.
    * ``isolation_level=None`` (autocommit) — the engine and CLI both
      manage their own transactions explicitly.
    * ``PRAGMA busy_timeout = 5000`` so individual statements wait for
      a competing writer (e.g. QGIS) instead of failing fast.
    * ``PRAGMA journal_mode = WAL`` so concurrent readers do not block
      a writer (QGIS holds an exclusive lock under the default
      ``DELETE`` journal mode).

    The WAL pragma is best-effort: on a read-only mount or if the
    GPKG is opened over a filesystem that disallows shared-memory
    files (some Windows network shares), the pragma silently fails
    and the connection falls back to whatever the file currently
    declares. Pass ``force_wal=False`` if the caller knows the GPKG
    is read-only.
    """
    conn = sqlite3.connect(
        str(path),
        timeout=timeout,
        isolation_level=isolation_level,
        check_same_thread=check_same_thread,
    )
    if row_factory is not None:
        conn.row_factory = row_factory
    if force_wal:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    except sqlite3.OperationalError:
        pass
    return conn
